import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth import csrf
from app.auth.csrf import CsrfToken, get_csrf_token, verify_csrf_token
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import (
    CreatedSession,
    create_authenticated_session,
    revoke_session,
    rotate_session,
)
from app.db import create_database_session_factory
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-csrf-service"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=False,
        session_ttl_days=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_memory_session(
    *,
    csrf_secret: str = "csrf-secret-for-unit-tests",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> AuthSession:
    return AuthSession(
        csrf_secret=csrf_secret,
        expires_at=expires_at or datetime(2026, 7, 19, 11, 0, tzinfo=UTC),
        revoked_at=revoked_at,
    )


def commit_user(db_session: Session) -> User:
    result = create_user(db_session, "+998901234567", "Password123")
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def test_csrf_token_is_redacted_in_repr_str_and_logs(caplog) -> None:
    session = make_memory_session()
    token = get_csrf_token(session)
    raw_token = token.as_form_value()
    logger = logging.getLogger("tests.csrf")

    with caplog.at_level(logging.INFO):
        logger.info("csrf %s %r", token, token)

    assert isinstance(token, CsrfToken)
    assert raw_token not in str(token)
    assert raw_token not in repr(token)
    assert raw_token not in caplog.text
    assert "redacted" in caplog.text


def test_verify_csrf_token_uses_constant_time_compare(monkeypatch) -> None:
    session = make_memory_session()
    token = get_csrf_token(session).as_form_value()
    calls: list[tuple[str, str]] = []

    def record_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return True

    monkeypatch.setattr(csrf.hmac, "compare_digest", record_compare)

    assert verify_csrf_token(
        session,
        token,
        datetime(2026, 7, 19, 10, 30, tzinfo=UTC),
    )
    assert calls == [(token, token)]


@pytest.mark.parametrize("submitted_token", [None, "", "wrong-token"])
def test_missing_empty_or_wrong_csrf_token_is_invalid(
    submitted_token: str | None,
) -> None:
    session = make_memory_session()

    assert (
        verify_csrf_token(
            session,
            submitted_token,
            datetime(2026, 7, 19, 10, 30, tzinfo=UTC),
        )
        is False
    )


@pytest.mark.integration
def test_same_session_csrf_token_is_valid_and_not_session_cookie(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    settings = make_settings()
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)

    csrf_token = get_csrf_token(created.session)

    assert csrf_token.as_form_value() != created.raw_token.as_cookie_value()
    assert verify_csrf_token(
        created.session,
        csrf_token.as_form_value(),
        now + timedelta(minutes=1),
    )


@pytest.mark.integration
def test_other_session_csrf_token_is_invalid(db_session: Session) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    settings = make_settings()
    user = commit_user(db_session)
    first = commit_authenticated_session(db_session, user, now, settings)
    second = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )

    first_token = get_csrf_token(first.session)

    assert (
        verify_csrf_token(
            second.session,
            first_token.as_form_value(),
            now + timedelta(minutes=1),
        )
        is False
    )


@pytest.mark.integration
def test_rotated_session_invalidates_old_csrf_token(db_session: Session) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    login_time = now + timedelta(minutes=2)
    settings = make_settings()
    user = commit_user(db_session)
    current = commit_authenticated_session(db_session, user, now, settings)
    old_token = get_csrf_token(current.session)
    old_csrf_secret = current.session.csrf_secret

    rotated = rotate_session(
        db_session,
        current.session,
        user.id,
        "pytest",
        login_time,
        settings=settings,
    )

    assert rotated.session.csrf_secret != old_csrf_secret
    assert (
        verify_csrf_token(
            current.session,
            old_token.as_form_value(),
            login_time,
        )
        is False
    )
    assert (
        verify_csrf_token(
            rotated.session,
            old_token.as_form_value(),
            login_time,
        )
        is False
    )
    assert verify_csrf_token(
        rotated.session,
        get_csrf_token(rotated.session).as_form_value(),
        login_time,
    )


@pytest.mark.integration
def test_revoked_session_csrf_token_is_invalid(db_session: Session) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    settings = make_settings()
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    token = get_csrf_token(created.session)

    revoke_session(db_session, created.session, now + timedelta(minutes=1))

    assert (
        verify_csrf_token(
            created.session,
            token.as_form_value(),
            now + timedelta(minutes=2),
        )
        is False
    )


@pytest.mark.integration
def test_expired_session_csrf_token_is_invalid(db_session: Session) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    settings = make_settings()
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    token = get_csrf_token(created.session)
    created.session.expires_at = now

    assert (
        verify_csrf_token(
            created.session,
            token.as_form_value(),
            now,
        )
        is False
    )
