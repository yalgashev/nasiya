"""Tenant disclosure discovery and forward-lock authority boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.customer.repository import lock_existing_own_customer_for_update
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import (
    _LockedActorShopStaff,
    _LockedShop,
    get_shop_staff_access,
    lock_actor_shop_staff_for_update,
    lock_shop_for_update,
)
from app.shop.values import ShopId, UserId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import (
    _LockedShopCustomer,
    lock_shop_customer_by_tenant_locator,
)
from app.shop_customer.values import CustomerId, ShopCustomerId

__all__ = (
    "DetachedDisclosureActorContext",
    "LockedTenantDisclosureTarget",
    "TenantDisclosureTargetResult",
    "discover_tenant_disclosure_target",
    "lock_tenant_disclosure_target",
    "recheck_historical_disclosure_authority",
    "validate_locked_tenant_disclosure_target",
)

_DISCLOSURE_ROLES = frozenset({ShopRole.OWNER, ShopRole.MANAGER, ShopRole.CASHIER})


@dataclass(frozen=True, slots=True, repr=False)
class DetachedDisclosureActorContext:
    """Scalar-only TX-A handoff; role is a hint and is revalidated in TX-B."""

    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)
    role_hint: ShopRole

    def __post_init__(self) -> None:
        if not isinstance(self.actor_user_id, UUID) or not isinstance(
            self.current_shop_id, UUID
        ):
            raise ValueError("Disclosure actor context is invalid")
        if not isinstance(self.role_hint, ShopRole):
            raise ValueError("Disclosure actor role hint is invalid")

    def __repr__(self) -> str:
        return "DetachedDisclosureActorContext(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _DiscoveredTenantDisclosureTarget:
    shop_customer_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    target_user_id: UUID = field(repr=False)

    def __repr__(self) -> str:
        return "_DiscoveredTenantDisclosureTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedTenantDisclosureTarget:
    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)
    customer_id: CustomerId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    role: ShopRole
    locked_shop: _LockedShop = field(repr=False)
    locked_staff: _LockedActorShopStaff = field(repr=False)
    actor: User = field(repr=False)
    customer: Customer = field(repr=False)
    locked_shop_customer: _LockedShopCustomer = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedTenantDisclosureTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantDisclosureTargetResult:
    error: ErrorCode | None
    locked: LockedTenantDisclosureTarget | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(
            self.locked, LockedTenantDisclosureTarget
        ):
            raise ValueError("Tenant disclosure target result is invalid")
        if self.error not in {
            None,
            ErrorCode.SHOP_CUSTOMER_UNAVAILABLE,
            ErrorCode.FORBIDDEN,
            ErrorCode.SHOP_SUSPENDED,
        }:
            raise ValueError("Tenant disclosure target error is invalid")

    def __repr__(self) -> str:
        return f"TenantDisclosureTargetResult(error={self.error!r}, target=<redacted>)"


def discover_tenant_disclosure_target(
    session: Session,
    *,
    actor: DetachedDisclosureActorContext,
    shop_customer_id: ShopCustomerId,
) -> _DiscoveredTenantDisclosureTarget | None:
    """Resolve only scalar target IDs through the actor's current Shop."""

    _require_actor(actor)
    if not isinstance(shop_customer_id, ShopCustomerId):
        raise TypeError("shop_customer_id must be a ShopCustomerId")
    row = session.execute(
        select(
            ShopCustomer.id,
            ShopCustomer.shop_id,
            ShopCustomer.customer_id,
            Customer.user_id,
        )
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .where(
            ShopCustomer.id == shop_customer_id.as_uuid(),
            ShopCustomer.shop_id == actor.current_shop_id,
        )
    ).one_or_none()
    if row is None:
        return None
    return _DiscoveredTenantDisclosureTarget(
        shop_customer_id=row.id,
        shop_id=row.shop_id,
        customer_id=row.customer_id,
        target_user_id=row.user_id,
    )


