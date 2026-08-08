"""Pure Asia/Tashkent business-time rules for pending debts."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

__all__ = (
    "PENDING_DEBT_TTL",
    "TASHKENT_TIMEZONE",
    "is_pending_expired",
    "parse_due_date",
    "pending_expires_at",
    "tashkent_business_date",
    "validate_acceptance_due_date",
    "validate_due_date_not_before_expiry_business_date",
)

TASHKENT_TIMEZONE: Final = ZoneInfo("Asia/Tashkent")
PENDING_DEBT_TTL: Final = timedelta(hours=72)
_ISO_DATE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", flags=re.ASCII)


def tashkent_business_date(now: datetime) -> date:
    _require_aware_datetime(now, field_name="Now")
    return now.astimezone(TASHKENT_TIMEZONE).date()


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
    if not isinstance(due_date, date) or isinstance(due_date, datetime):
        raise ValueError("Due date is invalid")
    expiry_business_date = tashkent_business_date(pending_expiry)
    if due_date < expiry_business_date:
        raise ValueError("Due date cannot be before pending expiry business date")
    return due_date


def validate_acceptance_due_date(*, now: datetime, due_date: date) -> date:
    if not isinstance(due_date, date) or isinstance(due_date, datetime):
        raise ValueError("Due date is invalid")
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
