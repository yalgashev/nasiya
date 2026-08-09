"""Pure Asia/Tashkent business-time rules for pending debts."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.debt.enums import M15_PERSISTED_STATUSES, DebtStatus

__all__ = (
    "PENDING_DEBT_TTL",
    "TASHKENT_TIMEZONE",
    "is_pending_expired",
    "is_effectively_overdue",
    "is_payment_due_date_payable",
    "normalize_payment_created_at",
    "parse_due_date",
    "payment_business_date",
    "pending_expires_at",
    "tashkent_business_date",
    "validate_acceptance_due_date",
    "validate_due_date_not_before_expiry_business_date",
    "validate_payment_due_date",
)

TASHKENT_TIMEZONE: Final = ZoneInfo("Asia/Tashkent")
PENDING_DEBT_TTL: Final = timedelta(hours=72)
_ISO_DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", flags=re.ASCII)


def tashkent_business_date(now: datetime) -> date:
    _require_aware_datetime(now, field_name="Now")
    return now.astimezone(TASHKENT_TIMEZONE).date()


def normalize_payment_created_at(payment_created_at: datetime) -> datetime:
    """Accept only the server-captured, aware UTC instant for a Payment."""

    _require_aware_datetime(payment_created_at, field_name="Payment created at")
    if payment_created_at.utcoffset() != timedelta(0):
        raise ValueError("Payment created at must be UTC")
    return payment_created_at.astimezone(UTC)


def payment_business_date(payment_created_at: datetime) -> date:
    return tashkent_business_date(normalize_payment_created_at(payment_created_at))


def is_payment_due_date_payable(
    *, payment_created_at: datetime, due_date: date
) -> bool:
    _require_due_date(due_date)
    return payment_business_date(payment_created_at) <= due_date


def is_effectively_overdue(
    *, status: DebtStatus, due_date: date, server_now: datetime
) -> bool:
    """Derive risk-sensitive overdue state without mutating persisted Debt."""

    if not isinstance(status, DebtStatus) or status not in M15_PERSISTED_STATUSES:
        raise ValueError("Debt status is outside the M15 persisted subset")
    _require_due_date(due_date)
    business_date = tashkent_business_date(server_now)
    return status is DebtStatus.OVERDUE or (
        status is DebtStatus.ACTIVE and due_date < business_date
    )


def validate_payment_due_date(*, payment_created_at: datetime, due_date: date) -> date:
    _require_due_date(due_date)
    if not is_payment_due_date_payable(
        payment_created_at=payment_created_at, due_date=due_date
    ):
        raise ValueError("Debt is not payable after its Tashkent due date")
    return due_date


def pending_expires_at(created_at: datetime) -> datetime:
    _require_aware_datetime(created_at, field_name="Created at")
    return created_at.astimezone(UTC) + PENDING_DEBT_TTL


def is_pending_expired(*, now: datetime, pending_expires_at: datetime) -> bool:
    _require_aware_datetime(now, field_name="Now")
    _require_aware_datetime(pending_expires_at, field_name="Pending expiry")
    return now >= pending_expires_at


def parse_due_date(value: str) -> date:
    if not isinstance(value, str) or _ISO_DATE_PATTERN.fullmatch(value) is None:
        raise ValueError("Due date must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Due date must be ISO YYYY-MM-DD") from None


def validate_due_date_not_before_expiry_business_date(
    *, due_date: date, pending_expiry: datetime
) -> date:
    _require_due_date(due_date)
    expiry_business_date = tashkent_business_date(pending_expiry)
    if due_date < expiry_business_date:
        raise ValueError("Due date cannot be before pending expiry business date")
    return due_date


def validate_acceptance_due_date(*, now: datetime, due_date: date) -> date:
    _require_due_date(due_date)
    if tashkent_business_date(now) > due_date:
        raise ValueError("Due date has passed in Tashkent business time")
    return due_date


def _require_aware_datetime(value: datetime, *, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be an aware datetime")


def _require_due_date(value: date) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("Due date is invalid")
