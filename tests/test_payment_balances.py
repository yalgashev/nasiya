from dataclasses import fields
from decimal import Decimal

import pytest

from app.debt.enums import DebtBalanceBasis, DebtStatus
from app.debt.values import DiscountedAmountUZS, OriginalAmountUZS
from app.payment.values import (
    ClawbackIncreaseUZS,
    IncoherentPaymentLedgerError,
    PaymentExposureUZS,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
    calculate_clawback_increase,
    calculate_discounted_remaining_due,
    calculate_overdue_remaining_due,
    calculate_payment_exposure,
    calculate_remaining_due,
    calculate_remaining_due_for_basis,
    open_debt_count_contribution,
)


@pytest.mark.parametrize(
    ("discounted", "posted", "expected"),
    (
        ("1000", "0", "1000"),
        ("1000", "1", "999"),
        ("1000", "400", "600"),
        ("1000", "999", "1"),
        ("1000", "1000", "0"),
        ("1", "0", "1"),
        ("1", "1", "0"),
    ),
)
def test_remaining_due_matrix_uses_discounted_amount_only(
    discounted: str, posted: str, expected: str
) -> None:
    result = calculate_remaining_due(
        discounted_amount=DiscountedAmountUZS(Decimal(discounted)),
        posted_total=PostedPaymentTotalUZS(Decimal(posted)),
    )

    assert result == RemainingDueUZS(Decimal(expected))


def test_remaining_due_fails_closed_when_persisted_total_is_incoherent() -> None:
    with pytest.raises(
        IncoherentPaymentLedgerError,
        match="Posted payment total exceeds discounted amount",
    ):
        calculate_remaining_due(
            discounted_amount=DiscountedAmountUZS(Decimal("600")),
            posted_total=PostedPaymentTotalUZS(Decimal("601")),
        )


@pytest.mark.parametrize(
    (
        "status",
        "original",
        "discounted",
        "posted",
        "expected_exposure",
        "expected_count",
    ),
    (
        (DebtStatus.PENDING, "1000", "1000", "0", "1000", 1),
        (DebtStatus.ACTIVE, "1000", "1000", "0", "1000", 1),
        (DebtStatus.ACTIVE, "1000", "1000", "1", "999", 1),
        (DebtStatus.ACTIVE, "1000", "1000", "400", "600", 1),
        (DebtStatus.OVERDUE, "1000", "600", "600", "400", 1),
        (DebtStatus.OVERDUE, "1000", "600", "999", "1", 1),
        (DebtStatus.PAID, "1000", "1000", "1000", "0", 0),
        (DebtStatus.REJECTED, "1000", "1000", "0", "0", 0),
        (DebtStatus.CANCELLED, "1000", "1000", "0", "0", 0),
        (DebtStatus.EXPIRED, "1000", "1000", "0", "0", 0),
    ),
)
def test_original_basis_exposure_and_open_count_matrix(
    status: DebtStatus,
    original: str,
    discounted: str,
    posted: str,
    expected_exposure: str,
    expected_count: int,
) -> None:
    exposure = calculate_payment_exposure(
        status=status,
        original_amount=OriginalAmountUZS(Decimal(original)),
        discounted_amount=DiscountedAmountUZS(Decimal(discounted)),
        posted_total=PostedPaymentTotalUZS(Decimal(posted)),
    )

    assert exposure == PaymentExposureUZS(Decimal(expected_exposure))
    assert open_debt_count_contribution(status) == expected_count


def test_discounted_remaining_and_original_exposure_never_share_a_basis() -> None:
    posted_total = PostedPaymentTotalUZS(Decimal("200"))

    remaining = calculate_remaining_due(
        discounted_amount=DiscountedAmountUZS(Decimal("600")),
        posted_total=posted_total,
    )
    exposure = calculate_payment_exposure(
        status=DebtStatus.ACTIVE,
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discounted_amount=DiscountedAmountUZS(Decimal("600")),
        posted_total=posted_total,
    )

    assert remaining == RemainingDueUZS(Decimal("400"))
    assert exposure == PaymentExposureUZS(Decimal("800"))


def test_status_aware_remaining_and_clawback_matrix_has_no_max_clamp() -> None:
    original = OriginalAmountUZS(Decimal("1000"))
    discounted = DiscountedAmountUZS(Decimal("600"))

    assert calculate_discounted_remaining_due(
        discounted_amount=discounted,
        posted_total=PostedPaymentTotalUZS(Decimal("200")),
    ) == RemainingDueUZS(Decimal("400"))
    assert calculate_overdue_remaining_due(
        original_amount=original,
        posted_total=PostedPaymentTotalUZS(Decimal("700")),
    ) == RemainingDueUZS(Decimal("300"))
    assert calculate_remaining_due_for_basis(
        basis=DebtBalanceBasis.ORIGINAL,
        original_amount=original,
        discounted_amount=discounted,
        posted_total=PostedPaymentTotalUZS(Decimal("700")),
    ) == RemainingDueUZS(Decimal("300"))
    assert calculate_clawback_increase(
        original_amount=original,
        discounted_amount=discounted,
        posted_total=PostedPaymentTotalUZS(Decimal("200")),
    ) == ClawbackIncreaseUZS(Decimal("400"))

    with pytest.raises(IncoherentPaymentLedgerError):
        calculate_discounted_remaining_due(
            discounted_amount=discounted,
            posted_total=PostedPaymentTotalUZS(Decimal("601")),
        )
    with pytest.raises(IncoherentPaymentLedgerError):
        calculate_overdue_remaining_due(
            original_amount=original,
            posted_total=PostedPaymentTotalUZS(Decimal("1001")),
        )


