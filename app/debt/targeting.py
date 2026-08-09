"""Tenant-safe, forward-ordered target resolution for debt creation."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.repository import (
    _LockedActorTargetUsers,
    lock_actor_and_target_users_for_update,
)
from app.customer.models import Customer
from app.customer.repository import (
    _LockedActiveTargetCustomer,
    lock_active_customer_for_target_user,
)
from app.debt.dependencies import DetachedDebtActorAuthority
from app.debt.values import ShopCustomerId, ShopId, UserId
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import (
    _LockedActorShopStaff,
    _LockedShop,
    lock_actor_shop_staff_for_update,
    lock_shop_for_update,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import (
    _LockedShopCustomer,
    _mark_shop_customer_predecessors_locked,
    lock_shop_customer_by_pair,
)
from app.telegram.models import TelegramLink
from app.telegram.repository import (
    get_telegram_link_by_user_for_update,
    is_otp_eligible_telegram_link,
)

_DEBT_STAFF_ROLES = frozenset({ShopRole.OWNER, ShopRole.MANAGER, ShopRole.CASHIER})


@dataclass(frozen=True, slots=True, repr=False)
class _DiscoveredDebtTarget:
    """Transient IDs from a non-locking, tenant-scoped lookup."""

    shop_customer_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    target_user_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)

    def __repr__(self) -> str:
        return "_DiscoveredDebtTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _LockedDebtTargetBeforeOffer:
    """Locks through Customer; OfferVersion must be locked next."""

    candidate: _DiscoveredDebtTarget = field(repr=False)
    locked_shop: _LockedShop = field(repr=False)
    locked_staff: _LockedActorShopStaff = field(repr=False)
    locked_users: _LockedActorTargetUsers = field(repr=False)
    locked_link: TelegramLink = field(repr=False)
    locked_customer: _LockedActiveTargetCustomer = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "_LockedDebtTargetBeforeOffer(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedDebtTarget:
    """Complete live target chain after the final ShopCustomer lock."""

    _before_offer: _LockedDebtTargetBeforeOffer = field(repr=False)
    _locked_shop_customer: _LockedShopCustomer = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedDebtTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DebtTargetResolution:
    """Identifier-free public result with one generic failure code."""

    error: ErrorCode | None
    locked_before_offer: _LockedDebtTargetBeforeOffer | None = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        allowed_errors = {ErrorCode.FORBIDDEN, ErrorCode.SHOP_CUSTOMER_UNAVAILABLE}
        if self.error is None:
            if not isinstance(self.locked_before_offer, _LockedDebtTargetBeforeOffer):
                raise ValueError("Available debt target requires locked state")
        elif self.error not in allowed_errors or self.locked_before_offer is not None:
            raise ValueError("Debt target resolution is invalid")

    def __repr__(self) -> str:
        return f"DebtTargetResolution(error={self.error!r}, target=<redacted>)"


def discover_tenant_debt_target(
    session: Session,
    *,
    authority: DetachedDebtActorAuthority,
    shop_customer_id: ShopCustomerId,
) -> _DiscoveredDebtTarget | None:
    """Discover trusted target IDs without a lock and without cross-tenant fallback."""

    if not isinstance(authority, DetachedDebtActorAuthority):
        raise TypeError("authority must be detached debt authority")
    if not isinstance(shop_customer_id, ShopCustomerId):
        raise TypeError("shop_customer_id must be a ShopCustomerId")
    if not authority.is_authenticated:
        return None
    assert authority.current_shop_id is not None
    statement = (
        select(
            ShopCustomer.id,
            ShopCustomer.customer_id,
            Customer.user_id,
            ShopCustomer.shop_id,
        )
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .where(
            ShopCustomer.id == shop_customer_id.as_uuid(),
            ShopCustomer.shop_id == authority.current_shop_id,
        )
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        return None
    return _DiscoveredDebtTarget(
        shop_customer_id=row.id,
        customer_id=row.customer_id,
        target_user_id=row.user_id,
        shop_id=row.shop_id,
    )


def lock_debt_target_before_offer(
    session: Session,
    *,
    authority: DetachedDebtActorAuthority,
    candidate: _DiscoveredDebtTarget | None,
) -> DebtTargetResolution:
    """Lock Shop/Staff/User set/TelegramLink/Customer and revalidate live state."""

    if not isinstance(authority, DetachedDebtActorAuthority):
        raise TypeError("authority must be detached debt authority")
    if not authority.is_authenticated:
        return DebtTargetResolution(error=ErrorCode.FORBIDDEN)
    if candidate is None:
        return DebtTargetResolution(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)
    if not isinstance(candidate, _DiscoveredDebtTarget):
        raise TypeError("candidate must come from discover_tenant_debt_target")
    assert authority.actor_user_id is not None
    assert authority.current_shop_id is not None
    if candidate.shop_id != authority.current_shop_id:
        return DebtTargetResolution(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)

    locked_shop = lock_shop_for_update(session, shop_id=ShopId(candidate.shop_id))
    if locked_shop is None or locked_shop.shop.status != ShopStatus.ACTIVE.value:
        return DebtTargetResolution(error=ErrorCode.FORBIDDEN)
    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=UserId(authority.actor_user_id),
    )
    if (
        locked_staff is None
        or ShopRole(locked_staff.staff.role) not in _DEBT_STAFF_ROLES
    ):
        return DebtTargetResolution(error=ErrorCode.FORBIDDEN)

    locked_users = lock_actor_and_target_users_for_update(
        session,
        actor_user_id=authority.actor_user_id,
        target_user_id=candidate.target_user_id,
    )
    if locked_users is None or not locked_users.actor.is_active:
        return DebtTargetResolution(error=ErrorCode.FORBIDDEN)
    if not locked_users.target.is_active:
        return DebtTargetResolution(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)

    locked_link = get_telegram_link_by_user_for_update(session, locked_users.target)
    if not is_otp_eligible_telegram_link(
        locked_link, expected_user_id=candidate.target_user_id
    ):
        return DebtTargetResolution(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)
    assert locked_link is not None
    locked_customer = lock_active_customer_for_target_user(
        session, locked_users=locked_users
    )
    if (
        locked_customer is None
        or locked_customer.customer.id != candidate.customer_id
        or locked_customer.customer.user_id != candidate.target_user_id
    ):
        return DebtTargetResolution(error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE)

    return DebtTargetResolution(
        error=None,
        locked_before_offer=_LockedDebtTargetBeforeOffer(
            candidate=candidate,
            locked_shop=locked_shop,
            locked_staff=locked_staff,
            locked_users=locked_users,
            locked_link=locked_link,
            locked_customer=locked_customer,
            _session=session,
        ),
    )


def lock_debt_target_shop_customer_after_offer(
    session: Session,
    *,
    locked_before_offer: _LockedDebtTargetBeforeOffer,
    locked_offer: object,
) -> LockedDebtTarget | None:
    """Take the final ShopCustomer lock after the caller locks OfferVersion."""

    token = _validate_before_offer(session, locked_before_offer)
    from app.debt.offer_gate import validate_locked_debt_offer

    offer = validate_locked_debt_offer(session, locked_offer)
    if offer._locked_target is not token:
        raise ValueError("locked offer belongs to a different debt target")
    predecessors = _mark_shop_customer_predecessors_locked(
        session,
        locked_shop=token.locked_shop,
        locked_customer=token.locked_customer,
    )
    locked = lock_shop_customer_by_pair(session, locked_predecessors=predecessors)
    if (
        locked is None
        or locked.row.id != token.candidate.shop_customer_id
        or locked.row.shop_id != token.candidate.shop_id
        or locked.row.customer_id != token.candidate.customer_id
    ):
        return None
    return LockedDebtTarget(
        _before_offer=token,
        _locked_shop_customer=locked,
        _session=session,
    )


def _validate_before_offer(
    session: Session, token: object
) -> _LockedDebtTargetBeforeOffer:
    if not isinstance(token, _LockedDebtTargetBeforeOffer):
        raise TypeError("locked_before_offer must come from target resolver")
    if token._session is not session:
        raise RuntimeError("locked target belongs to a different session")
    return token


def _validate_locked_debt_target(session: Session, token: object) -> LockedDebtTarget:
    if not isinstance(token, LockedDebtTarget):
        raise TypeError("locked_target must come from target resolver")
    if token._session is not session:
        raise RuntimeError("locked target belongs to a different session")
    _validate_before_offer(session, token._before_offer)
    return token


def locked_debt_target_customer(
    session: Session, *, locked_target: LockedDebtTarget
) -> Customer:
    """Expose only the already locked Customer row to debt-owned adapters."""

    target = _validate_locked_debt_target(session, locked_target)
    return target._before_offer.locked_customer.customer
