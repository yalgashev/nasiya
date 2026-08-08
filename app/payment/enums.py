"""Typed, closed payment vocabulary for the M14 domain boundary."""

from enum import StrEnum

__all__ = (
    "PaymentEvent",
    "PaymentMethod",
    "PaymentObjectType",
    "parse_payment_method",
)


class PaymentMethod(StrEnum):
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"
    OTHER = "other"


class PaymentEvent(StrEnum):
    RECORDED = "payment.recorded"


class PaymentObjectType(StrEnum):
    PAYMENT = "payment"


def parse_payment_method(value: str) -> PaymentMethod:
    try:
        return PaymentMethod(value)
    except (TypeError, ValueError):
        raise ValueError("Payment method is invalid") from None
