import inspect
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.auth.service as auth_service
import app.auth.telegram_reauth as telegram_reauth
from app.auth.models import AuthRateLimit, User
from app.auth.service import check_current_password, create_user
from app.auth.telegram_reauth import (
    TELEGRAM_REAUTH_IP_SCOPE,
    TELEGRAM_REAUTH_USER_SCOPE,
    TELEGRAM_REAUTH_WINDOW_SECONDS,
    TelegramReauthRateLimitPolicy,
)
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp

NOW = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
RAW_PASSWORD = "Password123"
TEST_HMAC_KEY = "test-current-password-rate-limit-hmac-key"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Iterator[Session]:
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
        rate_limit_hmac_key=TEST_HMAC_KEY,
    )


def add_user(
    db_session: Session,
    phone: str = "+998901111111",
    *,
    is_active: bool = True,
) -> User:
    result = create_user(
        db_session,
        phone,
        RAW_PASSWORD,
        is_active=is_active,
    )
    assert result.user is not None
    db_session.flush()
    return result.user


def test_current_password_check_uses_canonical_active_user(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)

    assert check_current_password(db_session, user, RAW_PASSWORD, settings) is True
    assert check_current_password(db_session, user, "Password124", settings) is False
    assert check_current_password(db_session, user, "", settings) is False
    assert (
        check_current_password(
            db_session,
            user,
            "x" * (settings.password_max_length + 1),
            settings,
        )
        is False
    )


def test_current_password_check_rejects_disabled_or_passwordless_user(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    disabled = add_user(
        db_session,
        "+998902222222",
        is_active=False,
    )
    passwordless = User(phone="+998903333333", password_hash=None)
    db_session.add(passwordless)
    db_session.flush()

    assert check_current_password(db_session, disabled, RAW_PASSWORD, settings) is False
    assert (
        check_current_password(db_session, passwordless, RAW_PASSWORD, settings)
        is False
    )


def test_current_password_service_has_no_transaction_or_telegram_dependency() -> None:
    source = inspect.getsource(auth_service.check_current_password)
    module_source = inspect.getsource(auth_service)

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "app.telegram" not in module_source


def test_reauth_limiter_blocks_fifth_user_failure_and_hashes_keys(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    client_ip = ResolvedClientIp("203.0.113.80")
    policy = TelegramReauthRateLimitPolicy(db_session, settings)

    for attempt in range(1, 5):
        result = policy.record_failure(
            user,
            client_ip,
            NOW + timedelta(seconds=attempt),
        )
        assert result.allowed is True

    blocked = policy.record_failure(
        user,
        client_ip,
        NOW + timedelta(seconds=5),
    )

    assert blocked.allowed is False
    assert policy.check(user, client_ip, NOW + timedelta(seconds=6)).allowed is False
    records = list(
        db_session.scalars(select(AuthRateLimit).order_by(AuthRateLimit.scope))
    )
    assert {record.scope for record in records} == {
        TELEGRAM_REAUTH_IP_SCOPE,
        TELEGRAM_REAUTH_USER_SCOPE,
    }
    assert {record.attempt_count for record in records} == {5}
    persisted_text = " ".join(f"{record.scope} {record.key_hash}" for record in records)
    assert str(user.id) not in persisted_text
    assert client_ip.as_hmac_input() not in persisted_text


def test_success_clears_only_user_bucket_and_window_resets(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session)
    client_ip = ResolvedClientIp("203.0.113.81")
    policy = TelegramReauthRateLimitPolicy(db_session, settings)
    policy.record_failure(user, client_ip, NOW)

    assert policy.clear_user_failures_after_success(user) is True
    records = list(db_session.scalars(select(AuthRateLimit)))
    assert [record.scope for record in records] == [TELEGRAM_REAUTH_IP_SCOPE]

    reset_result = policy.record_failure(
        user,
        client_ip,
        NOW + timedelta(seconds=TELEGRAM_REAUTH_WINDOW_SECONDS),
    )
    assert reset_result.allowed is True
    records = list(
        db_session.scalars(select(AuthRateLimit).order_by(AuthRateLimit.scope))
    )
    assert {record.attempt_count for record in records} == {1}


def test_reauth_policy_repr_and_source_do_not_expose_raw_dimensions() -> None:
    source = inspect.getsource(telegram_reauth)

    assert "raw_password" not in source
    assert "phone" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "login_phone" not in source
    assert "telegram_link_issue" not in source
