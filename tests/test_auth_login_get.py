import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.router import router as auth_router
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

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-login-get"


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
    app_environment: str = "testing",
    session_cookie_secure: bool = False,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment=app_environment,
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=session_cookie_secure,
        anonymous_session_ttl_minutes=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_client(
    engine: Engine,
    now: datetime,
    *,
    app_environment: str = "testing",
    session_cookie_secure: bool = False,
) -> tuple[TestClient, Settings]:
    settings = make_settings(
        engine,
        app_environment=app_environment,
        session_cookie_secure=session_cookie_secure,
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application), settings


def set_client_session_cookie(
    client: TestClient,
    settings: Settings,
    created: CreatedSession,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )


def commit_user(db_session: Session) -> User:
    result = create_user(db_session, "+998901234567", "Password123")
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_anonymous_session(
    db_session: Session,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_anonymous_session(
        db_session,
        "pytest-login",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-login",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def count_sessions(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(AuthSession)) or 0


def fetch_only_session(db_session: Session) -> AuthSession:
    session = db_session.scalar(select(AuthSession))
    assert session is not None
    return session


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


def assert_login_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_login_cookie_flags(
    response,
    settings: Settings,
    *,
    secure: bool,
) -> None:
    set_cookie = response.headers["set-cookie"]

    assert set_cookie.startswith(f"{settings.session_cookie_name}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert ("Secure" in set_cookie) is secure


def test_get_login_creates_anonymous_session_and_renders_form(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_login_security_headers(response)
    assert settings.session_cookie_name in response.headers["set-cookie"]
    assert_login_cookie_flags(response, settings, secure=False)
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    stored_session = fetch_only_session(db_session)
    assert stored_session.user_id is None
    assert stored_session.token_hash == hash_session_token(raw_cookie)
    assert raw_cookie not in stored_session.token_hash
    assert extract_hidden_csrf_token(response.text) == (
        get_csrf_token(stored_session).as_form_value()
    )
    assert '<label for="phone">Telefon raqam</label>' in response.text
    assert 'name="phone"' in response.text
    assert 'type="tel"' in response.text
    assert '<label for="password">Parol</label>' in response.text
    assert 'name="password"' in response.text
    assert 'type="password"' in response.text
    assert 'id="login-error"' in response.text
    assert 'role="alert"' in response.text
    assert ">Kirish</button>" in response.text


def test_get_login_reuses_valid_anonymous_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    csrf_token = get_csrf_token(created.session).as_form_value()
    set_client_session_cookie(client, settings, created)

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert count_sessions(db_session) == 1
    assert client.cookies.get(settings.session_cookie_name) == (
        created.raw_token.as_cookie_value()
    )
    assert extract_hidden_csrf_token(response.text) == csrf_token
    assert_login_security_headers(response)


@pytest.mark.parametrize("expired", [True, False])
def test_get_login_replaces_expired_or_revoked_anonymous_session(
    m2_test_database: Engine,
    db_session: Session,
    expired: bool,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now - timedelta(hours=1), settings)
    old_cookie = created.raw_token.as_cookie_value()
    if expired:
        created.session.expires_at = now - timedelta(seconds=1)
    else:
        revoke_session(db_session, created.session, now - timedelta(seconds=1))
    db_session.commit()
    set_client_session_cookie(client, settings, created)

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert count_sessions(db_session) == 2
    new_cookie = client.cookies.get(settings.session_cookie_name)
    assert new_cookie is not None
    assert new_cookie != old_cookie
    new_session = fetch_session_by_cookie(db_session, new_cookie)
    assert new_session.user_id is None
    assert extract_hidden_csrf_token(response.text) == (
        get_csrf_token(new_session).as_form_value()
    )
    assert old_cookie not in response.text
    assert_login_security_headers(response)


def test_get_login_replaces_missing_or_invalid_session_with_anonymous_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    invalid_cookie = "not-a-real-session-token"
    client.cookies.set(
        settings.session_cookie_name,
        invalid_cookie,
        domain="testserver.local",
        path="/",
    )

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert count_sessions(db_session) == 1
    assert client.cookies.get(settings.session_cookie_name) != invalid_cookie
    assert invalid_cookie not in response.text


def test_get_login_sets_secure_cookie_for_production_like_settings(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(
        m2_test_database,
        now,
        app_environment="production",
        session_cookie_secure=True,
    )

    response = client.get("/auth/login")

    assert response.status_code == 200
    assert_login_cookie_flags(response, settings, secure=True)


def test_get_login_redirects_authenticated_session_to_account(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/account"
    assert_login_security_headers(response)
    assert count_sessions(db_session) == 1


def test_login_template_has_no_registration_otp_telegram_or_inline_script(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/auth/login")
    html = response.text.casefold()

    assert "<script" not in html
    assert "<style" not in html
    assert "style=" not in html
    assert "registration" not in html
    assert "ro'yxat" not in html
    assert "otp" not in html
    assert "telegram" not in html


def test_auth_login_has_get_and_post_routes() -> None:
    methods_for_login = set()
    for route in auth_router.routes:
        if isinstance(route, APIRoute) and route.path_format == "/auth/login":
            methods_for_login.update(route.methods or set())

    assert methods_for_login == {"GET", "POST"}
