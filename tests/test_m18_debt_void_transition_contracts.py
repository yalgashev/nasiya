from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.contracts import (
    DebtAggregate,
    DebtLifecycleError,
    DebtPaymentVoidTransition,
    DebtPaymentVoidTransitionError,
    PendingPaymentVoidOverdueEffect,
    WriteOffReason,
)
from app.debt.enums import DebtOverdueSource, DebtStatus
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
)

CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)
ACCEPTED_AT = datetime(2026, 1, 2, tzinfo=UTC)
DUE_DATE = date(2026, 1, 5)
ON_DUE_DATE = datetime(2026, 1, 5, 18, tzinfo=UTC)
AFTER_DUE_DATE = datetime(2026, 1, 5, 19, tzinfo=UTC)


def _active() -> DebtAggregate:
    return DebtAggregate.create_pending(
        debt_id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=uuid4(),
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discount_basis_points=DiscountBasisPoints(1000),
        discounted_amount=DiscountedAmountUZS(Decimal("900")),
        due_date=DUE_DATE,
        created_at=CREATED_AT,
    ).accept(now=ACCEPTED_AT)


def _active_partial() -> DebtAggregate:
    active = _active()
    return active.record_payment(
        payment_amount_uzs=Decimal("100"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=active.revision,
        payment_created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )


def _overdue() -> DebtAggregate:
    return _active().mark_overdue(
        now=AFTER_DUE_DATE,
        source=DebtOverdueSource.BATCH,
        posted_total_uzs=Decimal("0"),
    )


def _overdue_partial() -> DebtAggregate:
    overdue = _overdue()
    return overdue.record_payment(
        payment_amount_uzs=Decimal("100"),
        current_remaining_due_uzs=Decimal("1000"),
        expected_revision=overdue.revision,
        payment_created_at=AFTER_DUE_DATE,
    )


def _written_off() -> DebtAggregate:
    overdue = _overdue()
    return overdue.mark_written_off(
        now=AFTER_DUE_DATE,
        actor_user_id=uuid4(),
        reason=WriteOffReason.LEGAL_OR_COMPLIANCE,
        posted_total_uzs=Decimal("0"),
        expected_revision=overdue.revision,
    )


def _written_off_partial() -> DebtAggregate:
    written_off = _written_off()
    return written_off.record_written_off_recovery(
        payment_amount_uzs=Decimal("100"),
        current_remaining_due_uzs=Decimal("1000"),
        expected_revision=written_off.revision,
        payment_created_at=AFTER_DUE_DATE,
    )


def _settled() -> DebtAggregate:
    written_off = _written_off()
    return written_off.record_written_off_recovery(
        payment_amount_uzs=Decimal("1000"),
        current_remaining_due_uzs=Decimal("1000"),
        expected_revision=written_off.revision,
        payment_created_at=AFTER_DUE_DATE,
    )


def _paid_without_overdue_marker() -> DebtAggregate:
    active = _active()
    return active.record_payment(
        payment_amount_uzs=Decimal("900"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=active.revision,
        payment_created_at=ON_DUE_DATE,
    )


def _paid_with_overdue_marker() -> DebtAggregate:
    overdue = _overdue()
    return overdue.record_payment(
        payment_amount_uzs=Decimal("1000"),
        current_remaining_due_uzs=Decimal("1000"),
        expected_revision=overdue.revision,
        payment_created_at=AFTER_DUE_DATE,
    )


def _same_status_after(before: DebtAggregate, voided_at: datetime) -> DebtAggregate:
    return replace(
        before,
        revision=DebtRevision(before.revision.value + 1),
        updated_at=voided_at,
    )


def _transition(
    before: DebtAggregate,
    after: DebtAggregate,
    *,
    voided_at: datetime,
    effect: PendingPaymentVoidOverdueEffect | None = None,
    expected_revision: DebtRevision | None = None,
    remaining: Decimal = Decimal("1"),
) -> DebtPaymentVoidTransition:
    return DebtPaymentVoidTransition(
        before=before,
        debt=after,
        expected_revision=expected_revision or before.revision,
        voided_at=voided_at,
        remaining_due_uzs=remaining,
        overdue_effect=effect,
    )


@pytest.mark.parametrize(
    "source", (_active_partial, _overdue_partial, _written_off_partial)
)
def test_partial_void_preserves_status_markers_and_exactly_one_revision(
    source: object,
) -> None:
    before = source()  # type: ignore[operator]
    voided_at = before.updated_at
    result = _transition(
        before,
        _same_status_after(before, voided_at),
        voided_at=voided_at,
    )

    assert result.debt.status is before.status
    assert result.debt.revision == DebtRevision(before.revision.value + 1)
    assert result.debt.updated_at == voided_at
    assert result.debt.accepted_at == before.accepted_at
    assert result.debt.overdue_at == before.overdue_at
    assert result.debt.overdue_revision == before.overdue_revision
    assert result.debt.written_off_at == before.written_off_at
    assert result.debt.written_off_revision == before.written_off_revision
    assert result.overdue_effect is None


def test_paid_void_on_due_date_reopens_active_and_clears_only_paid_marker() -> None:
    before = _paid_without_overdue_marker()
    after = replace(
        before,
        status=DebtStatus.ACTIVE,
        revision=DebtRevision(before.revision.value + 1),
        updated_at=ON_DUE_DATE,
        paid_at=None,
    )

    result = _transition(before, after, voided_at=ON_DUE_DATE)

    assert result.debt.status is DebtStatus.ACTIVE
    assert result.debt.paid_at is None
    assert result.debt.accepted_at == before.accepted_at
    assert result.debt.overdue_at is None
    assert result.overdue_effect is None


def test_paid_after_due_void_is_one_revision_with_exact_pending_overdue_effect() -> (
    None
):
    before = _paid_without_overdue_marker()
    revision = DebtRevision(before.revision.value + 1)
    after = replace(
        before,
        status=DebtStatus.OVERDUE,
        revision=revision,
        updated_at=AFTER_DUE_DATE,
        paid_at=None,
        overdue_at=AFTER_DUE_DATE,
        overdue_revision=revision,
    )
    effect = PendingPaymentVoidOverdueEffect(
        source=DebtOverdueSource.PAYMENT_VOID,
        from_status=DebtStatus.PAID,
        overdue_revision=revision,
        occurred_at=AFTER_DUE_DATE,
    )

    result = _transition(
        before,
        after,
        voided_at=AFTER_DUE_DATE,
        effect=effect,
    )

    assert result.debt.status is DebtStatus.OVERDUE
    assert result.debt.revision == result.debt.overdue_revision
    assert result.debt.updated_at == result.debt.overdue_at == AFTER_DUE_DATE
    assert result.overdue_effect is effect
    assert result.overdue_effect.source is DebtOverdueSource.PAYMENT_VOID
    assert result.overdue_effect.from_status is DebtStatus.PAID


def test_late_paid_void_preserves_old_overdue_marker_without_new_effect() -> None:
    before = _paid_with_overdue_marker()
    after = replace(
        before,
        status=DebtStatus.OVERDUE,
        revision=DebtRevision(before.revision.value + 1),
        updated_at=AFTER_DUE_DATE,
        paid_at=None,
    )

    result = _transition(before, after, voided_at=AFTER_DUE_DATE)

    assert result.debt.overdue_at == before.overdue_at
    assert result.debt.overdue_revision == before.overdue_revision
    assert result.overdue_effect is None


def test_settlement_void_reopens_written_off_and_clears_only_settlement_pair() -> None:
    before = _settled()
    after = replace(
        before,
        status=DebtStatus.WRITTEN_OFF,
        revision=DebtRevision(before.revision.value + 1),
        updated_at=AFTER_DUE_DATE,
        written_off_settled_at=None,
        written_off_settled_revision=None,
    )

    result = _transition(before, after, voided_at=AFTER_DUE_DATE)

    assert result.debt.status is DebtStatus.WRITTEN_OFF
    assert result.debt.written_off_settled_at is None
    assert result.debt.written_off_settled_revision is None
    assert result.debt.written_off_at == before.written_off_at
    assert result.debt.written_off_revision == before.written_off_revision
    assert result.debt.written_off_reason == before.written_off_reason
    assert result.debt.written_off_actor_user_id == before.written_off_actor_user_id


def test_generic_overdue_transition_cannot_use_payment_void_source() -> None:
    with pytest.raises(DebtLifecycleError, match="requires a paid Debt transition"):
        _active().mark_overdue(
            now=AFTER_DUE_DATE,
            source=DebtOverdueSource.PAYMENT_VOID,
            posted_total_uzs=Decimal("0"),
        )


def test_stale_revision_terminal_result_and_nonpositive_remaining_are_denied() -> None:
    before = _active_partial()
    lawful_after = _same_status_after(before, before.updated_at)
    with pytest.raises(DebtPaymentVoidTransitionError, match="revision changed"):
        _transition(
            before,
            lawful_after,
            voided_at=before.updated_at,
            expected_revision=DebtRevision(before.revision.value - 1),
        )
    with pytest.raises(DebtPaymentVoidTransitionError, match="exactly one"):
        _transition(
            before,
            replace(
                lawful_after,
                revision=DebtRevision(before.revision.value + 2),
            ),
            voided_at=before.updated_at,
        )
    paid = _paid_without_overdue_marker()
    terminal_after = replace(
        paid,
        revision=DebtRevision(paid.revision.value + 1),
        updated_at=ON_DUE_DATE,
    )
    with pytest.raises(DebtPaymentVoidTransitionError, match="paid marker"):
        _transition(paid, terminal_after, voided_at=ON_DUE_DATE)
    for invalid_remaining in (Decimal("0"), Decimal("-1"), Decimal("1.5")):
        with pytest.raises(DebtPaymentVoidTransitionError, match="positive"):
            _transition(
                before,
                lawful_after,
                voided_at=before.updated_at,
                remaining=invalid_remaining,
            )


def test_wrong_time_marker_mutation_and_missing_or_extra_effect_fail_closed() -> None:
    active = _active_partial()
    with pytest.raises(DebtPaymentVoidTransitionError, match="update time"):
        _transition(
            active,
            _same_status_after(active, active.updated_at),
            voided_at=active.updated_at.replace(hour=active.updated_at.hour + 1),
        )

    overdue = _overdue_partial()
    changed_overdue = replace(
        overdue,
        revision=DebtRevision(overdue.revision.value + 1),
        updated_at=AFTER_DUE_DATE,
        overdue_revision=DebtRevision(overdue.overdue_revision.value + 1),  # type: ignore[union-attr]
    )
    with pytest.raises(DebtPaymentVoidTransitionError, match="historic overdue"):
        _transition(overdue, changed_overdue, voided_at=AFTER_DUE_DATE)

    paid = _paid_without_overdue_marker()
    late_revision = DebtRevision(paid.revision.value + 1)
    late_after = replace(
        paid,
        status=DebtStatus.OVERDUE,
        revision=late_revision,
        updated_at=AFTER_DUE_DATE,
        paid_at=None,
        overdue_at=AFTER_DUE_DATE,
        overdue_revision=late_revision,
    )
    with pytest.raises(DebtPaymentVoidTransitionError, match="pending overdue"):
        _transition(paid, late_after, voided_at=AFTER_DUE_DATE)

    early_after = replace(
        paid,
        status=DebtStatus.ACTIVE,
        revision=late_revision,
        updated_at=ON_DUE_DATE,
        paid_at=None,
    )
    extra_effect = PendingPaymentVoidOverdueEffect(
        source=DebtOverdueSource.PAYMENT_VOID,
        from_status=DebtStatus.PAID,
        overdue_revision=late_revision,
        occurred_at=ON_DUE_DATE,
    )
    with pytest.raises(DebtPaymentVoidTransitionError, match="unexpected"):
        _transition(
            paid,
            early_after,
            voided_at=ON_DUE_DATE,
            effect=extra_effect,
        )


def test_immutable_accepted_and_writeoff_facts_cannot_be_rewritten() -> None:
    active = _active_partial()
    changed_acceptance = replace(
        active,
        revision=DebtRevision(active.revision.value + 1),
        updated_at=active.updated_at,
        accepted_at=active.accepted_at.replace(hour=active.accepted_at.hour + 1),  # type: ignore[union-attr]
    )
    with pytest.raises(DebtPaymentVoidTransitionError, match="immutable"):
        _transition(active, changed_acceptance, voided_at=active.updated_at)

    written_off = _written_off_partial()
    changed_reason = replace(
        written_off,
        revision=DebtRevision(written_off.revision.value + 1),
        updated_at=AFTER_DUE_DATE,
        written_off_reason=WriteOffReason.FRAUD_OR_ABUSE,
    )
    with pytest.raises(DebtPaymentVoidTransitionError, match="immutable"):
        _transition(written_off, changed_reason, voided_at=AFTER_DUE_DATE)


def test_pending_effect_is_closed_to_paid_payment_void_source() -> None:
    revision = DebtRevision(4)
    for source, from_status in (
        (DebtOverdueSource.BATCH, DebtStatus.PAID),
        (DebtOverdueSource.PAYMENT_VOID, DebtStatus.ACTIVE),
    ):
        with pytest.raises(DebtPaymentVoidTransitionError):
            PendingPaymentVoidOverdueEffect(
                source=source,
                from_status=from_status,
                overdue_revision=revision,
                occurred_at=AFTER_DUE_DATE,
            )
