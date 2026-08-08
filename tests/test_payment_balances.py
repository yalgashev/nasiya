from dataclasses import fields
from decimal import Decimal

import pytest

from app.debt.enums import DebtStatus
from app.debt.values import DiscountedAmountUZS, OriginalAmountUZS
from app.payment.values import (
    IncoherentPaymentLedgerError,
    PaymentExposureUZS,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
    calculate_payment_exposure,
    calculate_remaining_due,
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
        (DebtStatus.ACTIVE, "1000", "1000", "1000", "0", 1),
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


def test_balance_values_are_zero_inclusive_decimal_only_and_redacted() -> None:
    value_types = (
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
    "future_status",
    (DebtStatus.OVERDUE, DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF_SETTLED),
)
def test_future_statuses_cannot_reach_m14_exposure_or_open_count(
    future_status: DebtStatus,
) -> None:
    with pytest.raises(ValueError, match="outside the M14 persisted subset"):
        calculate_payment_exposure(
            status=future_status,
            original_amount=OriginalAmountUZS(Decimal("1000")),
            discounted_amount=DiscountedAmountUZS(Decimal("1000")),
            posted_total=PostedPaymentTotalUZS(Decimal("0")),
        )
    with pytest.raises(ValueError, match="outside the M14 persisted subset"):
        open_debt_count_contribution(future_status)


def test_exposure_also_fails_closed_for_an_incoherent_posted_total() -> None:
    with pytest.raises(IncoherentPaymentLedgerError):
        calculate_payment_exposure(
            status=DebtStatus.ACTIVE,
            original_amount=OriginalAmountUZS(Decimal("1000")),
            discounted_amount=DiscountedAmountUZS(Decimal("600")),
            posted_total=PostedPaymentTotalUZS(Decimal("601")),
        )
