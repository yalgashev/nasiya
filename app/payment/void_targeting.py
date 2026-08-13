"""Tenant discovery and forward locks for one Payment void correction."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.debt.models import Debt
from app.debt.values import DebtId
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.models import Payment
from app.payment.targeting import (
    LockedTenantPaymentDebt,
    LockedTenantPaymentPredecessors,
    discover_tenant_payment_target,
    lock_tenant_payment_debt,
    lock_tenant_payment_predecessors,
    validate_locked_tenant_payment_debt,
)
from app.payment.values import PaymentId
from app.shop.enums import ShopRole
from app.shop_customer.models import ShopCustomer

__all__ = (
    "DiscoveredTenantPaymentVoidTarget",
    "LockedTenantPaymentVoidTarget",
    "TenantPaymentVoidTargetResult",
    "discover_tenant_payment_void_target",
    "lock_tenant_payment_void_predecessors",
    "lock_tenant_payment_void_target",
    "validate_locked_tenant_payment_void_target",
)

_VOID_ROLES = frozenset({ShopRole.OWNER, ShopRole.MANAGER})


@dataclass(frozen=True, slots=True, repr=False)
class DiscoveredTenantPaymentVoidTarget:
    """Detached server-resolved scalar target; no ORM row escapes."""

    payment_id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)

    def __repr__(self) -> str:
        return "DiscoveredTenantPaymentVoidTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedTenantPaymentVoidTarget:
    locked_debt: LockedTenantPaymentDebt = field(repr=False)
    payment: Payment = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedTenantPaymentVoidTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantPaymentVoidTargetResult:
    error: ErrorCode | None
    locked: LockedTenantPaymentVoidTarget | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(
            self.locked, LockedTenantPaymentVoidTarget
        ):
            raise ValueError("Payment void target result is invalid")


def discover_tenant_payment_void_target(
    session: Session,
    *,
    actor: DetachedPaymentActorContext,
    payment_id: PaymentId,
) -> DiscoveredTenantPaymentVoidTarget | None:
    if not isinstance(payment_id, PaymentId):
        raise TypeError("payment_id must be a PaymentId")
    row = session.execute(
        select(Payment.id, Payment.debt_id)
        .join(Debt, Debt.id == Payment.debt_id)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(
            Payment.id == payment_id.as_uuid(),
            ShopCustomer.shop_id == actor.current_shop_id,
        )
    ).one_or_none()
    if row is None:
        return None
    debt_id = DebtId(row.debt_id)
    if discover_tenant_payment_target(session, actor=actor, debt_id=debt_id) is None:
        return None
    return DiscoveredTenantPaymentVoidTarget(
        payment_id=PaymentId(row.id), debt_id=debt_id
    )


def lock_tenant_payment_void_predecessors(
    session: Session,
    *,
    actor: DetachedPaymentActorContext,
    candidate: DiscoveredTenantPaymentVoidTarget | None,
) -> tuple[ErrorCode | None, LockedTenantPaymentPredecessors | None]:
    if candidate is None:
        return ErrorCode.PAYMENT_UNAVAILABLE, None
    debt_candidate = discover_tenant_payment_target(
        session, actor=actor, debt_id=candidate.debt_id
    )
    result = lock_tenant_payment_predecessors(
        session, actor=actor, candidate=debt_candidate
    )
    if result.error is not None:
        return result.error, None
    assert result.locked is not None
    if result.locked.role not in _VOID_ROLES:
        return ErrorCode.FORBIDDEN, None
    return None, result.locked


def lock_tenant_payment_void_target(
    session: Session,
    *,
    candidate: DiscoveredTenantPaymentVoidTarget,
    predecessors: LockedTenantPaymentPredecessors,
) -> TenantPaymentVoidTargetResult:
    debt_result = lock_tenant_payment_debt(session, predecessors=predecessors)
    if debt_result.error is not None:
        return TenantPaymentVoidTargetResult(error=debt_result.error)
    assert debt_result.locked is not None
    payment = session.scalar(
        select(Payment)
        .where(
            Payment.id == candidate.payment_id.as_uuid(),
            Payment.debt_id == debt_result.locked.row.id,
        )
        .with_for_update()
    )
    if payment is None:
        return TenantPaymentVoidTargetResult(error=ErrorCode.PAYMENT_UNAVAILABLE)
    return TenantPaymentVoidTargetResult(
        error=None,
        locked=LockedTenantPaymentVoidTarget(
            locked_debt=debt_result.locked,
            payment=payment,
            _session=session,
        ),
    )


def validate_locked_tenant_payment_void_target(
    session: Session, token: object
) -> LockedTenantPaymentVoidTarget:
    if not isinstance(token, LockedTenantPaymentVoidTarget):
        raise TypeError("locked void target must come from payment void targeting")
    if token._session is not session:
        raise RuntimeError("locked void target belongs to another session")
    validate_locked_tenant_payment_debt(session, token.locked_debt)
    if session.get(Payment, token.payment.id) is not token.payment:
        raise RuntimeError("locked void Payment is detached")
    return token
