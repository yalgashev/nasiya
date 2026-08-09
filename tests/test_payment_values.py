from decimal import Decimal
from uuid import uuid4

import pytest

from app.payment.values import (
    MAX_PAYMENT_AMOUNT_UZS,
    PaymentAmountUZS,
    PaymentId,
    RemainingDueUZS,
    parse_payment_amount_uzs,
    require_payment_amount_within_remaining,
)


def test_payment_identifier_is_redacted_and_value_based() -> None:
    raw_identifier = uuid4()
    payment_id = PaymentId(raw_identifier)

    assert payment_id.as_uuid() == raw_identifier
    assert payment_id == PaymentId(raw_identifier)
    assert hash(payment_id) == hash(PaymentId(raw_identifier))
    assert str(raw_identifier) not in repr(payment_id)
    assert str(raw_identifier) not in str(payment_id)

    with pytest.raises(ValueError, match="Payment identity is invalid"):
        PaymentId("not-a-uuid")  # type: ignore[arg-type]


def test_payment_amount_is_decimal_bounded_whole_uzs_and_redacted() -> None:
    amount = PaymentAmountUZS(Decimal("1"))
    assert amount == PaymentAmountUZS(Decimal("1"))
    assert hash(amount) == hash(PaymentAmountUZS(Decimal("1")))
    assert PaymentAmountUZS(MAX_PAYMENT_AMOUNT_UZS).value == MAX_PAYMENT_AMOUNT_UZS
    assert "1" not in repr(amount)
    assert "1" not in str(amount)

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
            PaymentAmountUZS(value)

    for value in (1, 1.0, True, "1"):
        with pytest.raises(TypeError, match="must be a Decimal"):
            PaymentAmountUZS(value)  # type: ignore[arg-type]


def test_payment_amount_parser_accepts_only_ascii_whole_uzs() -> None:
    assert parse_payment_amount_uzs("0001") == PaymentAmountUZS(Decimal("1"))
    assert parse_payment_amount_uzs(str(MAX_PAYMENT_AMOUNT_UZS)).value == (
        MAX_PAYMENT_AMOUNT_UZS
    )

    for malformed in (
        "",
        " 1",
        "1 ",
        "1 0",
        "+1",
        "-1",
        "1.0",
        "1e3",
        "1E3",
        "1_000",
        "1,000",
        "1,0",
        ".1",
        "1.00",
        "1E+0",
        "１２",
        "١٢",
        "\t1",
        "1\n",
        "0",
        "1000000000001",
        None,
        True,
        1,
        1.0,
    ):
        with pytest.raises(ValueError, match="Payment amount"):
            parse_payment_amount_uzs(malformed)  # type: ignore[arg-type]


def test_remaining_bound_is_a_separate_pure_payment_amount_decision() -> None:
    amount = PaymentAmountUZS(Decimal("100"))

    assert (
        require_payment_amount_within_remaining(
            amount=amount, remaining_due=RemainingDueUZS(Decimal("100"))
        )
        is amount
    )

    with pytest.raises(ValueError, match="exceeds remaining due"):
        require_payment_amount_within_remaining(
            amount=amount, remaining_due=RemainingDueUZS(Decimal("99"))
        )
    with pytest.raises(TypeError, match="Payment amount must be"):
        require_payment_amount_within_remaining(
            amount=Decimal("100"),  # type: ignore[arg-type]
            remaining_due=RemainingDueUZS(Decimal("100")),
        )
    with pytest.raises(TypeError, match="Remaining due must be a RemainingDueUZS"):
        require_payment_amount_within_remaining(
            amount=amount,
            remaining_due=Decimal("100"),  # type: ignore[arg-type]
        )
