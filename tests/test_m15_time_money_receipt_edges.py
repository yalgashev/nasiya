from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.debt.business_time import is_effectively_overdue
from app.debt.enums import DebtBalanceBasis, DebtStatus
from app.debt.values import (
    MAX_DEBT_AMOUNT_UZS,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    calculate_discounted_amount,
)
from app.payment.contracts import (
    IncoherentPaymentHistoryError,
    resolve_current_balance_basis,
    resolve_historical_balance_basis,
)
from app.payment.values import (
    PaymentAmountUZS,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
    calculate_clawback_increase,
    calculate_remaining_due_for_basis,
    require_payment_amount_within_remaining,
)


def test_tashkent_due_day_edges_are_exact_across_utc_calendar_boundaries() -> None:
    due = date(2026, 1, 1)
    local_due_day_start = datetime(2025, 12, 31, 19, tzinfo=UTC)
    local_due_day_last = datetime(2026, 1, 1, 18, 59, 59, 999999, tzinfo=UTC)
    next_local_instant = datetime(2026, 1, 1, 19, tzinfo=UTC)

    for on_time in (local_due_day_start, local_due_day_last):
        assert not is_effectively_overdue(
            status=DebtStatus.ACTIVE,
            due_date=due,
            server_now=on_time,
        )
        assert (
            resolve_current_balance_basis(
                status=DebtStatus.ACTIVE,
                due_date=due,
                server_now=on_time,
                overdue_revision=None,
            )
            is DebtBalanceBasis.DISCOUNTED
        )

    assert is_effectively_overdue(
        status=DebtStatus.ACTIVE,
        due_date=due,
        server_now=next_local_instant,
    )
    assert (
        resolve_current_balance_basis(
            status=DebtStatus.ACTIVE,
            due_date=due,
            server_now=next_local_instant,
            overdue_revision=None,
        )
        is DebtBalanceBasis.ORIGINAL
    )


def test_whole_uzs_clawback_matrix_has_no_float_rounding_or_max_clamp() -> None:
    maximum = OriginalAmountUZS(MAX_DEBT_AMOUNT_UZS)
    undiscounted = calculate_discounted_amount(maximum, DiscountBasisPoints(0))
    highest_persistable = calculate_discounted_amount(
        maximum, DiscountBasisPoints(9999)
    )

    assert undiscounted == DiscountedAmountUZS(MAX_DEBT_AMOUNT_UZS)
    assert highest_persistable == DiscountedAmountUZS(Decimal("100000000"))
    assert calculate_clawback_increase(
        original_amount=maximum,
        discounted_amount=highest_persistable,
        posted_total=PostedPaymentTotalUZS(Decimal("1")),
    ).value == Decimal("999900000000")

    with pytest.raises(ValueError, match="at least 1 UZS"):
        calculate_discounted_amount(maximum, DiscountBasisPoints(10000))

    original_one = OriginalAmountUZS(Decimal("1"))
    discounted_one = DiscountedAmountUZS(Decimal("1"))
    assert calculate_clawback_increase(
        original_amount=original_one,
        discounted_amount=discounted_one,
        posted_total=PostedPaymentTotalUZS(Decimal("0")),
    ).value == Decimal("0")

    posted = PostedPaymentTotalUZS(Decimal("1"))
    assert calculate_remaining_due_for_basis(
        basis=DebtBalanceBasis.DISCOUNTED,
        original_amount=maximum,
        discounted_amount=highest_persistable,
        posted_total=posted,
    ).value == Decimal("99999999")
    assert calculate_remaining_due_for_basis(
        basis=DebtBalanceBasis.ORIGINAL,
        original_amount=maximum,
        discounted_amount=highest_persistable,
        posted_total=posted,
    ).value == Decimal("999999999999")


def test_one_uzs_under_exact_and_over_payment_edges_are_not_clamped() -> None:
    remaining = RemainingDueUZS(Decimal("100"))

    assert require_payment_amount_within_remaining(
        amount=PaymentAmountUZS(Decimal("99")), remaining_due=remaining
    ).value == Decimal("99")
    assert require_payment_amount_within_remaining(
        amount=PaymentAmountUZS(Decimal("100")), remaining_due=remaining
    ).value == Decimal("100")
    with pytest.raises(ValueError, match="exceeds remaining"):
        require_payment_amount_within_remaining(
            amount=PaymentAmountUZS(Decimal("101")), remaining_due=remaining
        )


@pytest.mark.parametrize(
    ("payment_revision", "overdue_revision", "expected"),
    (
        (1, 3, DebtBalanceBasis.DISCOUNTED),
        (2, 3, DebtBalanceBasis.DISCOUNTED),
        (4, 3, DebtBalanceBasis.ORIGINAL),
        (9, 3, DebtBalanceBasis.ORIGINAL),
    ),
)
def test_receipt_marker_gaps_are_revision_not_timestamp_derived(
    payment_revision: int,
    overdue_revision: int,
    expected: DebtBalanceBasis,
) -> None:
    assert (
        resolve_historical_balance_basis(
            payment_revision=DebtRevision(payment_revision),
            overdue_revision=DebtRevision(overdue_revision),
        )
        is expected
    )


def test_receipt_marker_collision_fails_closed() -> None:
    with pytest.raises(IncoherentPaymentHistoryError, match="cannot equal"):
        resolve_historical_balance_basis(
            payment_revision=DebtRevision(3),
            overdue_revision=DebtRevision(3),
        )
