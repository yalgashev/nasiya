from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.values import (
    MAX_DEBT_AMOUNT_UZS,
    CustomerId,
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    DiscountPercent,
    OriginalAmountUZS,
    ShopCustomerId,
    ShopId,
    UserId,
    calculate_discounted_amount,
    parse_discount_percent,
    parse_original_amount_uzs,
)


def test_debt_identifier_is_redacted_and_reuses_existing_typed_adapters() -> None:
    raw_identifier = uuid4()
    debt_id = DebtId(raw_identifier)

    assert debt_id.as_uuid() == raw_identifier
    assert str(raw_identifier) not in repr(debt_id)
    assert str(raw_identifier) not in str(debt_id)
    assert ShopId(raw_identifier) == raw_identifier
    assert UserId(raw_identifier) == raw_identifier
    assert CustomerId(raw_identifier) == raw_identifier
    assert ShopCustomerId(raw_identifier).as_uuid() == raw_identifier
    assert DebtRevision(1).value == 1

    with pytest.raises(ValueError, match="Debt identity is invalid"):
        DebtId("not-a-uuid")  # type: ignore[arg-type]


def test_original_amount_and_discounted_amount_are_bounded_whole_uzs() -> None:
    assert OriginalAmountUZS(Decimal("1")).value == Decimal("1")
    assert OriginalAmountUZS(MAX_DEBT_AMOUNT_UZS).value == MAX_DEBT_AMOUNT_UZS
    assert DiscountedAmountUZS(Decimal("1")).value == Decimal("1")

    for value in (
        Decimal("0"),
        Decimal("-1"),
        Decimal("1.0"),
        Decimal("1E+3"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1000000000001"),
    ):
        with pytest.raises(ValueError):
            OriginalAmountUZS(value)
        with pytest.raises(ValueError):
            DiscountedAmountUZS(value)

    with pytest.raises(TypeError, match="must be a Decimal"):
        OriginalAmountUZS(1)  # type: ignore[arg-type]

    assert parse_original_amount_uzs("0001") == OriginalAmountUZS(Decimal("1"))
    for malformed in (
        "",
        " 1",
        "1 ",
        "+1",
        "-1",
        "1.0",
        "1e3",
        "1_000",
        "1,000",
        "１２",
        "0",
        None,
    ):
        with pytest.raises(ValueError):
            parse_original_amount_uzs(malformed)  # type: ignore[arg-type]


def test_discount_percent_uses_exact_ascii_grammar_and_basis_points() -> None:
    assert parse_discount_percent("0").as_basis_points() == DiscountBasisPoints(0)
    assert parse_discount_percent("0.01").as_basis_points() == DiscountBasisPoints(1)
    assert parse_discount_percent("12.5").as_basis_points() == DiscountBasisPoints(1250)
    assert parse_discount_percent("100.00").as_basis_points() == DiscountBasisPoints(
        10000
    )

    for malformed in (
        "",
        " 1",
        "1 ",
        "+1",
        "-1",
        "1.000",
        "1e1",
        "1E1",
        "1_000",
        "1,000",
        "１２",
        "100.01",
        None,
        1,
        1.0,
    ):
        with pytest.raises(ValueError):
            parse_discount_percent(malformed)  # type: ignore[arg-type]

    for value in (Decimal("0.001"), Decimal("-0.01"), Decimal("100.01")):
        with pytest.raises(ValueError):
            DiscountPercent(value)
    with pytest.raises(TypeError, match="must be a Decimal"):
        DiscountPercent(1)  # type: ignore[arg-type]

    for value in (-1, 10001, True, 1.0, "1"):
        with pytest.raises(ValueError, match="between 0 and 10000"):
            DiscountBasisPoints(value)  # type: ignore[arg-type]


def test_discounted_amount_uses_round_half_up_and_requires_at_least_one_uzs() -> None:
    assert calculate_discounted_amount(
        OriginalAmountUZS(Decimal("101")), DiscountBasisPoints(50)
    ) == DiscountedAmountUZS(Decimal("100"))
    assert calculate_discounted_amount(
        OriginalAmountUZS(Decimal("1")), DiscountBasisPoints(5000)
    ) == DiscountedAmountUZS(Decimal("1"))
    assert calculate_discounted_amount(
        OriginalAmountUZS(Decimal("1000000")), DiscountBasisPoints(1000)
    ) == DiscountedAmountUZS(Decimal("900000"))

    with pytest.raises(ValueError, match="at least 1 UZS"):
        calculate_discounted_amount(
            OriginalAmountUZS(Decimal("1")), DiscountBasisPoints(10000)
        )
