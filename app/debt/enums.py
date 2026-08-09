"""Debt lifecycle vocabulary carried from M13 into the bounded M14 domain."""

from enum import StrEnum
from typing import Final

__all__ = (
    "DebtStatus",
    "DebtExpirySource",
    "DebtBalanceBasis",
    "DebtOverdueSource",
    "DebtPaymentFailure",
    "DebtTransitionEvent",
    "M13_PERSISTED_STATUSES",
    "M14_PERSISTED_STATUSES",
    "M15_PERSISTED_STATUSES",
    "parse_debt_status",
)


class DebtStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAID = "paid"
    OVERDUE = "overdue"
    WRITTEN_OFF = "written_off"
    WRITTEN_OFF_SETTLED = "written_off_settled"


M13_PERSISTED_STATUSES: Final[frozenset[DebtStatus]] = frozenset(
    {
        DebtStatus.PENDING,
        DebtStatus.ACTIVE,
        DebtStatus.REJECTED,
        DebtStatus.CANCELLED,
        DebtStatus.EXPIRED,
    }
)


M14_PERSISTED_STATUSES: Final[frozenset[DebtStatus]] = frozenset(
    {
        *M13_PERSISTED_STATUSES,
        DebtStatus.PAID,
    }
)


M15_PERSISTED_STATUSES: Final[frozenset[DebtStatus]] = frozenset(
    {
        *M14_PERSISTED_STATUSES,
        DebtStatus.OVERDUE,
    }
)


class DebtBalanceBasis(StrEnum):
    DISCOUNTED = "discounted"
    ORIGINAL = "original"


class DebtOverdueSource(StrEnum):
    INLINE_PAYMENT = "inline_payment"
    BATCH = "batch"


class DebtTransitionEvent(StrEnum):
    CREATED = "debt.created"
    ACCEPTED = "debt.accepted"
    REJECTED = "debt.rejected"
    CANCELLED = "debt.cancelled"
    EXPIRED = "debt.expired"
    PAID = "debt.paid"


class DebtPaymentFailure(StrEnum):
    NOT_PAYABLE = "DEBT_NOT_PAYABLE"
    CHANGED = "DEBT_CHANGED"
    AMOUNT_EXCEEDS_BALANCE = "PAYMENT_AMOUNT_EXCEEDS_BALANCE"


class DebtExpirySource(StrEnum):
    INLINE = "inline"
    BATCH = "batch"


def parse_debt_status(value: str) -> DebtStatus:
    try:
        return DebtStatus(value)
    except (TypeError, ValueError):
        raise ValueError("Debt status is invalid") from None