def test_zero_discount_has_zero_clawback_and_identical_bases() -> None:
    original = OriginalAmountUZS(Decimal("1000"))
    discounted = DiscountedAmountUZS(Decimal("1000"))
    posted = PostedPaymentTotalUZS(Decimal("250"))

    assert calculate_clawback_increase(
        original_amount=original,
        discounted_amount=discounted,
        posted_total=posted,
    ) == ClawbackIncreaseUZS(Decimal("0"))
    assert calculate_remaining_due_for_basis(
        basis=DebtBalanceBasis.DISCOUNTED,
        original_amount=original,
        discounted_amount=discounted,
        posted_total=posted,
    ) == calculate_remaining_due_for_basis(
        basis=DebtBalanceBasis.ORIGINAL,
        original_amount=original,
        discounted_amount=discounted,
        posted_total=posted,
    )


def test_many_partial_decimal_sums_and_near_full_discount_remain_exact() -> None:
    discounted = DiscountedAmountUZS(Decimal("1000"))
    posted = Decimal("0")
    for amount in (Decimal("1"),) * 998 + (Decimal("2"),):
        posted += amount
        remaining = calculate_remaining_due(
            discounted_amount=discounted,
            posted_total=PostedPaymentTotalUZS(posted),
        )
        assert remaining.value == Decimal("1000") - posted

    near_full_discount_remaining = calculate_remaining_due(
        discounted_amount=DiscountedAmountUZS(Decimal("1")),
        posted_total=PostedPaymentTotalUZS(Decimal("0")),
    )
    near_full_discount_exposure = calculate_payment_exposure(
        status=DebtStatus.ACTIVE,
        original_amount=OriginalAmountUZS(Decimal("10000")),
        discounted_amount=DiscountedAmountUZS(Decimal("1")),
        posted_total=PostedPaymentTotalUZS(Decimal("0")),
    )
    assert near_full_discount_remaining.value == Decimal("1")
    assert near_full_discount_exposure.value == Decimal("10000")


def test_balance_values_are_zero_inclusive_decimal_only_and_redacted() -> None:
    value_types = (
        ClawbackIncreaseUZS,
        PostedPaymentTotalUZS,
        RemainingDueUZS,
        PaymentExposureUZS,
    )
    for value_type in value_types:
        zero = value_type(Decimal("0"))
        assert zero.value == Decimal("0")
        assert tuple(field.name for field in fields(zero)) == ("value",)
        assert "0" not in repr(zero)
        assert "0" not in str(zero)

        for invalid in (
            Decimal("-1"),
            Decimal("1.0"),
            Decimal("1E+3"),
            Decimal("NaN"),
            Decimal("Infinity"),
        ):
            with pytest.raises(ValueError):
                value_type(invalid)
        for invalid in (0, 0.0, False, "0"):
            with pytest.raises(TypeError, match="must be a Decimal"):
                value_type(invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("status", "posted"),
    (
        (DebtStatus.WRITTEN_OFF, "0"),
        (DebtStatus.WRITTEN_OFF_SETTLED, "1000"),
    ),
)
def test_written_off_states_are_outside_tt_open_exposure_and_count(
    status: DebtStatus,
    posted: str,
) -> None:
    exposure = calculate_payment_exposure(
        status=status,
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discounted_amount=DiscountedAmountUZS(Decimal("1000")),
        posted_total=PostedPaymentTotalUZS(Decimal(posted)),
    )
    assert exposure.value == Decimal("0")
    assert open_debt_count_contribution(status) == 0


def test_exposure_also_fails_closed_for_an_incoherent_posted_total() -> None:
    with pytest.raises(IncoherentPaymentLedgerError):
        calculate_payment_exposure(
            status=DebtStatus.ACTIVE,
            original_amount=OriginalAmountUZS(Decimal("1000")),
            discounted_amount=DiscountedAmountUZS(Decimal("600")),
            posted_total=PostedPaymentTotalUZS(Decimal("601")),
        )


@pytest.mark.parametrize(
    ("status", "discounted", "posted"),
    (
        (DebtStatus.PENDING, "600", "1"),
        (DebtStatus.REJECTED, "600", "1"),
        (DebtStatus.CANCELLED, "600", "1"),
        (DebtStatus.EXPIRED, "600", "1"),
        (DebtStatus.ACTIVE, "600", "601"),
        (DebtStatus.ACTIVE, "600", "600"),
        (DebtStatus.OVERDUE, "600", "1000"),
        (DebtStatus.PAID, "600", "999"),
    ),
)
def test_status_specific_posted_total_limits_fail_closed_without_clamp(
    status: DebtStatus,
    discounted: str,
    posted: str,
) -> None:
    with pytest.raises(IncoherentPaymentLedgerError):
        calculate_payment_exposure(
            status=status,
            original_amount=OriginalAmountUZS(Decimal("1000")),
            discounted_amount=DiscountedAmountUZS(Decimal(discounted)),
            posted_total=PostedPaymentTotalUZS(Decimal(posted)),
        )
