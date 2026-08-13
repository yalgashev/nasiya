"""Typed, closed payment vocabulary for the M14 domain boundary."""

from enum import StrEnum

__all__ = (
    "PaymentEvent",
    "PaymentMethod",
    "PaymentObjectType",
    "PaymentVoidOutcome",
    "PaymentVoidReason",
    "parse_payment_method",
    "parse_payment_void_reason",
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


class PaymentVoidReason(StrEnum):
    DUPLICATE_PAYMENT = "duplicate_payment"
    INCORRECT_AMOUNT = "incorrect_amount"
    INCORRECT_METHOD = "incorrect_method"
    PAYMENT_NOT_RECEIVED = "payment_not_received"
    WRONG_DEBT = "wrong_debt"


class PaymentVoidOutcome(StrEnum):
    NEW = "new"
    REPLAY = "replay"


def parse_payment_method(value: str) -> PaymentMethod:
    try:
        return PaymentMethod(value)
    except (TypeError, ValueError):
        raise ValueError("Payment method is invalid") from None


def parse_payment_void_reason(value: str) -> PaymentVoidReason:
    try:
        return PaymentVoidReason(value)
    except (TypeError, ValueError):
        raise ValueError("Payment void reason is invalid") from None
