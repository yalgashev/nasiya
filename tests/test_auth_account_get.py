import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.router import mask_phone_for_display
from app.auth.service import create_user
from app.auth.sessions import (
    CreatedSession,
    create_anonymous_session,
    create_authenticated_session,
    hash_session_token,
    revoke_session,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-account-get"


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


def commit_user(db_session: Session, phone: str = "+998901234567") -> User:
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
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-account",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def commit_anonymous_session(
    db_session: Session,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_anonymous_session(
        db_session,
        "pytest-account",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def fetch_session_by_cookie(db_session: Session, raw_cookie: str) -> AuthSession:
    session = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(raw_cookie)
        )
    )
    assert session is not None
    return session


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def assert_auth_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_delete_cookie(response, settings: Settings) -> None:
    set_cookie = response.headers["set-cookie"]

    assert set_cookie.startswith(f"{settings.session_cookie_name}=")
    assert "Max-Age=0" in set_cookie
    assert "Path=/" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_get_account_renders_masked_phone_logout_csrf_and_sessions_link(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    raw_cookie = created.raw_token.as_cookie_value()
    csrf_secret = created.session.csrf_secret
    password_hash = user.password_hash
    assert password_hash is not None
    set_client_session_cookie(client, settings, raw_cookie)

    response = client.get("/auth/account")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_auth_security_headers(response)
    visible_html = unescape(response.text)
    assert mask_phone_for_display(user.phone) in visible_html
    assert user.phone not in visible_html
    assert '<form method="post" action="/auth/logout">' in response.text
    assert extract_hidden_csrf_token(response.text) == (
        get_csrf_token(created.session).as_form_value()
    )
    assert 'href="/auth/sessions"' in response.text
    assert raw_cookie not in response.text
    assert created.session.token_hash not in response.text
    assert csrf_secret not in response.text
    assert password_hash not in response.text
    assert "password_hash" not in response.text
    assert "Password123" not in response.text
    assert "shop" not in response.text.casefold()
    assert "customer" not in response.text.casefold()
    assert "dashboard" not in response.text.casefold()
    assert "<script" not in response.text.casefold()
    assert "<style" not in response.text.casefold()
    assert "style=" not in response.text.casefold()


@pytest.mark.parametrize("with_anonymous_cookie", [False, True])
def test_get_account_redirects_missing_or_anonymous_session_to_login(
    m2_test_database: Engine,
    db_session: Session,
    with_anonymous_cookie: bool,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    if with_anonymous_cookie:
        created = commit_anonymous_session(db_session, now, settings)
        set_client_session_cookie(
            client,
            settings,
            created.raw_token.as_cookie_value(),
        )

    response = client.get("/auth/account", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_auth_security_headers(response)
    assert "set-cookie" not in response.headers


def test_get_account_redirects_expired_session_with_session_expired_semantics(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    now = created_at + timedelta(minutes=31)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, created_at, settings)
    created.session.expires_at = now - timedelta(seconds=1)
    db_session.commit()
    set_client_session_cookie(client, settings, created.raw_token.as_cookie_value())

    response = client.get("/auth/account", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.SESSION_EXPIRED.value
    assert_delete_cookie(response, settings)
    assert_auth_security_headers(response)


@pytest.mark.parametrize("revoked", [False, True])
def test_get_account_clears_invalid_or_revoked_cookie(
    m2_test_database: Engine,
    db_session: Session,
    revoked: bool,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    if revoked:
        user = commit_user(db_session)
        created = commit_authenticated_session(db_session, user, now, settings)
        revoke_session(db_session, created.session, now)
        db_session.commit()
        raw_cookie = created.raw_token.as_cookie_value()
    else:
        raw_cookie = "unknown-session-token"
    set_client_session_cookie(client, settings, raw_cookie)

    response = client.get("/auth/account", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_delete_cookie(response, settings)
    assert raw_cookie not in response.text
    assert_auth_security_headers(response)
