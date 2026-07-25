from collections.abc import Generator
from datetime import UTC, datetime
from inspect import signature
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.rate_limit as telegram_rate_limit
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.rate_limit import RateLimitResult, hash_rate_limit_key
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink, TelegramLinkToken
from app.telegram.rate_limit import (
    TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
    TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
    TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
    TelegramLinkIssuanceRateLimitPolicy,
    check_telegram_link_issuance_rate_limit,
    record_telegram_link_issuance_attempt,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-link-policy"


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
    engine: Engine | None = None,
    *,
    user_attempts: int = 3,
    phone_attempts: int = 3,
    ip_attempts: int = 20,
    window_seconds: int = 900,
) -> Settings:
    database_url = (
        engine.url.render_as_string(hide_password=False)
        if engine is not None
        else "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"
    )
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=database_url,
        session_cookie_secure=False,
        telegram_link_rate_limit_window_seconds=window_seconds,
        telegram_link_rate_limit_user_attempts=user_attempts,
        telegram_link_rate_limit_phone_attempts=phone_attempts,
        telegram_link_rate_limit_ip_attempts=ip_attempts,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def add_user(session: Session, phone: str = "+998900004001") -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def make_user(phone: str = "+998900004099") -> User:
    return User(id=uuid4(), phone=phone)


def test_policy_public_api_accepts_no_caller_supplied_phone() -> None:
    for callable_object in (
        check_telegram_link_issuance_rate_limit,
        record_telegram_link_issuance_attempt,
        TelegramLinkIssuanceRateLimitPolicy.check,
        TelegramLinkIssuanceRateLimitPolicy.record_attempt,
    ):
        assert "phone" not in signature(callable_object).parameters


def test_policy_checks_all_three_buckets_with_limiter_thresholds(monkeypatch) -> None:
    calls: list[tuple[str, str, int, int]] = []

    class FakeLimiter:
        def __init__(self, db, settings):
            self.db = db
            self.settings = settings

        def check(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            calls.append((scope, raw_key, limit, window_seconds))
            return RateLimitResult(allowed=True)

    monkeypatch.setattr(telegram_rate_limit, "AuthRateLimiter", FakeLimiter)
    settings = make_settings()
    user = make_user()
    client_ip = ResolvedClientIp("::ffff:203.0.113.10")

    result = check_telegram_link_issuance_rate_limit(
        object(),
        settings,
        user,
        client_ip,
        datetime(2026, 7, 24, 13, 0, tzinfo=UTC),
    )

    assert result.allowed is True
    assert calls == [
        (
            TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
            f"{TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX}{user.id}",
            4,
            900,
        ),
        (
            TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
            f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}{user.phone}",
            4,
            900,
        ),
        (
            TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
            f"{TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX}203.0.113.10",
            21,
            900,
        ),
    ]


