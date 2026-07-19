from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit
from app.auth.rate_limit import AuthRateLimiter, hash_rate_limit_key
from app.db import create_database_session_factory
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-rate-limiter"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_limiter(db_session: Session, settings: Settings) -> AuthRateLimiter:
    return AuthRateLimiter(db=db_session, settings=settings)


def get_rate_limit_record(db_session: Session) -> AuthRateLimit:
    record = db_session.scalar(select(AuthRateLimit))
    assert record is not None
    return record


def test_rate_limiter_allows_until_limit_then_blocks(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    limiter = make_limiter(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    raw_key = "+998901234567"

    assert limiter.check("phone", raw_key, now, limit=3, window_seconds=60).allowed
    first = limiter.record_failure(
        "phone",
        raw_key,
        now,
        limit=3,
        window_seconds=60,
    )
    second = limiter.record_failure(
        "phone",
        raw_key,
        now + timedelta(seconds=1),
        limit=3,
        window_seconds=60,
    )

    assert first.allowed is True
    assert first.attempts_remaining == 2
    assert second.allowed is True
    assert second.attempts_remaining == 1
    assert limiter.check(
        "phone",
        raw_key,
        now + timedelta(seconds=2),
        limit=3,
        window_seconds=60,
    ).allowed

    limited = limiter.record_failure(
        "phone",
        raw_key,
        now + timedelta(seconds=3),
        limit=3,
        window_seconds=60,
    )
    blocked = limiter.check(
        "phone",
        raw_key,
        now + timedelta(seconds=4),
        limit=3,
        window_seconds=60,
    )

    assert limited.allowed is False
    assert limited.error_code is ErrorCode.RATE_LIMITED
    assert blocked.allowed is False
    assert blocked.error_code is ErrorCode.RATE_LIMITED
    assert blocked.attempts_remaining == 0


def test_rate_limiter_resets_after_window_expires(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    limiter = make_limiter(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    later = now + timedelta(seconds=61)

    limiter.record_failure("phone", "same-key", now, limit=2, window_seconds=60)
    limiter.record_failure(
        "phone",
        "same-key",
        now + timedelta(seconds=1),
        limit=2,
        window_seconds=60,
    )

    assert limiter.check(
        "phone",
        "same-key",
        now + timedelta(seconds=2),
        limit=2,
        window_seconds=60,
    ).allowed is False
    assert limiter.check("phone", "same-key", later, limit=2, window_seconds=60).allowed

    reset = limiter.record_failure(
        "phone",
        "same-key",
        later,
        limit=2,
        window_seconds=60,
    )
    record = get_rate_limit_record(db_session)

    assert reset.allowed is True
    assert reset.attempts_remaining == 1
    assert record.attempt_count == 1
    assert record.window_started_at == later


def test_rate_limiter_keeps_scopes_isolated(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    limiter = make_limiter(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    raw_key = "203.0.113.7"

    limiter.record_failure("phone", raw_key, now, limit=2, window_seconds=60)
    limiter.record_failure(
        "phone",
        raw_key,
        now + timedelta(seconds=1),
        limit=2,
        window_seconds=60,
    )

    assert limiter.check(
        "phone",
        raw_key,
        now + timedelta(seconds=2),
        limit=2,
        window_seconds=60,
    ).allowed is False
    assert limiter.check(
        "ip",
        raw_key,
        now + timedelta(seconds=2),
        limit=2,
        window_seconds=60,
    ).allowed is True

    limiter.record_failure(
        "ip",
        raw_key,
        now + timedelta(seconds=3),
        limit=2,
        window_seconds=60,
    )
    statement = select(AuthRateLimit).order_by(AuthRateLimit.scope)
    scopes = {row.scope for row in db_session.scalars(statement)}

    assert scopes == {"ip", "phone"}


def test_rate_limiter_stores_only_hmac_key_hash(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    limiter = make_limiter(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    raw_key = "+998901234567"

    result = limiter.record_failure(
        "phone",
        raw_key,
        now,
        limit=5,
        window_seconds=60,
    )
    record = get_rate_limit_record(db_session)
    stored_values = db_session.execute(
        text(
            "SELECT scope, key_hash, window_started_at::text, "
            "attempt_count::text, updated_at::text "
            "FROM auth_rate_limits"
        )
    ).one()

    assert record.key_hash == hash_rate_limit_key(settings, raw_key)
    assert record.key_hash != raw_key
    assert raw_key not in "|".join(str(value) for value in stored_values)
    assert raw_key not in repr(result)


def test_rate_limiter_clear_key_removes_single_key(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    limiter = make_limiter(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)

    limiter.record_failure("phone", "first-key", now, limit=2, window_seconds=60)
    limiter.record_failure("phone", "second-key", now, limit=2, window_seconds=60)

    assert limiter.clear_key("phone", "first-key") is True
    assert limiter.check("phone", "first-key", now, limit=2, window_seconds=60).allowed
    assert db_session.scalar(select(func.count()).select_from(AuthRateLimit)) == 1
    assert limiter.clear_key("phone", "first-key") is False


def test_rate_limiter_service_does_not_commit(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        limiter = make_limiter(first_session, settings)
        now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)

        limiter.record_failure("phone", "uncommitted-key", now, 2, 60)

        stored_count = second_session.scalar(
            select(func.count()).select_from(AuthRateLimit)
        )
        assert stored_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


def test_rate_limiter_safe_exceptions_do_not_include_raw_key(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    limiter = make_limiter(db_session, settings)
    raw_key = "+998901234567"
    naive_now = datetime(2026, 7, 19, 10, 30)

    with pytest.raises(ValueError) as exc_info:
        limiter.check("phone", raw_key, naive_now, limit=2, window_seconds=60)

    assert raw_key not in str(exc_info.value)
