from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.contracts import DebtAggregate, DebtLifecycleError, WriteOffReason
from app.debt.enums import DebtPaymentFailure, DebtStatus
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
)


def _overdue() -> DebtAggregate:
    return DebtAggregate(
        id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=uuid4(),
        original_amount=OriginalAmountUZS(Decimal("100000")),
        discount_basis_points=DiscountBasisPoints(1000),
        discounted_amount=DiscountedAmountUZS(Decimal("90000")),
        due_date=date(2026, 1, 5),
        pending_expires_at=datetime(2026, 1, 4, tzinfo=UTC),
        status=DebtStatus.OVERDUE,
        revision=DebtRevision(3),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 6, tzinfo=UTC),
        accepted_at=datetime(2026, 1, 2, tzinfo=UTC),
        overdue_at=datetime(2026, 1, 6, tzinfo=UTC),
        overdue_revision=DebtRevision(3),
    )


def _written_off() -> DebtAggregate:
    return _overdue().mark_written_off(
        now=datetime(2026, 1, 6, 1, tzinfo=UTC),
        actor_user_id=uuid4(),
        reason=WriteOffReason.LEGAL_OR_COMPLIANCE,
        posted_total_uzs=Decimal("1000"),
        expected_revision=DebtRevision(3),
    )


def test_write_off_preserves_source_and_sets_exact_four_evidence_fields() -> None:
    source = _overdue()
    actor_id = uuid4()
    result = source.mark_written_off(
        now=datetime(2026, 1, 6, 1, tzinfo=UTC),
        actor_user_id=actor_id,
        reason=WriteOffReason.FRAUD_OR_ABUSE,
        posted_total_uzs=Decimal("99999"),
        expected_revision=source.revision,
    )
    assert result.status is DebtStatus.WRITTEN_OFF
    assert result.revision == DebtRevision(4)
    assert result.written_off_revision == result.revision
    assert result.written_off_reason is WriteOffReason.FRAUD_OR_ABUSE
    assert result.written_off_by_user_id == actor_id
    assert result.written_off_at == datetime(2026, 1, 6, 1, tzinfo=UTC)
    assert result.accepted_at == source.accepted_at
    assert result.overdue_at == source.overdue_at
    assert result.overdue_revision == source.overdue_revision
    assert result.paid_at is None


@pytest.mark.parametrize("posted_total", [Decimal("100000"), Decimal("100001")])
def test_write_off_requires_positive_original_basis_remaining(
    posted_total: Decimal,
) -> None:
    with pytest.raises(DebtLifecycleError, match="balance"):
        _overdue().mark_written_off(
            now=datetime(2026, 1, 7, tzinfo=UTC),
            actor_user_id=uuid4(),
            reason=WriteOffReason.COLLECTION_EXHAUSTED,
            posted_total_uzs=posted_total,
            expected_revision=DebtRevision(3),
        )


def test_partial_recovery_keeps_markers_and_full_recovery_sets_separate_pair() -> None:
    written_off = _written_off()
    partial = written_off.record_written_off_recovery(
        payment_amount_uzs=Decimal("1"),
        current_remaining_due_uzs=Decimal("99000"),
        expected_revision=written_off.revision,
        payment_created_at=datetime(2026, 1, 7, tzinfo=UTC),
    )
    assert partial.status is DebtStatus.WRITTEN_OFF
    assert partial.written_off_at == written_off.written_off_at
    assert partial.written_off_settled_at is None
    settled = partial.record_written_off_recovery(
        payment_amount_uzs=Decimal("98999"),
        current_remaining_due_uzs=Decimal("98999"),
        expected_revision=partial.revision,
        payment_created_at=datetime(2026, 1, 8, tzinfo=UTC),
    )
    assert settled.status is DebtStatus.WRITTEN_OFF_SETTLED
    assert settled.written_off_settled_revision == settled.revision
    assert settled.written_off_settled_at == datetime(2026, 1, 8, tzinfo=UTC)
    assert settled.paid_at is None


def test_invalid_partial_metadata_reverse_and_overpay_fail_closed() -> None:
    written_off = _written_off()
    with pytest.raises(ValueError, match="present together"):
        replace(written_off, written_off_reason=None)
    with pytest.raises(ValueError, match="settlement metadata"):
        replace(written_off, written_off_settled_at=written_off.written_off_at)
    with pytest.raises(ValueError, match="current revision"):
        replace(
            written_off,
            status=DebtStatus.WRITTEN_OFF_SETTLED,
            revision=DebtRevision(6),
            written_off_settled_at=written_off.written_off_at,
            written_off_settled_revision=DebtRevision(5),
        )
    with pytest.raises(Exception) as exc_info:
        written_off.record_written_off_recovery(
            payment_amount_uzs=Decimal("99001"),
            current_remaining_due_uzs=Decimal("99000"),
            expected_revision=written_off.revision,
            payment_created_at=datetime(2026, 1, 7, tzinfo=UTC),
        )
    assert exc_info.value.failure is DebtPaymentFailure.AMOUNT_EXCEEDS_BALANCE


def test_effective_only_and_terminal_states_cannot_be_written_off_or_reversed() -> None:
    source = _overdue()
    active = replace(
        source,
        status=DebtStatus.ACTIVE,
        overdue_at=None,
        overdue_revision=None,
        updated_at=datetime(2026, 1, 5, tzinfo=UTC),
    )
    with pytest.raises(DebtLifecycleError, match="not writable"):
        active.mark_written_off(
            now=datetime(2026, 1, 7, tzinfo=UTC),
            actor_user_id=uuid4(),
            reason=WriteOffReason.COLLECTION_EXHAUSTED,
            posted_total_uzs=Decimal("0"),
            expected_revision=active.revision,
        )
    settled = _written_off().record_written_off_recovery(
        payment_amount_uzs=Decimal("99000"),
        current_remaining_due_uzs=Decimal("99000"),
        expected_revision=DebtRevision(4),
        payment_created_at=datetime(2026, 1, 7, tzinfo=UTC),
    )
    with pytest.raises(Exception) as exc_info:
        settled.record_written_off_recovery(
            payment_amount_uzs=Decimal("1"),
            current_remaining_due_uzs=Decimal("1"),
            expected_revision=settled.revision,
            payment_created_at=datetime(2026, 1, 8, tzinfo=UTC),
        )
    assert exc_info.value.failure is DebtPaymentFailure.NOT_PAYABLE
