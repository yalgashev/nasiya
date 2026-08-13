"""Composition adapters for debt/payment-local structural rating ports."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.debt.rating_ports import (
    LockedOverdueRatingSource,
    OverdueRatingAppendOutcome,
    PendingOverdueRatingEffect,
    PendingWrittenOffRatingEffect,
    WrittenOffRatingAppendOutcome,
    validate_locked_overdue_rating_source,
)
from app.debt.values import DebtId, DebtRevision
from app.payment.rating_ports import (
    PaymentRatingAppendOutcome,
    PaymentRatingEligibility,
    PaymentRatingEligibilityFacts,
    PendingOnTimePaidRatingEffect,
)
from app.payment.targeting import (
    LockedTenantPaymentDebt,
    validate_locked_tenant_payment_debt,
)
from app.rating.contracts import (
    create_on_time_paid_rating_event,
    create_overdue_rating_event,
    create_written_off_rating_event,
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
from app.shop_customer.values import CustomerId, ShopCustomerId


class SqlAlchemyLockedRatingAppendAdapter:
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