def test_policy_returns_generic_rate_limited_without_bucket_leak(monkeypatch) -> None:
    calls: list[str] = []
    raw_phone = "+998900004098"
    raw_ip = "203.0.113.20"

    class FakeLimiter:
        def __init__(self, db, settings):
            self.db = db
            self.settings = settings

        def check(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            calls.append(scope)
            return RateLimitResult(
                allowed=scope != TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
                error_code=(
                    ErrorCode.RATE_LIMITED
                    if scope == TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE
                    else None
                ),
            )

    monkeypatch.setattr(telegram_rate_limit, "AuthRateLimiter", FakeLimiter)
    result = check_telegram_link_issuance_rate_limit(
        object(),
        make_settings(),
        make_user(phone=raw_phone),
        ResolvedClientIp(raw_ip),
        datetime(2026, 7, 24, 13, 5, tzinfo=UTC),
    )

    result_text = f"{result!r} {result.public_error}"

    assert calls == [
        TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
    ]
    assert result.allowed is False
    assert result.error_code is ErrorCode.RATE_LIMITED
    assert result.public_error == {
        "code": "RATE_LIMITED",
        "message": "Juda ko'p urinish. Keyinroq qayta urinib ko'ring.",
    }
    assert raw_phone not in result_text
    assert raw_ip not in result_text
    assert "phone" not in result_text.casefold()
    assert "ip" not in result_text.casefold()
    assert "user" not in result_text.casefold()


def test_record_attempt_checks_then_records_all_three_buckets(monkeypatch) -> None:
    checks: list[str] = []
    records: list[str] = []

    class FakeLimiter:
        def __init__(self, db, settings):
            self.db = db
            self.settings = settings

        def check(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            checks.append(scope)
            return RateLimitResult(allowed=True)

        def record_failure(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            records.append(scope)
            return RateLimitResult(allowed=True)

    monkeypatch.setattr(telegram_rate_limit, "AuthRateLimiter", FakeLimiter)

    result = record_telegram_link_issuance_attempt(
        object(),
        make_settings(),
        make_user(),
        ResolvedClientIp("203.0.113.30"),
        datetime(2026, 7, 24, 13, 10, tzinfo=UTC),
    )

    expected_scopes = [
        TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
    ]
    assert result.allowed is True
    assert checks == expected_scopes
    assert records == expected_scopes


def test_record_attempt_does_not_record_when_existing_bucket_is_blocked(
    monkeypatch,
) -> None:
    records: list[str] = []

    class FakeLimiter:
        def __init__(self, db, settings):
            self.db = db
            self.settings = settings

        def check(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            return RateLimitResult(
                allowed=scope != TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
                error_code=(
                    ErrorCode.RATE_LIMITED
                    if scope == TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE
                    else None
                ),
            )

        def record_failure(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            records.append(scope)
            return RateLimitResult(allowed=True)

    monkeypatch.setattr(telegram_rate_limit, "AuthRateLimiter", FakeLimiter)

    result = record_telegram_link_issuance_attempt(
        object(),
        make_settings(),
        make_user(),
        ResolvedClientIp("203.0.113.31"),
        datetime(2026, 7, 24, 13, 15, tzinfo=UTC),
    )

    assert result.allowed is False
    assert result.error_code is ErrorCode.RATE_LIMITED
    assert records == []


@pytest.mark.integration
def test_policy_records_scoped_hmac_buckets_without_raw_values(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    client_ip = ResolvedClientIp("::ffff:203.0.113.10")
    now = datetime(2026, 7, 24, 13, 20, tzinfo=UTC)

    result = TelegramLinkIssuanceRateLimitPolicy(
        db=db_session,
        settings=settings,
    ).record_attempt(user, client_ip, now)

    records = db_session.scalars(
        select(AuthRateLimit).order_by(AuthRateLimit.scope)
    ).all()
    actual_hashes = {record.key_hash for record in records}
    records_by_scope = {record.scope: record for record in records}
    stored_text = "|".join(
        str(value)
        for row in db_session.execute(
            text(
                "SELECT scope, key_hash, window_started_at::text, "
                "attempt_count::text, updated_at::text "
                "FROM auth_rate_limits"
            )
        ).all()
        for value in row
    )

    assert result.allowed is True
    assert set(records_by_scope) == {
        TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
        TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
    }
    assert records_by_scope[
        TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE
    ].key_hash == hash_rate_limit_key(
        settings,
        f"{TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX}{user.id}",
    )
    assert records_by_scope[
        TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE
    ].key_hash == hash_rate_limit_key(
        settings,
        f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}{user.phone}",
    )
    assert records_by_scope[
        TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE
    ].key_hash == hash_rate_limit_key(
        settings,
        f"{TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX}203.0.113.10",
    )
    assert hash_rate_limit_key(settings, str(user.id)) not in actual_hashes
    assert hash_rate_limit_key(settings, user.phone) not in actual_hashes
    assert hash_rate_limit_key(settings, "203.0.113.10") not in actual_hashes
    assert str(user.id) not in stored_text
    assert user.phone not in stored_text
    assert client_ip.as_hmac_input() not in stored_text


@pytest.mark.integration
def test_policy_blocks_without_disclosing_which_bucket_is_limited(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database, user_attempts=1)
    policy = TelegramLinkIssuanceRateLimitPolicy(db=db_session, settings=settings)
    user = add_user(db_session, "+998900004002")
    client_ip = ResolvedClientIp("203.0.113.40")
    now = datetime(2026, 7, 24, 13, 25, tzinfo=UTC)

    first = policy.record_attempt(user, client_ip, now)
    blocked = policy.record_attempt(user, client_ip, now)
    result_text = f"{blocked!r} {blocked.public_error}"

    assert first.allowed is True
    assert blocked.allowed is False
    assert blocked.error_code is ErrorCode.RATE_LIMITED
    assert "user" not in result_text.casefold()
    assert "phone" not in result_text.casefold()
    assert "ip" not in result_text.casefold()
    assert user.phone not in result_text
    assert client_ip.as_hmac_input() not in result_text


@pytest.mark.integration
def test_policy_does_not_touch_token_or_link_state(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session, "+998900004003")

    result = record_telegram_link_issuance_attempt(
        db_session,
        settings,
        user,
        ResolvedClientIp("203.0.113.50"),
        datetime(2026, 7, 24, 13, 30, tzinfo=UTC),
    )

    assert result.allowed is True
    assert count_table(db_session, AuthRateLimit) == 3
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkToken) == 0


@pytest.mark.integration
def test_policy_does_not_commit_or_full_rollback(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = add_user(first_session, "+998900004004")
        policy = TelegramLinkIssuanceRateLimitPolicy(
            db=first_session,
            settings=settings,
        )

        result = policy.record_attempt(
            user,
            ResolvedClientIp("203.0.113.60"),
            datetime(2026, 7, 24, 13, 35, tzinfo=UTC),
        )

        stored_count = second_session.scalar(
            select(func.count()).select_from(AuthRateLimit)
        )

        assert result.allowed is True
        assert stored_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()
