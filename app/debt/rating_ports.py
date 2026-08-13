"""Debt-local structural boundary for one pending overdue source effect."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from app.debt.business_time import tashkent_business_date
from app.debt.values import DebtId, DebtRevision
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "LockedOverdueRatingAppendPort",
    "LockedOverdueRatingSource",
    "LockedWrittenOffRatingAppendPort",
    "OverdueRatingAppendOutcome",
    "PendingOverdueRatingEffect",
    "PendingWrittenOffRatingEffect",
    "WrittenOffRatingAppendOutcome",
    "mark_locked_overdue_rating_source",
    "validate_locked_overdue_rating_source",
)


class OverdueRatingAppendOutcome(StrEnum):
    APPENDED = "appended"
    SOURCE_ALREADY_EXISTS = "source_already_exists"


class WrittenOffRatingAppendOutcome(StrEnum):
    APPENDED = "appended"
    SOURCE_ALREADY_EXISTS = "source_already_exists"


@dataclass(frozen=True, slots=True, repr=False)
class LockedOverdueRatingSource:
    """Debt-owned proof derived after the caller locks the complete chain."""

    customer_id: UUID = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.customer_id, UUID):
            raise ValueError("Locked overdue Customer is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Locked overdue ShopCustomer is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Locked overdue Debt is invalid")
        if not isinstance(self._session, Session):
            raise ValueError("Locked overdue Session is invalid")

    def __repr__(self) -> str:
        return "LockedOverdueRatingSource(<redacted>)"


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


@dataclass(frozen=True, slots=True, repr=False)
class PendingWrittenOffRatingEffect:
    event_id: UUID = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    written_off_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID):
            raise ValueError("Pending write-off rating identity is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Pending write-off rating Debt is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Pending write-off rating ShopCustomer is invalid")
        occurred_at = _normalize_aware_utc(self.written_off_at)
        tashkent_business_date(occurred_at)
        object.__setattr__(self, "written_off_at", occurred_at)

    def __repr__(self) -> str:
        return "PendingWrittenOffRatingEffect(<redacted>)"


@runtime_checkable
class LockedOverdueRatingAppendPort(Protocol):
    def append_pending_overdue(
        self,
        session: Session,
        *,
        locked_source: LockedOverdueRatingSource,
        effect: PendingOverdueRatingEffect,
    ) -> OverdueRatingAppendOutcome: ...


@runtime_checkable
class LockedWrittenOffRatingAppendPort(Protocol):
    def has_coherent_overdue_source(
        self,
        session: Session,
        *,
        locked_source: LockedOverdueRatingSource,
        overdue_at: datetime,
        overdue_revision: DebtRevision,
    ) -> bool: ...

    def append_pending_written_off(
        self,
        session: Session,
        *,
        locked_source: LockedOverdueRatingSource,
        effect: PendingWrittenOffRatingEffect,
    ) -> WrittenOffRatingAppendOutcome: ...


def mark_locked_overdue_rating_source(
    session: Session,
    *,
    customer_id: UUID,
    shop_customer_id: ShopCustomerId,
    debt_id: DebtId,
) -> LockedOverdueRatingSource:
    if not isinstance(session, Session):
        raise TypeError("session must be a Session")
    if not isinstance(customer_id, UUID):
        raise TypeError("customer_id must be a UUID")
    if not isinstance(shop_customer_id, ShopCustomerId):
        raise TypeError("shop_customer_id must be a ShopCustomerId")
    if not isinstance(debt_id, DebtId):
        raise TypeError("debt_id must be a DebtId")
    return LockedOverdueRatingSource(
        customer_id=customer_id,
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        _session=session,
    )


def validate_locked_overdue_rating_source(
    session: Session,
    token: object,
) -> LockedOverdueRatingSource:
    if not isinstance(token, LockedOverdueRatingSource):
        raise TypeError("locked source must come from debt rating boundary")
    if token._session is not session:
        raise RuntimeError("locked overdue rating source belongs to another session")
    return token


def _normalize_aware_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Pending overdue rating time must be aware")
    return value.astimezone(UTC)
