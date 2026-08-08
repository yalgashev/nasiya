"""Debt lifecycle vocabulary frozen for M13."""

from enum import StrEnum
from typing import Final

__all__ = (
    "DebtStatus",
    "DebtExpirySource",
    "DebtTransitionEvent",
    "M13_PERSISTED_STATUSES",
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


class DebtTransitionEvent(StrEnum):
    CREATED = "debt.created"
    ACCEPTED = "debt.accepted"
    REJECTED = "debt.rejected"
    CANCELLED = "debt.cancelled"
    EXPIRED = "debt.expired"


class DebtExpirySource(StrEnum):
    INLINE = "inline"
    BATCH = "batch"


def parse_debt_status(value: str) -> DebtStatus:
    try:
        return DebtStatus(value)
    except (TypeError, ValueError):
        raise ValueError("Debt status is invalid") from None
