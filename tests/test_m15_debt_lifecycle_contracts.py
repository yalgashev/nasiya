from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.contracts import (
    DebtAggregate,
    DebtLifecycleError,
    DebtPaymentTransitionError,
)
from app.debt.enums import DebtOverdueSource, DebtPaymentFailure, DebtStatus
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
    UserId,
)

CREATED_AT = datetime(2026, 5, 1, 8, tzinfo=UTC)
ACCEPTED_AT = CREATED_AT + timedelta(hours=1)
OVERDUE_AT = datetime(2026, 5, 4, 19, tzinfo=UTC)


def _active_debt(*, discount_basis_points: int = 1000) -> DebtAggregate:
    original = OriginalAmountUZS(Decimal("1000"))
    discounted = DiscountedAmountUZS(
        Decimal("1000") if discount_basis_points == 0 else Decimal("900")
    )
    return DebtAggregate.create_pending(
        debt_id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=UserId(uuid4()),
        original_amount=original,
        discount_basis_points=DiscountBasisPoints(discount_basis_points),
        discounted_amount=discounted,
        due_date=date(2026, 5, 4),
        created_at=CREATED_AT,
    ).accept(now=ACCEPTED_AT)


@pytest.mark.parametrize("source", tuple(DebtOverdueSource))
@pytest.mark.parametrize("posted", (Decimal("0"), Decimal("899"), Decimal("900")))
def test_active_to_overdue_is_immutable_exactly_one_revision(
    source: DebtOverdueSource, posted: Decimal
) -> None:
    active = _active_debt()

    overdue = active.mark_overdue(
        now=OVERDUE_AT,
        source=source,
        posted_total_uzs=posted,
    )

    assert active.status is DebtStatus.ACTIVE
    assert active.overdue_at is None and active.overdue_revision is None
    assert overdue.status is DebtStatus.OVERDUE
    assert overdue.revision == DebtRevision(active.revision.value + 1)
    assert overdue.overdue_revision == overdue.revision
    assert overdue.overdue_at == overdue.updated_at == OVERDUE_AT
    assert overdue.accepted_at == active.accepted_at
    assert overdue.original_amount == active.original_amount
    assert overdue.discounted_amount == active.discounted_amount


def test_zero_discount_rollover_is_still_a_real_transition() -> None:
    active = _active_debt(discount_basis_points=0)

    overdue = active.mark_overdue(
        now=OVERDUE_AT,
        source=DebtOverdueSource.BATCH,
        posted_total_uzs=Decimal("0"),
    )

    assert overdue.status is DebtStatus.OVERDUE
    assert overdue.overdue_revision == DebtRevision(3)


def test_overdue_partial_and_full_payment_preserve_marker_and_revision_rules() -> None:
    overdue = _active_debt().mark_overdue(
        now=OVERDUE_AT,
        source=DebtOverdueSource.BATCH,
        posted_total_uzs=Decimal("100"),
    )
    payment_at = OVERDUE_AT + timedelta(hours=1)

    partial = overdue.record_payment(
        payment_amount_uzs=Decimal("1"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=overdue.revision,
        payment_created_at=payment_at,
    )
    full = overdue.record_payment(
        payment_amount_uzs=Decimal("900"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=overdue.revision,
        payment_created_at=payment_at,
    )

    assert partial.status is DebtStatus.OVERDUE
    assert partial.revision == DebtRevision(overdue.revision.value + 1)
    assert partial.paid_at is None
    assert full.status is DebtStatus.PAID
    assert full.revision == DebtRevision(overdue.revision.value + 1)
    assert full.paid_at == payment_at
    for transitioned in (partial, full):
        assert transitioned.overdue_at == overdue.overdue_at
        assert transitioned.overdue_revision == overdue.overdue_revision


def test_inline_rollover_then_payment_consumes_two_distinct_revisions() -> None:
    active = _active_debt()
    overdue = active.mark_overdue(
        now=OVERDUE_AT,
        source=DebtOverdueSource.INLINE_PAYMENT,
        posted_total_uzs=Decimal("0"),
    )
    paid = overdue.record_payment(
        payment_amount_uzs=Decimal("1000"),
        current_remaining_due_uzs=Decimal("1000"),
        expected_revision=overdue.revision,
        payment_created_at=OVERDUE_AT,
    )

    assert overdue.overdue_revision == DebtRevision(active.revision.value + 1)
    assert paid.revision == DebtRevision(active.revision.value + 2)
    assert paid.overdue_revision is not None
    assert paid.overdue_revision.value < paid.revision.value


def test_active_past_due_cannot_take_direct_discounted_payment() -> None:
    active = _active_debt()

    with pytest.raises(DebtPaymentTransitionError) as captured:
        active.record_payment(
            payment_amount_uzs=Decimal("900"),
            current_remaining_due_uzs=Decimal("900"),
            expected_revision=active.revision,
            payment_created_at=OVERDUE_AT,
        )

    assert captured.value.failure is DebtPaymentFailure.NOT_PAYABLE


@pytest.mark.parametrize(
    ("now", "source", "posted", "message"),
    (
        (
            datetime(2026, 5, 4, 18, 59, tzinfo=UTC),
            DebtOverdueSource.BATCH,
            Decimal("0"),
            "has not passed",
        ),
        (OVERDUE_AT, "batch", Decimal("0"), "source is invalid"),
        (OVERDUE_AT, DebtOverdueSource.BATCH, Decimal("901"), "ledger is incoherent"),
        (OVERDUE_AT, DebtOverdueSource.BATCH, Decimal("1.0"), "ledger is incoherent"),
        (OVERDUE_AT, DebtOverdueSource.BATCH, Decimal("-1"), "ledger is incoherent"),
    ),
)
def test_overdue_transition_denies_early_untyped_or_incoherent_input(
    now: datetime,
    source: object,
    posted: Decimal,
    message: str,
) -> None:
    with pytest.raises((DebtLifecycleError, ValueError), match=message):
        _active_debt().mark_overdue(
            now=now,
            source=source,  # type: ignore[arg-type]
            posted_total_uzs=posted,
        )


def test_overdue_metadata_pair_revision_and_timestamp_invariants_fail_closed() -> None:
    active = _active_debt()
    overdue = active.mark_overdue(
        now=OVERDUE_AT,
        source=DebtOverdueSource.BATCH,
        posted_total_uzs=Decimal("0"),
    )

    with pytest.raises(ValueError, match="present together"):
        replace(overdue, overdue_revision=None)
    with pytest.raises(ValueError, match="cannot exceed"):
        replace(overdue, overdue_revision=DebtRevision(99))
    with pytest.raises(ValueError, match="cannot carry overdue metadata"):
        replace(overdue, status=DebtStatus.ACTIVE)
    with pytest.raises(ValueError, match="cannot precede acceptance"):
        replace(
            overdue,
            overdue_at=CREATED_AT,
            updated_at=OVERDUE_AT,
        )


def test_m15_aggregate_has_no_reversal_or_written_off_transition() -> None:
    method_names = {name for name in dir(DebtAggregate) if not name.startswith("_")}

    assert {"mark_overdue", "record_payment"} <= method_names
    assert not {"reverse_clawback", "write_off", "settle_written_off"} & method_names
    assert {field.name for field in fields(DebtAggregate)} >= {
        "overdue_at",
        "overdue_revision",
    }
