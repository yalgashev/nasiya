from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.debt.business_time import (
    PENDING_DEBT_TTL,
    TASHKENT_TIMEZONE,
    is_pending_expired,
    parse_due_date,
    pending_expires_at,
    tashkent_business_date,
    validate_acceptance_due_date,
    validate_due_date_not_before_expiry_business_date,
)


def test_tashkent_business_date_uses_the_injected_aware_instant() -> None:
    assert TASHKENT_TIMEZONE.key == "Asia/Tashkent"
    assert tashkent_business_date(datetime(2026, 1, 1, 19, tzinfo=UTC)) == date(
        2026, 1, 2
    )
    assert tashkent_business_date(datetime(2026, 1, 1, 18, 59, tzinfo=UTC)) == date(
        2026, 1, 1
    )

    for naive_or_invalid in (datetime(2026, 1, 1), None):
        with pytest.raises(ValueError, match="aware datetime"):
            tashkent_business_date(naive_or_invalid)  # type: ignore[arg-type]


def test_pending_expiry_is_exactly_72_hours_and_boundary_is_expired() -> None:
    created_at = datetime(2026, 5, 1, 8, 30, tzinfo=UTC)
    expiry = pending_expires_at(created_at)

    assert PENDING_DEBT_TTL == timedelta(hours=72)
    assert expiry == datetime(2026, 5, 4, 8, 30, tzinfo=UTC)
    assert not is_pending_expired(
        now=expiry - timedelta(microseconds=1), pending_expires_at=expiry
    )
    assert is_pending_expired(now=expiry, pending_expires_at=expiry)
    assert is_pending_expired(
        now=expiry + timedelta(microseconds=1), pending_expires_at=expiry
    )

    dst_created_at = datetime(2026, 3, 7, 12, tzinfo=ZoneInfo("America/New_York"))
    dst_expiry = pending_expires_at(dst_created_at)
    assert dst_expiry.tzinfo is UTC
    assert dst_expiry - dst_created_at.astimezone(UTC) == timedelta(hours=72)


def test_due_date_parser_and_business_date_validation_are_exact() -> None:
    expiry = datetime(2026, 5, 1, 19, tzinfo=UTC)
    assert parse_due_date("2026-05-02") == date(2026, 5, 2)
    assert validate_due_date_not_before_expiry_business_date(
        due_date=date(2026, 5, 2), pending_expiry=expiry
    ) == date(2026, 5, 2)

    for malformed in ("2026-5-02", "2026-02-30", "2026/05/02", "", None):
        with pytest.raises(ValueError, match="ISO YYYY-MM-DD"):
            parse_due_date(malformed)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="cannot be before"):
        validate_due_date_not_before_expiry_business_date(
            due_date=date(2026, 5, 1), pending_expiry=expiry
        )


def test_acceptance_requires_tashkent_business_date_not_after_due_date() -> None:
    due_date = date(2026, 5, 2)
    assert (
        validate_acceptance_due_date(
            now=datetime(2026, 5, 2, 18, 59, tzinfo=UTC), due_date=due_date
        )
        == due_date
    )

    with pytest.raises(ValueError, match="Due date has passed"):
        validate_acceptance_due_date(
            now=datetime(2026, 5, 2, 19, tzinfo=UTC), due_date=due_date
        )
