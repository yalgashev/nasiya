"""Tenant-scoped forward predecessor locks for M14 payment creation."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.customer.repository import lock_existing_own_customer_for_update
from app.debt.models import Debt
from app.debt.values import DebtId
from app.payment.dependencies import DetachedPaymentActorContext
from app.shop.enums import ShopRole, ShopStatus
from app.shop.repository import (
    _LockedActorShopStaff,
    _LockedShop,
    lock_actor_shop_staff_for_update,
    lock_shop_for_update,
)
from app.shop.values import ShopId, UserId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import (
    _LockedShopCustomer,
    lock_shop_customer_by_tenant_locator,
)
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "LockedTenantPaymentDebt",
    "LockedTenantPaymentPredecessors",
    "TenantPaymentDebtLockResult",
    "TenantPaymentTargetResult",
    "discover_tenant_payment_target",
    "lock_tenant_payment_predecessors",
    "lock_tenant_payment_debt",
    "validate_locked_tenant_payment_debt",
    "validate_locked_tenant_payment_predecessors",
)

_PAYMENT_STAFF_ROLES = frozenset({ShopRole.OWNER, ShopRole.MANAGER, ShopRole.CASHIER})


@dataclass(frozen=True, slots=True, repr=False)
class _DiscoveredTenantPaymentTarget:
    """Non-locking, current-Shop candidate IDs only."""

    debt_id: UUID = field(repr=False)
    shop_customer_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    target_user_id: UUID = field(repr=False)

    def __repr__(self) -> str:
        return "_DiscoveredTenantPaymentTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedTenantPaymentPredecessors:
    """Live locks through ShopCustomer; the Debt is deliberately still unlocked."""

    debt_id: UUID = field(repr=False)
    actor_user_id: UUID = field(repr=False)
    current_shop_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    shop_customer_id: UUID = field(repr=False)
    role: ShopRole
    locked_shop: _LockedShop = field(repr=False)
    locked_staff: _LockedActorShopStaff = field(repr=False)
    actor: User = field(repr=False)
    customer: Customer = field(repr=False)
    locked_shop_customer: _LockedShopCustomer = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return (
            "LockedTenantPaymentPredecessors("
            "identifiers=<redacted>, role=<redacted>, locks=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class LockedTenantPaymentDebt:
    """The tenant-revalidated Debt row locked after durable-key resolution."""

    row: Debt = field(repr=False)
    predecessors: LockedTenantPaymentPredecessors = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedTenantPaymentDebt(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantPaymentDebtLockResult:
    error: ErrorCode | None
    locked: LockedTenantPaymentDebt | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.error is None:
            if not isinstance(self.locked, LockedTenantPaymentDebt):
                raise ValueError("Available payment Debt requires a lock token")
        elif self.error is not ErrorCode.DEBT_UNAVAILABLE or self.locked is not None:
            raise ValueError("Payment Debt lock result is invalid")

    def __repr__(self) -> str:
        return f"TenantPaymentDebtLockResult(error={self.error!r}, debt=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantPaymentTargetResult:
    error: ErrorCode | None
    locked: LockedTenantPaymentPredecessors | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(
            self.locked, LockedTenantPaymentPredecessors
        ):
            raise ValueError("Tenant payment target result is invalid")
        if self.error not in {
            None,
            ErrorCode.DEBT_UNAVAILABLE,
            ErrorCode.FORBIDDEN,
            ErrorCode.SHOP_SUSPENDED,
        }:
            raise ValueError("Tenant payment target error is invalid")

    def __repr__(self) -> str:
        return f"TenantPaymentTargetResult(error={self.error!r}, target=<redacted>)"


def discover_tenant_payment_target(
    session: Session,
    *,
    actor: DetachedPaymentActorContext,
    debt_id: DebtId,
) -> _DiscoveredTenantPaymentTarget | None:
    """Resolve an opaque Debt locator only through the server current Shop."""

    _require_actor(actor)
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    row = session.execute(
        select(
            Debt.id,
            ShopCustomer.id,
            ShopCustomer.shop_id,
            ShopCustomer.customer_id,
            Customer.user_id,
        )
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .where(
            Debt.id == debt_id.as_uuid(),
            ShopCustomer.shop_id == actor.current_shop_id,
        )
    ).one_or_none()
    if row is None:
        return None
    return _DiscoveredTenantPaymentTarget(
        debt_id=row[0],
        shop_customer_id=row[1],
        shop_id=row[2],
        customer_id=row[3],
        target_user_id=row[4],
    )


def lock_tenant_payment_predecessors(
    session: Session,
    *,
    actor: DetachedPaymentActorContext,
    candidate: _DiscoveredTenantPaymentTarget | None,
) -> TenantPaymentTargetResult:
    """Lock Shop -> Staff -> actor User -> target Customer -> ShopCustomer."""

    _require_actor(actor)
    if candidate is None:
        return TenantPaymentTargetResult(error=ErrorCode.DEBT_UNAVAILABLE)
    if not isinstance(candidate, _DiscoveredTenantPaymentTarget):
        raise TypeError("candidate must come from discover_tenant_payment_target")
    if candidate.shop_id != actor.current_shop_id:
        return TenantPaymentTargetResult(error=ErrorCode.DEBT_UNAVAILABLE)

    locked_shop = lock_shop_for_update(session, shop_id=ShopId(actor.current_shop_id))
    if locked_shop is None:
        return TenantPaymentTargetResult(error=ErrorCode.DEBT_UNAVAILABLE)
    if locked_shop.shop.status == ShopStatus.SUSPENDED.value:
        return TenantPaymentTargetResult(error=ErrorCode.SHOP_SUSPENDED)
    if locked_shop.shop.status != ShopStatus.ACTIVE.value:
        return TenantPaymentTargetResult(error=ErrorCode.FORBIDDEN)

    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=UserId(actor.actor_user_id),
    )
    if locked_staff is None or not _payment_staff_role_allowed(locked_staff.staff.role):
        return TenantPaymentTargetResult(error=ErrorCode.FORBIDDEN)
    role = ShopRole(locked_staff.staff.role)

    locked_actor = session.scalar(
        select(User).where(User.id == actor.actor_user_id).with_for_update()
    )
    if locked_actor is None or not locked_actor.is_active:
        return TenantPaymentTargetResult(error=ErrorCode.FORBIDDEN)

    customer = lock_existing_own_customer_for_update(
        session,
        actor_user_id=candidate.target_user_id,
    )
    if (
        customer is None
        or customer.id != candidate.customer_id
        or customer.user_id != candidate.target_user_id
    ):
        return TenantPaymentTargetResult(error=ErrorCode.DEBT_UNAVAILABLE)

    locked_shop_customer = lock_shop_customer_by_tenant_locator(
        session,
        locked_shop=locked_shop,
        shop_customer_id=ShopCustomerId(candidate.shop_customer_id),
    )
    if (
        locked_shop_customer is None
        or locked_shop_customer.row.shop_id != actor.current_shop_id
        or locked_shop_customer.row.customer_id != customer.id
    ):
        return TenantPaymentTargetResult(error=ErrorCode.DEBT_UNAVAILABLE)

    still_current = session.scalar(
        select(Debt.id)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(
            Debt.id == candidate.debt_id,
            Debt.shop_customer_id == locked_shop_customer.row.id,
            ShopCustomer.shop_id == actor.current_shop_id,
            ShopCustomer.customer_id == customer.id,
        )
    )
    if still_current != candidate.debt_id:
        return TenantPaymentTargetResult(error=ErrorCode.DEBT_UNAVAILABLE)

    return TenantPaymentTargetResult(
        error=None,
        locked=LockedTenantPaymentPredecessors(
            debt_id=candidate.debt_id,
            actor_user_id=locked_actor.id,
            current_shop_id=locked_shop.shop.id,
            customer_id=customer.id,
            shop_customer_id=locked_shop_customer.row.id,
            role=role,
            locked_shop=locked_shop,
            locked_staff=locked_staff,
            actor=locked_actor,
            customer=customer,
            locked_shop_customer=locked_shop_customer,
            _session=session,
        ),
    )


def validate_locked_tenant_payment_predecessors(
    session: Session, token: object
) -> LockedTenantPaymentPredecessors:
    if not isinstance(token, LockedTenantPaymentPredecessors):
        raise TypeError("locked predecessors must come from payment targeting")
    if token._session is not session:
        raise RuntimeError("locked payment predecessors belong to another session")
    return token


def lock_tenant_payment_debt(
    session: Session, *, predecessors: LockedTenantPaymentPredecessors
) -> TenantPaymentDebtLockResult:
    """Lock and revalidate exactly the candidate Debt under its locked parent."""

    token = validate_locked_tenant_payment_predecessors(session, predecessors)
    row = session.scalar(
        select(Debt)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(
            Debt.id == token.debt_id,
            Debt.shop_customer_id == token.shop_customer_id,
            ShopCustomer.shop_id == token.current_shop_id,
            ShopCustomer.customer_id == token.customer_id,
        )
        .with_for_update()
    )
    if row is None:
        return TenantPaymentDebtLockResult(error=ErrorCode.DEBT_UNAVAILABLE)
    return TenantPaymentDebtLockResult(
        error=None,
        locked=LockedTenantPaymentDebt(
            row=row,
            predecessors=token,
            _session=session,
        ),
    )


def validate_locked_tenant_payment_debt(
    session: Session, token: object
) -> LockedTenantPaymentDebt:
    if not isinstance(token, LockedTenantPaymentDebt):
        raise TypeError("locked Debt must come from payment targeting")
    if token._session is not session:
        raise RuntimeError("locked payment Debt belongs to another session")
    validate_locked_tenant_payment_predecessors(session, token.predecessors)
    if session.get(Debt, token.row.id) is not token.row:
        raise RuntimeError("locked payment Debt is detached")
    return token


def _payment_staff_role_allowed(raw_role: object) -> bool:
    try:
        role = ShopRole(raw_role)
    except (TypeError, ValueError):
        return False
    return role in _PAYMENT_STAFF_ROLES


def _require_actor(actor: DetachedPaymentActorContext) -> None:
    if not isinstance(actor, DetachedPaymentActorContext):
        raise TypeError("actor must be a DetachedPaymentActorContext")
