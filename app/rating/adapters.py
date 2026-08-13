"""Composition adapters for debt/payment-local structural rating ports."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.debt.business_time import tashkent_business_date
from app.debt.rating_ports import (
    LockedOverdueRatingSource,
    OverdueRatingAppendOutcome,
    PendingOverdueRatingEffect,
    PendingWrittenOffRatingEffect,
    WrittenOffRatingAppendOutcome,
    validate_locked_overdue_rating_source,
)
from app.debt.values import DebtId, DebtRevision
from app.payment.models import Payment
from app.payment.rating_ports import (
    PaymentRatingAppendOutcome,
    PaymentRatingEligibility,
    PaymentRatingEligibilityFacts,
    PaymentRatingPositiveSource,
    PaymentVoidCompensationAppendOutcome,
    PaymentVoidRatingAppendPort,
    PaymentVoidRatingSourceFacts,
    PaymentVoidRatingSourceReadPort,
    PendingOnTimePaidRatingEffect,
    PendingWrittenOffSettledRatingEffect,
    PreTransitionRatingSourceToken,
    WrittenOffSettledRatingAppendOutcome,
)
from app.payment.targeting import (
    LockedTenantPaymentDebt,
    validate_locked_tenant_payment_debt,
)
from app.rating.contracts import (
    RatingCompensationSourceProof,
    create_on_time_paid_rating_event,
    create_overdue_rating_event,
    create_rating_compensation_event,
    create_written_off_rating_event,
    create_written_off_settled_rating_event,
)
from app.rating.contracts import (
    RatingEvent as RatingEventContract,
)
from app.rating.eligibility import (
    OnTimePaidEligibilityFacts,
    evaluate_on_time_paid_eligibility,
)
from app.rating.enums import (
    PositiveRatingDecision,
    RatingEventAppendOutcome,
    RatingEventType,
    RatingRecordingSource,
)
from app.rating.models import RatingEvent
from app.rating.ports import LockedRatingSourceScope
from app.rating.repository import positive_cap_used_locked
from app.rating.service import append_locked_source_event
from app.rating.values import RatingEventId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import CustomerId, ShopCustomerId


class SqlAlchemyLockedRatingAppendAdapter(
    PaymentVoidRatingSourceReadPort, PaymentVoidRatingAppendPort
):
    """Stateless adapter; validates inherited tokens and never acquires locks."""

    def evaluate_on_time_paid(
        self, facts: PaymentRatingEligibilityFacts
    ) -> PaymentRatingEligibility:
        if not isinstance(facts, PaymentRatingEligibilityFacts):
            raise TypeError("facts must be PaymentRatingEligibilityFacts")
        decision = evaluate_on_time_paid_eligibility(
            OnTimePaidEligibilityFacts(
                shop_customer_id=facts.shop_customer_id,
                pre_status=facts.pre_status,
                post_status=facts.post_status,
                payment_amount=facts.payment_amount,
                discounted_remaining=facts.discounted_remaining,
                original_amount=facts.original_amount,
                accepted_at=facts.accepted_at,
                payment_created_at=facts.payment_created_at,
                due_date=facts.due_date,
                overdue_at=facts.overdue_at,
                overdue_revision=facts.overdue_revision,
                daily_cap_already_used=facts.daily_cap_already_used,
            )
        ).decision
        if decision is PositiveRatingDecision.AWARD:
            return PaymentRatingEligibility.AWARD
        if decision is PositiveRatingDecision.DAILY_CAP_ALREADY_USED:
            return PaymentRatingEligibility.DAILY_CAP_ALREADY_USED
        return PaymentRatingEligibility.NO_BONUS

    def positive_daily_slot_used(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        payment_business_date: date,
    ) -> bool:
        locked = validate_locked_tenant_payment_debt(session, locked_debt)
        scope = self._payment_scope(session, locked)
        return positive_cap_used_locked(
            session,
            locked_customer=scope.customer_scope,
            shop_customer_id=scope.shop_customer_id,
            business_date=payment_business_date,
        )

    def append_pending_on_time_paid(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        effect: PendingOnTimePaidRatingEffect,
    ) -> PaymentRatingAppendOutcome:
        locked = validate_locked_tenant_payment_debt(session, locked_debt)
        if not isinstance(effect, PendingOnTimePaidRatingEffect):
            raise TypeError("effect must be a PendingOnTimePaidRatingEffect")
        result = append_locked_source_event(
            session,
            locked_source=self._payment_scope(session, locked),
            event=create_on_time_paid_rating_event(
                event_id=RatingEventId(effect.event_id),
                shop_customer_id=effect.shop_customer_id,
                debt_id=effect.debt_id,
                payment_created_at=effect.payment_created_at,
                source_revision=effect.source_revision,
                recording_source=RatingRecordingSource.LIVE,
            ),
        )
        return _payment_outcome(result.outcome)

    def append_pending_overdue(
        self,
        session: Session,
        *,
        locked_source: LockedOverdueRatingSource,
        effect: PendingOverdueRatingEffect,
    ) -> OverdueRatingAppendOutcome:
        source = validate_locked_overdue_rating_source(session, locked_source)
        if not isinstance(effect, PendingOverdueRatingEffect):
            raise TypeError("effect must be a PendingOverdueRatingEffect")
        result = append_locked_source_event(
            session,
            locked_source=LockedRatingSourceScope(
                customer_id=CustomerId(source.customer_id),
                shop_customer_id=source.shop_customer_id,
                debt_id=source.debt_id,
                _session=session,
            ),
            event=create_overdue_rating_event(
                event_id=RatingEventId(effect.event_id),
                shop_customer_id=effect.shop_customer_id,
                debt_id=effect.debt_id,
                overdue_at=effect.overdue_at,
                source_revision=effect.source_revision,
                recording_source=RatingRecordingSource.LIVE,
            ),
        )
        if result.outcome is RatingEventAppendOutcome.APPENDED:
            return OverdueRatingAppendOutcome.APPENDED
        if result.outcome is RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS:
            return OverdueRatingAppendOutcome.SOURCE_ALREADY_EXISTS
        raise RuntimeError("Rating event append failed")

    def append_pending_written_off(
        self,
        session: Session,
        *,
        locked_source: LockedOverdueRatingSource,
        effect: PendingWrittenOffRatingEffect,
    ) -> WrittenOffRatingAppendOutcome:
        source = validate_locked_overdue_rating_source(session, locked_source)
        if not isinstance(effect, PendingWrittenOffRatingEffect):
            raise TypeError("effect must be a PendingWrittenOffRatingEffect")
        result = append_locked_source_event(
            session,
            locked_source=LockedRatingSourceScope(
                customer_id=CustomerId(source.customer_id),
                shop_customer_id=source.shop_customer_id,
                debt_id=source.debt_id,
                _session=session,
            ),
            event=create_written_off_rating_event(
                event_id=RatingEventId(effect.event_id),
                shop_customer_id=effect.shop_customer_id,
                debt_id=effect.debt_id,
                written_off_at=effect.written_off_at,
                source_revision=effect.source_revision,
            ),
        )
        if result.outcome is RatingEventAppendOutcome.APPENDED:
            return WrittenOffRatingAppendOutcome.APPENDED
        if result.outcome is RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS:
            return WrittenOffRatingAppendOutcome.SOURCE_ALREADY_EXISTS
        raise RuntimeError("Rating event append failed")

    def has_coherent_overdue_source(
        self,
        session: Session,
        *,
        locked_source: LockedOverdueRatingSource,
        overdue_at,
        overdue_revision: DebtRevision,
    ) -> bool:
        source = validate_locked_overdue_rating_source(session, locked_source)
        if not isinstance(overdue_revision, DebtRevision):
            raise TypeError("overdue_revision must be a DebtRevision")
        rows = tuple(
            session.execute(
                select(
                    RatingEvent.shop_customer_id,
                    RatingEvent.delta,
                    RatingEvent.occurred_at,
                ).where(
                    RatingEvent.debt_id == source.debt_id.as_uuid(),
                    RatingEvent.event_type == RatingEventType.OVERDUE.value,
                )
            )
        )
        return len(rows) == 1 and (
            rows[0].shop_customer_id == source.shop_customer_id.as_uuid()
            and rows[0].delta == -15
            and rows[0].occurred_at == overdue_at
        )

    def has_coherent_written_off_source(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        written_off_at,
        written_off_revision: DebtRevision,
    ) -> bool:
        locked = validate_locked_tenant_payment_debt(session, locked_debt)
        if not isinstance(written_off_revision, DebtRevision):
            raise TypeError("written_off_revision must be a DebtRevision")
        rows = tuple(
            session.execute(
                select(
                    RatingEvent.shop_customer_id,
                    RatingEvent.delta,
                    RatingEvent.occurred_at,
                ).where(
                    RatingEvent.debt_id == locked.row.id,
                    RatingEvent.event_type == RatingEventType.WRITTEN_OFF.value,
                )
            )
        )
        audits = tuple(
            session.execute(
                select(
                    AuditLog.actor_kind,
                    AuditLog.actor_user_id,
                    AuditLog.object_type,
                    AuditLog.occurred_at,
                    AuditLog.payload,
                ).where(
                    AuditLog.event_type == "debt.written_off",
                    AuditLog.object_id == locked.row.id,
                )
            )
        )
        return (
            len(rows) == 1
            and len(audits) == 1
            and (
                rows[0].shop_customer_id == locked.row.shop_customer_id
                and rows[0].delta == -40
                and rows[0].occurred_at == written_off_at
                and audits[0].actor_kind == "USER"
                and audits[0].actor_user_id == locked.row.written_off_actor_user_id
                and audits[0].object_type == "debt"
                and audits[0].occurred_at == written_off_at
                and audits[0].payload.get("written_off_revision")
                == written_off_revision.value
            )
        )

    def append_pending_written_off_settled(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        effect: PendingWrittenOffSettledRatingEffect,
    ) -> WrittenOffSettledRatingAppendOutcome:
        locked = validate_locked_tenant_payment_debt(session, locked_debt)
        if not isinstance(effect, PendingWrittenOffSettledRatingEffect):
            raise TypeError("effect must be a PendingWrittenOffSettledRatingEffect")
        result = append_locked_source_event(
            session,
            locked_source=self._payment_scope(session, locked),
            event=create_written_off_settled_rating_event(
                event_id=RatingEventId(effect.event_id),
                shop_customer_id=effect.shop_customer_id,
                debt_id=effect.debt_id,
                written_off_settled_at=effect.payment_created_at,
                source_revision=effect.source_revision,
            ),
        )
        if result.outcome is RatingEventAppendOutcome.APPENDED:
            return WrittenOffSettledRatingAppendOutcome.APPENDED
        if result.outcome is RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS:
            return WrittenOffSettledRatingAppendOutcome.SOURCE_ALREADY_EXISTS
        raise RuntimeError("Rating event append failed")

    def read_pre_transition_source(
        self,
        session: Session,
        *,
        facts: PaymentVoidRatingSourceFacts,
    ) -> PreTransitionRatingSourceToken | None:
        """Read one exact positive source without acquiring another lock."""

        if not isinstance(facts, PaymentVoidRatingSourceFacts):
            raise TypeError("facts must be PaymentVoidRatingSourceFacts")

        positive_source, event_type, audit_type = {
            "paid": (
                PaymentRatingPositiveSource.ON_TIME_PAID,
                RatingEventType.ON_TIME_PAID,
                "debt.paid",
            ),
            "written_off_settled": (
                PaymentRatingPositiveSource.WRITTEN_OFF_SETTLED,
                RatingEventType.WRITTEN_OFF_SETTLED,
                "debt.written_off_settled",
            ),
        }.get(facts.terminal_status.value, (None, None, None))
        if positive_source is None:
            return None
        payment = session.execute(
            select(Payment.recorded_by_user_id).where(
                Payment.id == facts.payment_id.as_uuid(),
                Payment.debt_id == facts.debt_id.as_uuid(),
                Payment.amount_uzs == facts.payment_amount.value,
                Payment.debt_revision_after == facts.source_revision.value,
                Payment.created_at == facts.source_occurred_at,
            )
        ).one_or_none()
        events = tuple(
            session.execute(
                select(RatingEvent.id).where(
                    RatingEvent.shop_customer_id == facts.shop_customer_id.as_uuid(),
                    RatingEvent.debt_id == facts.debt_id.as_uuid(),
                    RatingEvent.event_type == event_type.value,
                    RatingEvent.source_revision == facts.source_revision.value,
                    RatingEvent.occurred_at == facts.source_occurred_at,
                )
            )
        )
        payment_audits = tuple(
            session.execute(
                select(AuditLog.id).where(
                    AuditLog.event_type == "payment.recorded",
                    AuditLog.object_type == "payment",
                    AuditLog.object_id == facts.payment_id.as_uuid(),
                    AuditLog.actor_kind == "USER",
                    AuditLog.actor_user_id
                    == (None if payment is None else payment.recorded_by_user_id),
                    AuditLog.occurred_at == facts.source_occurred_at,
                    AuditLog.payload["debt_revision_after"].as_integer()
                    == facts.source_revision.value,
                )
            )
        )
        debt_audits = tuple(
            session.execute(
                select(AuditLog.id).where(
                    AuditLog.event_type == audit_type,
                    AuditLog.object_type == "debt",
                    AuditLog.object_id == facts.debt_id.as_uuid(),
                    AuditLog.actor_kind == "USER",
                    AuditLog.actor_user_id
                    == (None if payment is None else payment.recorded_by_user_id),
                    AuditLog.occurred_at == facts.source_occurred_at,
                    AuditLog.payload["debt_revision_after"].as_integer()
                    == facts.source_revision.value,
                )
            )
        )
        counts = (
            payment is not None,
            len(events),
            len(payment_audits),
            len(debt_audits),
        )
        if counts == (True, 0, 1, 1) and event_type is RatingEventType.ON_TIME_PAID:
            accepted_at = facts.accepted_at
            eligible_without_cap = (
                facts.overdue_revision is None
                and accepted_at is not None
                and facts.original_amount.value >= 100_000
                and tashkent_business_date(accepted_at)
                < tashkent_business_date(facts.source_occurred_at)
                <= facts.due_date
                and facts.as_of_payment_total.value == facts.discounted_amount.value
            )
            if eligible_without_cap:
                cap_used = bool(
                    session.scalar(
                        select(
                            exists().where(
                                RatingEvent.shop_customer_id
                                == facts.shop_customer_id.as_uuid(),
                                RatingEvent.event_type
                                == RatingEventType.ON_TIME_PAID.value,
                                RatingEvent.business_date
                                == tashkent_business_date(facts.source_occurred_at),
                            )
                        )
                    )
                )
                if not cap_used:
                    raise RuntimeError("Payment rating source is inconsistent")
            return None
        if counts != (True, 1, 1, 1):
            raise RuntimeError("Payment rating source is inconsistent")
        return PreTransitionRatingSourceToken(
            payment_id=facts.payment_id,
            debt_id=facts.debt_id,
            shop_customer_id=facts.shop_customer_id,
            positive_source=positive_source,
            terminal_status=facts.terminal_status,
            source_revision=facts.source_revision,
            source_occurred_at=facts.source_occurred_at,
        )

    def append_source_compensation(
        self,
        session: Session,
        *,
        source: PreTransitionRatingSourceToken,
        voided_at: datetime,
        completed_replay: bool,
    ) -> PaymentVoidCompensationAppendOutcome:
        """Append one source-paired negative event under inherited locks."""

        if not isinstance(source, PreTransitionRatingSourceToken):
            raise TypeError("source must be a PreTransitionRatingSourceToken")
        customer_id = session.scalar(
            select(ShopCustomer.customer_id).where(
                ShopCustomer.id == source.shop_customer_id.as_uuid()
            )
        )
        positive_type = {
            PaymentRatingPositiveSource.ON_TIME_PAID: RatingEventType.ON_TIME_PAID,
            PaymentRatingPositiveSource.WRITTEN_OFF_SETTLED: (
                RatingEventType.WRITTEN_OFF_SETTLED
            ),
        }[source.positive_source]
        positive_row = session.execute(
            select(RatingEvent).where(
                RatingEvent.shop_customer_id == source.shop_customer_id.as_uuid(),
                RatingEvent.debt_id == source.debt_id.as_uuid(),
                RatingEvent.event_type == positive_type.value,
                RatingEvent.source_revision == source.source_revision.value,
                RatingEvent.occurred_at == source.source_occurred_at,
            )
        ).scalar_one_or_none()
        payment_row = session.execute(
            select(Payment.recorded_by_user_id).where(
                Payment.id == source.payment_id.as_uuid(),
                Payment.debt_id == source.debt_id.as_uuid(),
                Payment.debt_revision_after == source.source_revision.value,
                Payment.created_at == source.source_occurred_at,
            )
        ).one_or_none()
        audit_type = {
            PaymentRatingPositiveSource.ON_TIME_PAID: "debt.paid",
            PaymentRatingPositiveSource.WRITTEN_OFF_SETTLED: (
                "debt.written_off_settled"
            ),
        }[source.positive_source]
        audits = tuple(
            session.execute(
                select(AuditLog.id).where(
                    AuditLog.event_type == audit_type,
                    AuditLog.object_type == "debt",
                    AuditLog.object_id == source.debt_id.as_uuid(),
                    AuditLog.actor_kind == "USER",
                    AuditLog.actor_user_id
                    == (
                        None if payment_row is None else payment_row.recorded_by_user_id
                    ),
                    AuditLog.occurred_at == source.source_occurred_at,
                    AuditLog.payload["debt_revision_after"].as_integer()
                    == source.source_revision.value,
                )
            )
        )
        if customer_id is None or positive_row is None or len(audits) != 1:
            raise RuntimeError("Payment rating source is inconsistent")
        positive = RatingEventContract(
            id=RatingEventId(positive_row.id),
            shop_customer_id=ShopCustomerId(positive_row.shop_customer_id),
            debt_id=DebtId(positive_row.debt_id),
            event_type=RatingEventType(positive_row.event_type),
            delta=positive_row.delta,
            occurred_at=positive_row.occurred_at,
            business_date=positive_row.business_date,
            recording_source=RatingRecordingSource(positive_row.recording_source),
            source_revision=DebtRevision(positive_row.source_revision),
        )
        result = append_locked_source_event(
            session,
            locked_source=LockedRatingSourceScope(
                customer_id=CustomerId(customer_id),
                shop_customer_id=source.shop_customer_id,
                debt_id=source.debt_id,
                _session=session,
            ),
            event=create_rating_compensation_event(
                event_id=RatingEventId(uuid4()),
                source=RatingCompensationSourceProof(
                    payment_id=source.payment_id.as_uuid(),
                    payment_debt_id=source.debt_id,
                    payment_shop_customer_id=source.shop_customer_id,
                    payment_revision=source.source_revision,
                    payment_created_at=source.source_occurred_at,
                    positive_event=positive,
                    audit_event_type=audit_type,
                    audit_debt_id=source.debt_id,
                    audit_revision=source.source_revision,
                    audit_occurred_at=source.source_occurred_at,
                ),
                voided_at=voided_at,
            ),
        )
        if result.outcome is RatingEventAppendOutcome.APPENDED:
            return PaymentVoidCompensationAppendOutcome.APPENDED
        if result.outcome is RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS:
            if completed_replay:
                return PaymentVoidCompensationAppendOutcome.SOURCE_ALREADY_EXISTS
            raise RuntimeError("Payment rating compensation already exists")
        raise RuntimeError("Payment rating compensation append failed")

    @staticmethod
    def _payment_scope(
        session: Session,
        locked: LockedTenantPaymentDebt,
    ) -> LockedRatingSourceScope:
        return LockedRatingSourceScope(
            customer_id=CustomerId(locked.predecessors.customer_id),
            shop_customer_id=ShopCustomerId(locked.row.shop_customer_id),
            debt_id=DebtId(locked.row.id),
            _session=session,
        )


def _payment_outcome(
    outcome: RatingEventAppendOutcome,
) -> PaymentRatingAppendOutcome:
    if outcome is RatingEventAppendOutcome.APPENDED:
        return PaymentRatingAppendOutcome.APPENDED
    if outcome is RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS:
        return PaymentRatingAppendOutcome.SOURCE_ALREADY_EXISTS
    if outcome is RatingEventAppendOutcome.POSITIVE_DAILY_CAP_ALREADY_USED:
        return PaymentRatingAppendOutcome.DAILY_CAP_ALREADY_USED
    raise RuntimeError("Rating event append failed")
