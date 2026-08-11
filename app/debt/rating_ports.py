"""Debt-local structural boundary for one pending overdue source effect."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from app.debt.business_time import tashkent_business_date
from app.debt.values import DebtId
from app.shop_customer.values import ShopCustomerId

if TYPE_CHECKING:
    from app.debt.overdue_targeting import LockedOverdueDebt

__all__ = (
    "LockedOverdueRatingAppendPort",
    "OverdueRatingAppendOutcome",
    "PendingOverdueRatingEffect",
)


class OverdueRatingAppendOutcome(StrEnum):
    APPENDED = "appended"
    SOURCE_ALREADY_EXISTS = "source_already_exists"


@dataclass(frozen=True, slots=True, repr=False)
class PendingOverdueRatingEffect:
    event_id: UUID = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    overdue_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID):
            raise ValueError("Pending overdue rating identity is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Pending overdue rating Debt is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Pending overdue rating ShopCustomer is invalid")
        occurred_at = _normalize_aware_utc(self.overdue_at)
        tashkent_business_date(occurred_at)
        object.__setattr__(self, "overdue_at", occurred_at)

    def __repr__(self) -> str:
        return "PendingOverdueRatingEffect(<redacted>)"


@runtime_checkable
class LockedOverdueRatingAppendPort(Protocol):
    def append_pending_overdue(
        self,
        session: Session,
        *,
        locked_debt: LockedOverdueDebt,
        effect: PendingOverdueRatingEffect,
    ) -> OverdueRatingAppendOutcome: ...


def _normalize_aware_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Pending overdue rating time must be aware")
    return value.astimezone(UTC)
