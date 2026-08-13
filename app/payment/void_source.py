"""Latest-only Payment void eligibility and source-coherence proof."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.debt.business_time import tashkent_business_date
from app.debt.contracts import DebtAggregate
from app.debt.enums import DebtStatus
from app.debt.repository import debt_aggregate_from_row
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
from app.rating.enums import RatingEventType
from app.rating.models import RatingEvent
from app.shop.values import ShopId
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "PaymentVoidPositiveSource",
    "PaymentVoidSourceClassification",
    "PaymentVoidSourceFacts",
    "prove_locked_payment_void_source",
)


class PaymentVoidSourceClassification(StrEnum):
    POSITIVE_MATCH = "positive_match"
    NO_COMPENSATION = "no_compensation"


class PaymentVoidPositiveSource(StrEnum):
    ON_TIME_PAID = "on_time_paid"
    WRITTEN_OFF_SETTLED = "written_off_settled"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentVoidSourceFacts:
    payment: PaymentAggregate = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    current_total: PostedPaymentTotalUZS = field(repr=False)
    as_of_payment_total: PostedPaymentTotalUZS = field(repr=False)
    classification: PaymentVoidSourceClassification
    positive_source: PaymentVoidPositiveSource | None
    source_revision: DebtRevision
    source_occurred_at: datetime

    def __post_init__(self) -> None:
        if (self.classification is PaymentVoidSourceClassification.POSITIVE_MATCH) != (
            self.positive_source is not None
        ):
            raise ValueError("Payment void source classification is invalid")

    def __repr__(self) -> str:
        return "PaymentVoidSourceFacts(<redacted>)"


def prove_locked_payment_void_source(
    session: Session,
    *,
    locked_target: LockedTenantPaymentVoidTarget,
    target_payment_id: PaymentId,
    expected_debt_revision: DebtRevision,
) -> PaymentVoidSourceFacts:
    """Recheck latest/payment/void/money/rating/audit facts under the Debt lock."""

    target = validate_locked_tenant_payment_void_target(session, locked_target)
    debt = debt_aggregate_from_row(target.locked_debt.row)
    if debt.revision != expected_debt_revision:
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
    source = _classify_positive_source(
        session,
        debt=debt,
        payment=latest,
        shop_customer_id=relation_id,
        as_of_payment_total=as_of_total,
    )
    if debt.status is DebtStatus.WRITTEN_OFF_SETTLED and source is None:
        raise RuntimeError("Settlement rating source is inconsistent")
    return PaymentVoidSourceFacts(
        payment=latest,
        debt_id=debt_id,
        shop_customer_id=relation_id,
        current_total=current_total,
        as_of_payment_total=as_of_total,
        classification=(
            PaymentVoidSourceClassification.POSITIVE_MATCH
            if source is not None
            else PaymentVoidSourceClassification.NO_COMPENSATION
        ),
        positive_source=source,
        source_revision=latest.debt_revision_after,
        source_occurred_at=latest.created_at,
    )


def _classify_positive_source(
    session: Session,
    *,
    debt: DebtAggregate,
    payment: PaymentAggregate,
    shop_customer_id: ShopCustomerId,
    as_of_payment_total: PostedPaymentTotalUZS,
) -> PaymentVoidPositiveSource | None:
    expected = {
        DebtStatus.PAID: (
            PaymentVoidPositiveSource.ON_TIME_PAID,
            RatingEventType.ON_TIME_PAID,
            "debt.paid",
        ),
        DebtStatus.WRITTEN_OFF_SETTLED: (
            PaymentVoidPositiveSource.WRITTEN_OFF_SETTLED,
            RatingEventType.WRITTEN_OFF_SETTLED,
            "debt.written_off_settled",
        ),
    }.get(debt.status)
    if expected is None:
        return None
    positive_source, event_type, debt_audit_type = expected
    events = tuple(
        session.execute(
            select(RatingEvent.id).where(
                RatingEvent.shop_customer_id == shop_customer_id.as_uuid(),
                RatingEvent.debt_id == payment.debt_id.as_uuid(),
                RatingEvent.event_type == event_type.value,
                RatingEvent.source_revision == payment.debt_revision_after.value,
                RatingEvent.occurred_at == payment.created_at,
            )
        )
    )
    payment_audits = tuple(
        session.execute(
            select(AuditLog.id).where(
                AuditLog.event_type == "payment.recorded",
                AuditLog.object_type == "payment",
                AuditLog.object_id == payment.id.as_uuid(),
                AuditLog.actor_kind == "USER",
                AuditLog.actor_user_id == payment.recorded_by_user_id,
                AuditLog.occurred_at == payment.created_at,
                AuditLog.payload["debt_revision_after"].as_integer()
                == payment.debt_revision_after.value,
            )
        )
    )
    debt_audits = tuple(
        session.execute(
            select(AuditLog.id).where(
                AuditLog.event_type == debt_audit_type,
                AuditLog.object_type == "debt",
                AuditLog.object_id == payment.debt_id.as_uuid(),
                AuditLog.actor_kind == "USER",
                AuditLog.actor_user_id == payment.recorded_by_user_id,
                AuditLog.occurred_at == payment.created_at,
                AuditLog.payload["debt_revision_after"].as_integer()
                == payment.debt_revision_after.value,
            )
        )
    )
    counts = (len(events), len(payment_audits), len(debt_audits))
    if counts == (0, 1, 1) and event_type is RatingEventType.ON_TIME_PAID:
        accepted_at = debt.accepted_at
        eligible_without_cap = (
            debt.overdue_revision is None
            and accepted_at is not None
            and debt.original_amount.value >= 100_000
            and tashkent_business_date(accepted_at)
            < tashkent_business_date(payment.created_at)
            <= debt.due_date
            and as_of_payment_total.value == debt.discounted_amount.value
        )
        if eligible_without_cap:
            cap_used = bool(
                session.scalar(
                    select(
                        exists().where(
                            RatingEvent.shop_customer_id == shop_customer_id.as_uuid(),
                            RatingEvent.event_type
                            == RatingEventType.ON_TIME_PAID.value,
                            RatingEvent.business_date
                            == tashkent_business_date(payment.created_at),
                        )
                    )
                )
            )
            if not cap_used:
                raise RuntimeError("Payment rating source is inconsistent")
        return None
    if counts != (1, 1, 1):
        raise RuntimeError("Payment rating source is inconsistent")
    return positive_source
