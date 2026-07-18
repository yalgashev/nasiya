from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import Session as AuthSession
from app.auth.service import create_user
from app.auth.sessions import (
    UserSessionStatus,
    create_anonymous_session,
    create_authenticated_session,
    create_session_token,
    hash_session_token,
    list_user_sessions,
    resolve_by_raw_token,
    revoke_other_sessions,
    revoke_session,
    revoke_user_session,
    rotate_session,
    touch_session,
)
from app.auth.user_agent import MAX_USER_AGENT_LENGTH
from app.db import create_database_session_factory
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-session-service"


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
    anonymous_session_ttl_minutes: int = 30,
    session_ttl_days: int = 30,
    session_touch_interval_minutes: int = 5,
) -> Settings:
    return Settings(
        app_environment="testing",
        debug=True,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=False,
        anonymous_session_ttl_minutes=anonymous_session_ttl_minutes,
        session_ttl_days=session_ttl_days,
        session_touch_interval_minutes=session_touch_interval_minutes,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def count_sessions(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            select(func.count()).select_from(AuthSession)
        ).scalar_one()


def fetch_stored_sessions(engine: Engine) -> list[AuthSession]:
    session_factory = create_database_session_factory(engine)
    with session_factory() as session:
        statement = select(AuthSession).order_by(AuthSession.created_at)
        return list(session.scalars(statement))


def fetch_stored_session(engine: Engine) -> AuthSession:
    stored_sessions = fetch_stored_sessions(engine)
    assert len(stored_sessions) == 1
    return stored_sessions[0]


def fetch_stored_session_by_id(engine: Engine, session_id: UUID) -> AuthSession:
    session_factory = create_database_session_factory(engine)
    with session_factory() as session:
        stored_session = session.get(AuthSession, session_id)
        assert stored_session is not None
        return stored_session


def commit_user(db_session: Session, phone: str = "+998901234567"):
    result = create_user(db_session, phone, "Password123")
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


@pytest.mark.integration
def test_create_anonymous_session_stores_null_user_id_without_committing(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(anonymous_session_ttl_minutes=45)

    created = create_anonymous_session(db_session, "pytest", now, settings=settings)

    assert created.session.user_id is None
    assert created.session.created_at == now
    assert created.session.last_seen_at == now
    assert created.session.expires_at == now + timedelta(minutes=45)
    assert count_sessions(m2_test_database) == 0

    db_session.commit()

    stored_session = fetch_stored_sessions(m2_test_database)[0]
    assert stored_session.user_id is None
    assert stored_session.token_hash == hash_session_token(created.raw_token)


@pytest.mark.integration
def test_create_authenticated_session_stores_user_id_without_committing(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(session_ttl_days=14)

    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )

    assert created.session.user_id == user.id
    assert created.session.expires_at == now + timedelta(days=14)
    assert count_sessions(m2_test_database) == 0

    db_session.commit()

    stored_session = fetch_stored_sessions(m2_test_database)[0]
    assert stored_session.user_id == user.id
    assert stored_session.token_hash == hash_session_token(created.raw_token)


@pytest.mark.integration
def test_raw_session_token_is_not_stored_in_database(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    created = create_anonymous_session(
        db_session,
        "pytest",
        now,
        settings=make_settings(),
    )
    raw_token = created.raw_token.as_cookie_value()

    db_session.commit()

    stored_session = fetch_stored_sessions(m2_test_database)[0]
    assert stored_session.token_hash != raw_token
    assert raw_token not in stored_session.token_hash
    assert "token" not in AuthSession.__table__.columns
    assert "raw_session_token" not in AuthSession.__table__.columns


@pytest.mark.integration
def test_create_session_stores_safe_user_agent_and_lists_display_metadata(
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    raw_user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
        + ("x" * MAX_USER_AGENT_LENGTH)
    )

    created = create_authenticated_session(
        db_session,
        user.id,
        raw_user_agent,
        now,
        settings=make_settings(),
    )

    assert created.session.user_agent == raw_user_agent[:MAX_USER_AGENT_LENGTH]

    db_session.commit()

    summary = list_user_sessions(db_session, user.id, now)[0]
    assert summary.user_agent == raw_user_agent[:MAX_USER_AGENT_LENGTH]
    assert summary.browser_label == "Chrome"
    assert summary.device_label == "Windows"


@pytest.mark.integration
def test_two_sessions_have_different_tokens_and_csrf_secrets(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings()

    first = create_anonymous_session(db_session, "pytest", now, settings=settings)
    second = create_anonymous_session(db_session, "pytest", now, settings=settings)

    assert first.raw_token.as_cookie_value() != second.raw_token.as_cookie_value()
    assert first.session.token_hash != second.session.token_hash
    assert first.session.csrf_secret != second.session.csrf_secret


@pytest.mark.integration
def test_session_expiry_uses_settings_ttl(
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(
        anonymous_session_ttl_minutes=17,
        session_ttl_days=9,
    )

    anonymous = create_anonymous_session(db_session, "pytest", now, settings=settings)
    authenticated = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )

    assert anonymous.session.expires_at == now + timedelta(minutes=17)
    assert authenticated.session.expires_at == now + timedelta(days=9)


@pytest.mark.integration
def test_resolve_active_authenticated_session_returns_session_and_user(
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=make_settings(),
    )
    db_session.commit()

    resolved = resolve_by_raw_token(
        db_session,
        created.raw_token,
        now + timedelta(minutes=1),
    )

    assert resolved is not None
    assert resolved.session.id == created.session.id
    assert resolved.authenticated_user is not None
    assert resolved.authenticated_user.id == user.id


@pytest.mark.integration
def test_resolve_unknown_session_token_returns_none(db_session: Session) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)

    assert resolve_by_raw_token(db_session, create_session_token(), now) is None


@pytest.mark.integration
def test_resolve_expired_session_returns_none(db_session: Session) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    created = create_anonymous_session(
        db_session,
        "pytest",
        now,
        settings=make_settings(anonymous_session_ttl_minutes=1),
    )
    db_session.commit()

    resolved = resolve_by_raw_token(
        db_session,
        created.raw_token,
        now + timedelta(minutes=1),
    )

    assert resolved is None


@pytest.mark.integration
def test_resolve_revoked_session_returns_none(db_session: Session) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    created = create_anonymous_session(
        db_session,
        "pytest",
        now,
        settings=make_settings(),
    )
    db_session.commit()

    revoke_session(db_session, created.session, now + timedelta(minutes=1))
    db_session.commit()

    resolved = resolve_by_raw_token(
        db_session,
        created.raw_token,
        now + timedelta(minutes=2),
    )

    assert resolved is None
    assert created.session.revoked_at == now + timedelta(minutes=1)


@pytest.mark.integration
def test_resolve_inactive_user_session_returns_none(db_session: Session) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=make_settings(),
    )
    db_session.commit()

    user.is_active = False
    db_session.commit()

    assert resolve_by_raw_token(db_session, created.raw_token, now) is None


@pytest.mark.integration
def test_resolve_anonymous_session_does_not_authenticate_user(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    created = create_anonymous_session(
        db_session,
        "pytest",
        now,
        settings=make_settings(),
    )
    db_session.commit()

    resolved = resolve_by_raw_token(db_session, created.raw_token, now)

    assert resolved is not None
    assert resolved.session.user_id is None
    assert resolved.authenticated_user is None


@pytest.mark.integration
def test_resolve_error_message_does_not_include_token_material(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    created = create_anonymous_session(
        db_session,
        "pytest",
        now,
        settings=make_settings(),
    )
    db_session.commit()
    raw_token = created.raw_token.as_cookie_value()
    token_hash = hash_session_token(created.raw_token)

    with pytest.raises(ValueError) as exc_info:
        resolve_by_raw_token(db_session, created.raw_token, datetime(2026, 7, 18))

    assert raw_token not in str(exc_info.value)
    assert raw_token not in repr(exc_info.value)
    assert token_hash not in str(exc_info.value)
    assert token_hash not in repr(exc_info.value)


@pytest.mark.integration
def test_touch_session_is_noop_inside_touch_interval(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(
        session_ttl_days=10,
        session_touch_interval_minutes=5,
    )
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )
    db_session.commit()
    stored_before = fetch_stored_session(m2_test_database)
    touch_time = now + timedelta(minutes=4)

    touched = touch_session(db_session, created.session, touch_time, settings=settings)

    assert touched is False
    assert created.session not in db_session.dirty

    db_session.commit()

    stored_after = fetch_stored_session(m2_test_database)
    assert stored_after.last_seen_at == stored_before.last_seen_at
    assert stored_after.expires_at == stored_before.expires_at


@pytest.mark.integration
def test_touch_session_updates_after_touch_interval_without_committing(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(
        session_ttl_days=10,
        session_touch_interval_minutes=5,
    )
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )
    db_session.commit()
    stored_before = fetch_stored_session(m2_test_database)
    touch_time = now + timedelta(minutes=6)

    touched = touch_session(db_session, created.session, touch_time, settings=settings)

    assert touched is True
    assert created.session.last_seen_at == touch_time
    assert created.session.expires_at == touch_time + timedelta(days=10)
    assert fetch_stored_session(m2_test_database).last_seen_at == (
        stored_before.last_seen_at
    )

    db_session.commit()

    stored_after = fetch_stored_session(m2_test_database)
    assert stored_after.last_seen_at == touch_time
    assert stored_after.expires_at == touch_time + timedelta(days=10)


@pytest.mark.integration
def test_touch_session_moves_expiry_forward(
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(
        session_ttl_days=10,
        session_touch_interval_minutes=5,
    )
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )
    db_session.commit()
    original_expires_at = created.session.expires_at
    touch_time = now + timedelta(hours=1)

    touched = touch_session(db_session, created.session, touch_time, settings=settings)

    assert touched is True
    assert created.session.expires_at > original_expires_at
    assert created.session.expires_at == touch_time + timedelta(days=10)


@pytest.mark.integration
def test_touch_session_does_not_extend_revoked_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(session_touch_interval_minutes=5)
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )
    db_session.commit()

    revoke_session(db_session, created.session, now + timedelta(minutes=10))
    db_session.commit()
    stored_before = fetch_stored_session(m2_test_database)

    touched = touch_session(
        db_session,
        created.session,
        now + timedelta(minutes=20),
        settings=settings,
    )
    db_session.commit()

    stored_after = fetch_stored_session(m2_test_database)
    assert touched is False
    assert stored_after.last_seen_at == stored_before.last_seen_at
    assert stored_after.expires_at == stored_before.expires_at
    assert stored_after.revoked_at == stored_before.revoked_at


@pytest.mark.integration
def test_touch_session_does_not_extend_expired_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(session_touch_interval_minutes=5)
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        now,
        settings=settings,
    )
    created.session.expires_at = now - timedelta(seconds=1)
    db_session.commit()
    stored_before = fetch_stored_session(m2_test_database)

    touched = touch_session(
        db_session,
        created.session,
        now + timedelta(minutes=20),
        settings=settings,
    )
    db_session.commit()

    stored_after = fetch_stored_session(m2_test_database)
    assert touched is False
    assert stored_after.last_seen_at == stored_before.last_seen_at
    assert stored_after.expires_at == stored_before.expires_at


@pytest.mark.integration
def test_touch_session_does_not_extend_anonymous_session_to_authenticated_ttl(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(
        anonymous_session_ttl_minutes=30,
        session_ttl_days=30,
        session_touch_interval_minutes=5,
    )
    created = create_anonymous_session(db_session, "pytest", now, settings=settings)
    db_session.commit()
    stored_before = fetch_stored_session(m2_test_database)

    touched = touch_session(
        db_session,
        created.session,
        now + timedelta(minutes=10),
        settings=settings,
    )
    db_session.commit()

    stored_after = fetch_stored_session(m2_test_database)
    assert touched is False
    assert stored_after.last_seen_at == stored_before.last_seen_at
    assert stored_after.expires_at == stored_before.expires_at


@pytest.mark.integration
def test_rotate_anonymous_session_on_login_invalidates_old_token_and_authenticates_new(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    login_time = now + timedelta(minutes=2)
    settings = make_settings(session_ttl_days=12)
    anonymous = create_anonymous_session(
        db_session,
        "anonymous-agent",
        now,
        settings=settings,
    )
    db_session.commit()
    old_token_hash = anonymous.session.token_hash
    old_csrf_secret = anonymous.session.csrf_secret
    resolved_anonymous = resolve_by_raw_token(db_session, anonymous.raw_token, now)
    assert resolved_anonymous is not None

    rotated = rotate_session(
        db_session,
        resolved_anonymous.session,
        user.id,
        "login-agent",
        login_time,
        settings=settings,
    )

    assert rotated.raw_token.as_cookie_value() != anonymous.raw_token.as_cookie_value()
    assert rotated.session.token_hash != old_token_hash
    assert rotated.session.csrf_secret != old_csrf_secret
    assert rotated.session.user_agent == "login-agent"
    assert resolve_by_raw_token(db_session, anonymous.raw_token, login_time) is None
    new_resolved = resolve_by_raw_token(db_session, rotated.raw_token, login_time)
    assert new_resolved is not None
    assert new_resolved.authenticated_user is not None
    assert new_resolved.authenticated_user.id == user.id
    assert count_sessions(m2_test_database) == 1

    db_session.commit()

    stored_sessions = fetch_stored_sessions(m2_test_database)
    assert len(stored_sessions) == 2
    old_session = next(
        session for session in stored_sessions if session.token_hash == old_token_hash
    )
    new_session = next(
        session
        for session in stored_sessions
        if session.token_hash == rotated.session.token_hash
    )
    assert old_session.revoked_at == login_time
    assert new_session.revoked_at is None
    assert new_session.user_id == user.id
    assert new_session.user_agent == "login-agent"
    assert resolve_by_raw_token(db_session, anonymous.raw_token, login_time) is None
    assert resolve_by_raw_token(db_session, rotated.raw_token, login_time) is not None


@pytest.mark.integration
def test_rotate_authenticated_session_on_relogin_invalidates_old_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session)
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    relogin_time = now + timedelta(minutes=3)
    settings = make_settings(session_ttl_days=15)
    authenticated = create_authenticated_session(
        db_session,
        user.id,
        "old-agent",
        now,
        settings=settings,
    )
    db_session.commit()
    old_token_hash = authenticated.session.token_hash
    old_csrf_secret = authenticated.session.csrf_secret
    resolved_authenticated = resolve_by_raw_token(
        db_session,
        authenticated.raw_token,
        now,
    )
    assert resolved_authenticated is not None

    rotated = rotate_session(
        db_session,
        resolved_authenticated.session,
        user.id,
        "new-agent",
        relogin_time,
        settings=settings,
    )

    assert rotated.raw_token.as_cookie_value() != (
        authenticated.raw_token.as_cookie_value()
    )
    assert rotated.session.token_hash != old_token_hash
    assert rotated.session.csrf_secret != old_csrf_secret
    assert rotated.session.user_agent == "new-agent"
    assert (
        resolve_by_raw_token(db_session, authenticated.raw_token, relogin_time)
        is None
    )
    new_resolved = resolve_by_raw_token(db_session, rotated.raw_token, relogin_time)
    assert new_resolved is not None
    assert new_resolved.authenticated_user is not None
    assert new_resolved.authenticated_user.id == user.id
    assert count_sessions(m2_test_database) == 1

    db_session.commit()

    stored_sessions = fetch_stored_sessions(m2_test_database)
    assert len(stored_sessions) == 2
    old_session = next(
        session for session in stored_sessions if session.token_hash == old_token_hash
    )
    new_session = next(
        session
        for session in stored_sessions
        if session.token_hash == rotated.session.token_hash
    )
    assert old_session.revoked_at == relogin_time
    assert new_session.revoked_at is None
    assert new_session.user_id == user.id
    assert new_session.user_agent == "new-agent"
    assert (
        resolve_by_raw_token(db_session, authenticated.raw_token, relogin_time)
        is None
    )
    assert resolve_by_raw_token(db_session, rotated.raw_token, relogin_time) is not None


@pytest.mark.integration
def test_list_user_sessions_returns_only_owned_sessions_with_statuses(
    db_session: Session,
) -> None:
    user = commit_user(db_session, "+998901234567")
    other_user = commit_user(db_session, "+998901234568")
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings(session_ttl_days=30)
    active = create_authenticated_session(
        db_session,
        user.id,
        "active-agent",
        now,
        settings=settings,
    )
    expired = create_authenticated_session(
        db_session,
        user.id,
        "expired-agent",
        now,
        settings=settings,
    )
    expired.session.expires_at = now - timedelta(seconds=1)
    revoked = create_authenticated_session(
        db_session,
        user.id,
        "revoked-agent",
        now,
        settings=settings,
    )
    revoke_session(db_session, revoked.session, now + timedelta(minutes=1))
    other_user_session = create_authenticated_session(
        db_session,
        other_user.id,
        "other-agent",
        now,
        settings=settings,
    )
    anonymous = create_anonymous_session(
        db_session,
        "anon-agent",
        now,
        settings=settings,
    )
    db_session.commit()

    summaries = list_user_sessions(db_session, user.id, now)
    status_by_id = {summary.session_id: summary.status for summary in summaries}

    assert set(status_by_id) == {
        active.session.id,
        expired.session.id,
        revoked.session.id,
    }
    assert status_by_id[active.session.id] == UserSessionStatus.ACTIVE
    assert status_by_id[expired.session.id] == UserSessionStatus.EXPIRED
    assert status_by_id[revoked.session.id] == UserSessionStatus.REVOKED
    assert other_user_session.session.id not in status_by_id
    assert anonymous.session.id not in status_by_id
    assert all(isinstance(summary.session_id, UUID) for summary in summaries)
    assert all(not hasattr(summary, "token_hash") for summary in summaries)


@pytest.mark.integration
def test_revoke_user_session_revokes_owned_session_without_committing(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session, "+998901234567")
    other_user = commit_user(db_session, "+998901234568")
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    revoke_time = now + timedelta(minutes=5)
    settings = make_settings()
    owned = create_authenticated_session(
        db_session,
        user.id,
        "owned-agent",
        now,
        settings=settings,
    )
    other = create_authenticated_session(
        db_session,
        other_user.id,
        "other-agent",
        now,
        settings=settings,
    )
    db_session.commit()

    assert (
        revoke_user_session(db_session, user.id, other.session.id, revoke_time)
        is False
    )
    assert other.session.revoked_at is None

    revoked = revoke_user_session(db_session, user.id, owned.session.id, revoke_time)

    assert revoked is True
    assert owned.session.revoked_at == revoke_time
    assert (
        fetch_stored_session_by_id(m2_test_database, owned.session.id).revoked_at
        is None
    )

    db_session.commit()

    assert (
        fetch_stored_session_by_id(m2_test_database, owned.session.id).revoked_at
        == revoke_time
    )
    assert (
        fetch_stored_session_by_id(m2_test_database, other.session.id).revoked_at
        is None
    )


@pytest.mark.integration
def test_revoke_other_sessions_keeps_current_session_and_scope(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = commit_user(db_session, "+998901234567")
    other_user = commit_user(db_session, "+998901234568")
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    revoke_time = now + timedelta(minutes=5)
    settings = make_settings()
    current = create_authenticated_session(
        db_session,
        user.id,
        "current-agent",
        now,
        settings=settings,
    )
    first_other = create_authenticated_session(
        db_session,
        user.id,
        "first-other-agent",
        now,
        settings=settings,
    )
    second_other = create_authenticated_session(
        db_session,
        user.id,
        "second-other-agent",
        now,
        settings=settings,
    )
    other_user_session = create_authenticated_session(
        db_session,
        other_user.id,
        "other-user-agent",
        now,
        settings=settings,
    )
    db_session.commit()

    revoked_count = revoke_other_sessions(
        db_session,
        user.id,
        current.session.id,
        revoke_time,
    )

    assert revoked_count == 2
    assert current.session.revoked_at is None
    assert first_other.session.revoked_at == revoke_time
    assert second_other.session.revoked_at == revoke_time
    assert other_user_session.session.revoked_at is None
    assert (
        fetch_stored_session_by_id(m2_test_database, first_other.session.id).revoked_at
        is None
    )

    db_session.commit()

    assert fetch_stored_session_by_id(
        m2_test_database,
        current.session.id,
    ).revoked_at is None
    assert (
        fetch_stored_session_by_id(m2_test_database, first_other.session.id).revoked_at
        == revoke_time
    )
    assert (
        fetch_stored_session_by_id(m2_test_database, second_other.session.id).revoked_at
        == revoke_time
    )
    assert fetch_stored_session_by_id(
        m2_test_database,
        other_user_session.session.id,
    ).revoked_at is None


@pytest.mark.integration
def test_revoke_other_sessions_requires_current_session_owned_by_user(
    db_session: Session,
) -> None:
    user = commit_user(db_session, "+998901234567")
    other_user = commit_user(db_session, "+998901234568")
    now = datetime(2026, 7, 18, 10, 30, tzinfo=UTC)
    settings = make_settings()
    owned = create_authenticated_session(
        db_session,
        user.id,
        "owned-agent",
        now,
        settings=settings,
    )
    other_user_session = create_authenticated_session(
        db_session,
        other_user.id,
        "other-user-agent",
        now,
        settings=settings,
    )
    db_session.commit()

    revoked_count = revoke_other_sessions(
        db_session,
        user.id,
        other_user_session.session.id,
        now + timedelta(minutes=5),
    )

    assert revoked_count == 0
    assert owned.session.revoked_at is None
    assert other_user_session.session.revoked_at is None
