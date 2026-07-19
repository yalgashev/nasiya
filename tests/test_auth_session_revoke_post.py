from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-session-revoke"


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


def make_client(engine: Engine, now: datetime) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application), settings


def set_client_session_cookie(
    client: TestClient,
    settings: Settings,
    raw_cookie: str,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )


def commit_user(db_session: Session, phone: str) -> User:
    result = create_user(db_session, phone, "Password123")
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
    user_agent: str,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        user_agent,
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def create_logged_in_client(
    engine: Engine,
    db_session: Session,
    now: datetime,
) -> tuple[TestClient, Settings, User, CreatedSession]:
    client, settings = make_client(engine, now)
    user = commit_user(db_session, "+998901234567")
    current = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        "current-agent",
    )
    set_client_session_cookie(client, settings, current.raw_token.as_cookie_value())
    return client, settings, user, current


def post_revoke(
    client: TestClient,
    session_id,
    csrf_token: str,
):
    return client.post(
        f"/auth/sessions/{session_id}/revoke",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )


def post_revoke_others(client: TestClient, csrf_token: str):
    return client.post(
        "/auth/sessions/revoke-others",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )


def assert_auth_security_headers(response) -> None:
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_no_store(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL


def assert_delete_cookie(response, settings: Settings) -> None:
    set_cookie = response.headers["set-cookie"]

    assert set_cookie.startswith(f"{settings.session_cookie_name}=")
    assert "Max-Age=0" in set_cookie
    assert "Path=/" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def assert_session_revoked_at(
    db_session: Session,
    session: AuthSession,
    now: datetime,
) -> None:
    db_session.expire_all()
    stored_session = db_session.get(AuthSession, session.id)
    assert stored_session is not None
    assert stored_session.revoked_at == now


def assert_session_active(db_session: Session, session: AuthSession) -> None:
    db_session.expire_all()
    stored_session = db_session.get(AuthSession, session.id)
    assert stored_session is not None
    assert stored_session.revoked_at is None


def test_post_revoke_own_other_session_redirects_to_sessions_page(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    other = commit_authenticated_session(db_session, user, now, settings, "other")
    csrf_token = get_csrf_token(current.session).as_form_value()

    response = post_revoke(client, other.session.id, csrf_token)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/sessions"
    assert_no_store(response)
    assert_auth_security_headers(response)
    assert_session_revoked_at(db_session, other.session, now)
    assert_session_active(db_session, current.session)
    assert other.raw_token.as_cookie_value() not in response.text
    assert other.session.token_hash not in response.text


def test_post_revoke_other_user_session_is_safe_not_found_and_idor_protected(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, _user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    other_user = commit_user(db_session, "+998901234568")
    other_user_session = commit_authenticated_session(
        db_session,
        other_user,
        now,
        settings,
        "other-user",
    )

    response = post_revoke(
        client,
        other_user_session.session.id,
        get_csrf_token(current.session).as_form_value(),
    )

    assert response.status_code == 404
    assert_no_store(response)
    assert_auth_security_headers(response)
    assert_session_active(db_session, other_user_session.session)
    assert str(other_user_session.session.id) not in response.text
    assert other_user_session.raw_token.as_cookie_value() not in response.text
    assert other_user_session.session.token_hash not in response.text


def test_post_revoke_unknown_uuid_is_safe_not_found(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings, _user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    unknown_session_id = uuid4()

    response = post_revoke(
        client,
        unknown_session_id,
        get_csrf_token(current.session).as_form_value(),
    )

    assert response.status_code == 404
    assert_no_store(response)
    assert_auth_security_headers(response)
    assert_session_active(db_session, current.session)
    assert str(unknown_session_id) not in response.text


def test_post_revoke_current_session_deletes_cookie_and_redirects_login(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, _user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    raw_cookie = current.raw_token.as_cookie_value()

    response = post_revoke(
        client,
        current.session.id,
        get_csrf_token(current.session).as_form_value(),
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert_delete_cookie(response, settings)
    assert_no_store(response)
    assert_auth_security_headers(response)
    assert_session_revoked_at(db_session, current.session, now)
    assert raw_cookie not in response.text
    assert current.session.token_hash not in response.text


def test_post_revoke_repeated_revoke_is_safe_not_found(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    other = commit_authenticated_session(db_session, user, now, settings, "other")
    csrf_token = get_csrf_token(current.session).as_form_value()
    first_response = post_revoke(client, other.session.id, csrf_token)
    assert first_response.status_code == 303

    second_response = post_revoke(client, other.session.id, csrf_token)

    assert second_response.status_code == 303
    assert second_response.headers["location"] == "/auth/sessions"
    assert_session_revoked_at(db_session, other.session, now)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"csrf_token": "wrong-csrf-token"},
    ],
)
def test_post_revoke_missing_or_wrong_csrf_fails_and_keeps_session_active(
    m2_test_database: Engine,
    db_session: Session,
    data: dict[str, str],
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    other = commit_authenticated_session(db_session, user, now, settings, "other")

    response = client.post(
        f"/auth/sessions/{other.session.id}/revoke",
        data=data,
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_auth_security_headers(response)
    assert_session_active(db_session, current.session)
    assert_session_active(db_session, other.session)
    assert "wrong-csrf-token" not in response.text
    assert other.session.token_hash not in response.text


def test_post_revoke_others_keeps_current_and_revokes_same_user_active_sessions(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    first_other = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        "first-other",
    )
    second_other = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        "second-other",
    )
    expired = commit_authenticated_session(db_session, user, now, settings, "expired")
    expired.session.expires_at = now - timedelta(seconds=1)
    db_session.commit()

    response = post_revoke_others(
        client,
        get_csrf_token(current.session).as_form_value(),
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/sessions"
    assert_no_store(response)
    assert_auth_security_headers(response)
    assert_session_active(db_session, current.session)
    assert_session_revoked_at(db_session, first_other.session, now)
    assert_session_revoked_at(db_session, second_other.session, now)
    assert_session_active(db_session, expired.session)


def test_post_revoke_others_leaves_other_user_sessions_untouched(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, _user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    other_user = commit_user(db_session, "+998901234568")
    other_user_session = commit_authenticated_session(
        db_session,
        other_user,
        now,
        settings,
        "other-user",
    )

    response = post_revoke_others(
        client,
        get_csrf_token(current.session).as_form_value(),
    )

    assert response.status_code == 303
    assert_session_active(db_session, current.session)
    assert_session_active(db_session, other_user_session.session)


def test_post_revoke_others_without_csrf_revokes_nothing(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    first_other = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        "first-other",
    )
    second_other = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        "second-other",
    )

    response = client.post(
        "/auth/sessions/revoke-others",
        data={},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_auth_security_headers(response)
    assert_session_active(db_session, current.session)
    assert_session_active(db_session, first_other.session)
    assert_session_active(db_session, second_other.session)


def test_post_revoke_others_repeated_request_is_safe(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, user, current = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    other = commit_authenticated_session(db_session, user, now, settings, "other")
    csrf_token = get_csrf_token(current.session).as_form_value()
    first_response = post_revoke_others(client, csrf_token)
    assert first_response.status_code == 303

    second_response = post_revoke_others(client, csrf_token)

    assert second_response.status_code == 303
    assert second_response.headers["location"] == "/auth/sessions"
    assert_session_active(db_session, current.session)
    assert_session_revoked_at(db_session, other.session, now)
