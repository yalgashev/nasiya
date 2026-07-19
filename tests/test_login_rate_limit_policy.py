from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.auth.error_codes import ErrorCode
from app.auth.login_rate_limit import (
    LOGIN_IP_SCOPE,
    LOGIN_PHONE_SCOPE,
    LoginRateLimitPolicy,
    clear_login_phone_failures,
    get_login_client_host,
)
from app.auth.models import AuthRateLimit
from app.db import create_database_session_factory
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-login-policy"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(
    engine: Engine,
    *,
    phone_attempts: int = 2,
    ip_attempts: int = 20,
    window_seconds: int = 60,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        login_rate_limit_window_seconds=window_seconds,
        login_rate_limit_phone_attempts=phone_attempts,
        login_rate_limit_ip_attempts=ip_attempts,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_policy(db_session: Session, settings: Settings) -> LoginRateLimitPolicy:
    return LoginRateLimitPolicy(db=db_session, settings=settings)


def get_scope_counts(db_session: Session) -> dict[str, int]:
    records = db_session.scalars(select(AuthRateLimit)).all()
    return {
        scope: sum(1 for record in records if record.scope == scope)
        for scope in {record.scope for record in records}
    }


def make_request(client_host: str, x_forwarded_for: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/login",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", x_forwarded_for.encode("ascii"))],
            "client": (client_host, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_login_phone_threshold_blocks_with_safe_rate_limited_result(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, phone_attempts=2, ip_attempts=20)
    policy = make_policy(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    phone = "+998901234567"

    assert policy.record_failure(phone, "203.0.113.10", now).allowed is True

    blocked = policy.record_failure(
        phone,
        "203.0.113.10",
        now + timedelta(seconds=1),
    )
    later_check = policy.check(phone, "203.0.113.10", now + timedelta(seconds=2))

    assert blocked.allowed is False
    assert blocked.error_code is ErrorCode.RATE_LIMITED
    assert blocked.public_error == {
        "code": "RATE_LIMITED",
        "message": "Juda ko'p urinish. Keyinroq qayta urinib ko'ring.",
    }
    assert phone not in repr(blocked)
    assert later_check.allowed is False
    assert later_check.error_code is ErrorCode.RATE_LIMITED


def test_login_ip_threshold_blocks_independent_of_phone(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, phone_attempts=20, ip_attempts=2)
    policy = make_policy(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client_host = "203.0.113.10"

    assert policy.record_failure("+998901234567", client_host, now).allowed is True

    blocked = policy.record_failure(
        "+998901234568",
        client_host,
        now + timedelta(seconds=1),
    )

    assert blocked.allowed is False
    assert blocked.error_code is ErrorCode.RATE_LIMITED
    assert policy.check(
        "+998901234569",
        client_host,
        now + timedelta(seconds=2),
    ).allowed is False


def test_login_phone_buckets_are_isolated_by_normalized_phone(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, phone_attempts=2, ip_attempts=20)
    policy = make_policy(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)

    policy.record_failure("901234567", "203.0.113.10", now)
    blocked_phone = policy.record_failure(
        "+998901234567",
        "203.0.113.11",
        now + timedelta(seconds=1),
    )
    other_phone = policy.check(
        "+998901234568",
        "203.0.113.12",
        now + timedelta(seconds=2),
    )

    assert blocked_phone.allowed is False
    assert other_phone.allowed is True
    assert get_scope_counts(db_session)[LOGIN_PHONE_SCOPE] == 1


def test_login_limited_result_does_not_reveal_user_existence(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, phone_attempts=1, ip_attempts=20)
    policy = make_policy(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)

    first_phone = policy.record_failure("+998901234567", "203.0.113.10", now)
    second_phone = policy.record_failure("+998901234568", "203.0.113.11", now)

    assert first_phone.allowed is False
    assert second_phone.allowed is False
    assert first_phone.error_code is ErrorCode.RATE_LIMITED
    assert second_phone.error_code is ErrorCode.RATE_LIMITED
    assert first_phone.public_error == second_phone.public_error
    assert "user" not in repr(first_phone).casefold()


def test_invalid_phone_is_counted_against_ip_only(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, phone_attempts=2, ip_attempts=2)
    policy = make_policy(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    invalid_phone = "+99890abc4567"
    client_host = "203.0.113.10"

    first = policy.record_failure(invalid_phone, client_host, now)
    second = policy.record_failure(
        invalid_phone,
        client_host,
        now + timedelta(seconds=1),
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.error_code is ErrorCode.RATE_LIMITED
    assert get_scope_counts(db_session) == {LOGIN_IP_SCOPE: 1}


def test_successful_login_clears_phone_bucket_but_keeps_ip_bucket(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, phone_attempts=2, ip_attempts=20)
    policy = make_policy(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    phone = "+998901234567"

    policy.record_failure(phone, "203.0.113.10", now)
    policy.record_failure(phone, "203.0.113.10", now + timedelta(seconds=1))

    blocked_check = policy.check(
        phone,
        "203.0.113.10",
        now + timedelta(seconds=2),
    )
    assert blocked_check.allowed is False
    assert clear_login_phone_failures(db_session, settings, "901234567") is True

    cleared_check = policy.check(
        phone,
        "203.0.113.10",
        now + timedelta(seconds=3),
    )
    assert cleared_check.allowed is True
    assert get_scope_counts(db_session) == {LOGIN_IP_SCOPE: 1}


def test_login_policy_uses_request_client_host_and_ignores_x_forwarded_for() -> None:
    request = make_request(
        client_host="203.0.113.10",
        x_forwarded_for="198.51.100.77",
    )

    assert get_login_client_host(request) == "203.0.113.10"


def test_login_policy_does_not_store_raw_phone_or_ip(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, phone_attempts=20, ip_attempts=20)
    policy = make_policy(db_session, settings)
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    raw_phone = "901234567"
    canonical_phone = "+998901234567"
    client_host = "203.0.113.10"

    policy.record_failure(raw_phone, client_host, now)
    stored_values = db_session.execute(
        text(
            "SELECT scope, key_hash, window_started_at::text, "
            "attempt_count::text, updated_at::text "
            "FROM auth_rate_limits"
        )
    ).all()
    stored_text = "|".join(
        str(value)
        for row in stored_values
        for value in row
    )

    assert raw_phone not in stored_text
    assert canonical_phone not in stored_text
    assert client_host not in stored_text
