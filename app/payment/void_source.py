"""Latest-only Payment void target and non-voided-money proof."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.debt.values import DebtId, DebtRevision
from app.payment.contracts import PaymentAggregate, PaymentNotVoidableError
from app.payment.models import PaymentVoid
from app.payment.repository import (
    latest_non_voided_payment_for_tenant_debt,
    non_voided_posted_payment_total,
)
from app.payment.values import PaymentId, PostedPaymentTotalUZS
from app.payment.void_targeting import (
    LockedTenantPaymentVoidTarget,
    validate_locked_tenant_payment_void_target,
)
from app.shop.values import ShopId
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "PaymentVoidSourceFacts",
    "prove_locked_payment_void_source",
)


@dataclass(frozen=True, slots=True, repr=False)
class PaymentVoidSourceFacts:
    payment: PaymentAggregate = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    current_total: PostedPaymentTotalUZS = field(repr=False)
    as_of_payment_total: PostedPaymentTotalUZS = field(repr=False)
    source_revision: DebtRevision
    source_occurred_at: datetime

    def __repr__(self) -> str:
        return "PaymentVoidSourceFacts(<redacted>)"


def prove_locked_payment_void_source(
    session: Session,
    *,
    locked_target: LockedTenantPaymentVoidTarget,
    target_payment_id: PaymentId,
    expected_debt_revision: DebtRevision,
) -> PaymentVoidSourceFacts:
    """Recheck latest Payment, void absence, and money under the Debt lock."""

    target = validate_locked_tenant_payment_void_target(session, locked_target)
    if target.locked_debt.row.revision != expected_debt_revision.value:
        raise PaymentNotVoidableError("Payment is not voidable")
    shop_id = ShopId(target.locked_debt.predecessors.current_shop_id)
    relation_id = ShopCustomerId(target.locked_debt.predecessors.shop_customer_id)
    debt_id = DebtId(target.locked_debt.row.id)
    latest = latest_non_voided_payment_for_tenant_debt(
        session,
        shop_id=shop_id,
        shop_customer_id=relation_id,
        debt_id=debt_id,
    )
    if latest is None or latest.id != target_payment_id:
        raise PaymentNotVoidableError("Payment is not voidable")
    if (
        session.scalar(
            select(PaymentVoid.id).where(
                PaymentVoid.payment_id == target_payment_id.as_uuid()
            )
        )
        is not None
    ):
        raise PaymentNotVoidableError("Payment is not voidable")
    current_total = non_voided_posted_payment_total(
        session,
        shop_id=shop_id,
        shop_customer_id=relation_id,
        debt_id=debt_id,
    )
    as_of_total = non_voided_posted_payment_total(
        session,
        shop_id=shop_id,
        shop_customer_id=relation_id,
        debt_id=debt_id,
        as_of_revision=latest.debt_revision_after,
    )
    return PaymentVoidSourceFacts(
        payment=latest,
        debt_id=debt_id,
        shop_customer_id=relation_id,
        current_total=current_total,
        as_of_payment_total=as_of_total,
        source_revision=latest.debt_revision_after,
        source_occurred_at=latest.created_at,
    )