def lock_tenant_disclosure_target(
    session: Session,
    *,
    actor: DetachedDisclosureActorContext,
    candidate: _DiscoveredTenantDisclosureTarget | None,
) -> TenantDisclosureTargetResult:
    """Lock Shop -> Staff -> actor User -> target Customer -> ShopCustomer."""

    _require_actor(actor)
    unavailable = ErrorCode.SHOP_CUSTOMER_UNAVAILABLE
    if candidate is None:
        return TenantDisclosureTargetResult(error=unavailable)
    if not isinstance(candidate, _DiscoveredTenantDisclosureTarget):
        raise TypeError("candidate must come from disclosure discovery")
    if candidate.shop_id != actor.current_shop_id:
        return TenantDisclosureTargetResult(error=unavailable)

    locked_shop = lock_shop_for_update(session, shop_id=actor.current_shop_id)
    if locked_shop is None:
        return TenantDisclosureTargetResult(error=unavailable)
    if locked_shop.shop.status == ShopStatus.SUSPENDED.value:
        return TenantDisclosureTargetResult(error=ErrorCode.SHOP_SUSPENDED)
    if locked_shop.shop.status != ShopStatus.ACTIVE.value:
        return TenantDisclosureTargetResult(error=ErrorCode.FORBIDDEN)

    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=actor.actor_user_id,
    )
    if (
        locked_staff is None
        or not locked_staff.staff.is_active
        or locked_staff.staff.revoked_at is not None
        or ShopRole(locked_staff.staff.role) not in _DISCLOSURE_ROLES
    ):
        return TenantDisclosureTargetResult(error=ErrorCode.FORBIDDEN)

    locked_actor = session.scalar(
        select(User).where(User.id == actor.actor_user_id).with_for_update()
    )
    if locked_actor is None or not locked_actor.is_active:
        return TenantDisclosureTargetResult(error=ErrorCode.FORBIDDEN)

    customer = lock_existing_own_customer_for_update(
        session,
        actor_user_id=candidate.target_user_id,
    )
    if (
        customer is None
        or customer.id != candidate.customer_id
        or customer.user_id != candidate.target_user_id
    ):
        return TenantDisclosureTargetResult(error=unavailable)

    locked_relation = lock_shop_customer_by_tenant_locator(
        session,
        locked_shop=locked_shop,
        shop_customer_id=ShopCustomerId(candidate.shop_customer_id),
    )
    if (
        locked_relation is None
        or locked_relation.row.shop_id != actor.current_shop_id
        or locked_relation.row.customer_id != customer.id
    ):
        return TenantDisclosureTargetResult(error=unavailable)

    return TenantDisclosureTargetResult(
        error=None,
        locked=LockedTenantDisclosureTarget(
            actor_user_id=UserId(locked_actor.id),
            current_shop_id=ShopId(locked_shop.shop.id),
            customer_id=CustomerId(customer.id),
            shop_customer_id=ShopCustomerId(locked_relation.row.id),
            role=ShopRole(locked_staff.staff.role),
            locked_shop=locked_shop,
            locked_staff=locked_staff,
            actor=locked_actor,
            customer=customer,
            locked_shop_customer=locked_relation,
            _session=session,
        ),
    )


def recheck_historical_disclosure_authority(
    session: Session, *, actor: DetachedDisclosureActorContext
) -> ErrorCode | None:
    """Read-only live authority check; all denial states are indistinguishable."""

    _require_actor(actor)
    access = get_shop_staff_access(
        session,
        shop_id=actor.current_shop_id,
        user_id=actor.actor_user_id,
    )
    if (
        access is None
        or access.shop_status is not ShopStatus.ACTIVE
        or not access.is_live
        or access.role not in _DISCLOSURE_ROLES
    ):
        return ErrorCode.SHOP_CUSTOMER_UNAVAILABLE
    return None


def validate_locked_tenant_disclosure_target(
    session: Session, token: object
) -> LockedTenantDisclosureTarget:
    if not isinstance(token, LockedTenantDisclosureTarget):
        raise TypeError("Disclosure target lock token is invalid")
    if token._session is not session:
        raise RuntimeError("Disclosure target lock belongs to another session")
    return token


def _require_actor(actor: object) -> DetachedDisclosureActorContext:
    if not isinstance(actor, DetachedDisclosureActorContext):
        raise TypeError("actor must be a DetachedDisclosureActorContext")
    return actor
