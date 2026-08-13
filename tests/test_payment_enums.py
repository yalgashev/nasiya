import pytest

from app.payment.enums import (
    PaymentEvent,
    PaymentMethod,
    PaymentObjectType,
    PaymentVoidOutcome,
    PaymentVoidReason,
    parse_payment_method,
    parse_payment_void_reason,
)


def test_payment_method_event_and_object_vocabularies_are_exact() -> None:
    assert tuple(PaymentMethod) == (
        PaymentMethod.CASH,
        PaymentMethod.CARD,
        PaymentMethod.TRANSFER,
        PaymentMethod.OTHER,
    )
    assert tuple(PaymentEvent) == (PaymentEvent.RECORDED,)
    assert tuple(PaymentObjectType) == (PaymentObjectType.PAYMENT,)
    assert PaymentEvent.RECORDED.value == "payment.recorded"
    assert PaymentObjectType.PAYMENT.value == "payment"


def test_m18_payment_void_vocabulary_is_exact_and_closed() -> None:
    assert tuple(PaymentVoidReason) == (
        PaymentVoidReason.DUPLICATE_PAYMENT,
        PaymentVoidReason.INCORRECT_AMOUNT,
        PaymentVoidReason.INCORRECT_METHOD,
        PaymentVoidReason.PAYMENT_NOT_RECEIVED,
        PaymentVoidReason.WRONG_DEBT,
    )
    assert tuple(PaymentVoidOutcome) == (
        PaymentVoidOutcome.NEW,
        PaymentVoidOutcome.REPLAY,
    )
    assert {reason.value for reason in PaymentVoidReason} == {
        "duplicate_payment",
        "incorrect_amount",
        "incorrect_method",
        "payment_not_received",
        "wrong_debt",
    }
    assert {outcome.value for outcome in PaymentVoidOutcome} == {"new", "replay"}


def test_payment_method_parser_accepts_only_exact_persisted_values() -> None:
    for method in PaymentMethod:
        assert parse_payment_method(method.value) is method

    for malformed in ("CASH", " cash", "cash ", "bank", "", None, True):
        with pytest.raises(ValueError, match="Payment method is invalid"):
            parse_payment_method(malformed)  # type: ignore[arg-type]


def test_payment_void_reason_parser_accepts_only_closed_values() -> None:
    for reason in PaymentVoidReason:
        assert parse_payment_void_reason(reason.value) is reason

    for malformed in (
        "DUPLICATE_PAYMENT",
        "duplicate-payment",
        "refund",
        "unvoid",
        "correction",
        "",
        None,
        True,
    ):
        with pytest.raises(ValueError, match="Payment void reason is invalid"):
            parse_payment_void_reason(malformed)  # type: ignore[arg-type]
