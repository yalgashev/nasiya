import pytest

from app.payment.enums import (
    PaymentEvent,
    PaymentMethod,
    PaymentObjectType,
    parse_payment_method,
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


def test_payment_method_parser_accepts_only_exact_persisted_values() -> None:
    for method in PaymentMethod:
        assert parse_payment_method(method.value) is method

    for malformed in ("CASH", " cash", "cash ", "bank", "", None, True):
        with pytest.raises(ValueError, match="Payment method is invalid"):
            parse_payment_method(malformed)  # type: ignore[arg-type]
