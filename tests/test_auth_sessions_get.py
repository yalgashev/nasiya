import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import (
    CreatedSession,
    create_anonymous_session,
    create_authenticated_session,
    revoke_session,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-sessions-get"


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
        session_touch_interval_minutes=60,
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


def create_session(
    db_session: Session,
    user: User,
    user_agent: str,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    return create_authenticated_session(
        db_session,
        user.id,
        user_agent,
        now,
        settings=settings,
    )


def assert_auth_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def extract_hidden_csrf_tokens(html: str) -> list[str]:
    return re.findall(r'name="csrf_token"\s+value="(?P<token>[^"]+)"', html)


def test_get_sessions_lists_only_current_user_sessions_with_revoke_forms(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    session_created_at = now - timedelta(minutes=10)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    other_user = commit_user(db_session, "+998901234568")
    current = create_session(
        db_session,
        user,
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
        session_created_at,
        settings,
    )
    other_active = create_session(
        db_session,
        user,
        "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Firefox/126.0",
        session_created_at + timedelta(minutes=1),
        settings,
    )
    expired = create_session(
        db_session,
        user,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1",
        session_created_at + timedelta(minutes=2),
        settings,
    )
    expired.session.expires_at = now - timedelta(seconds=1)
    revoked = create_session(
        db_session,
        user,
        "Mozilla/5.0 (Android 13) Edg/126.0",
        session_created_at + timedelta(minutes=3),
        settings,
    )
    revoke_session(db_session, revoked.session, now - timedelta(minutes=1))
    other_user_session = create_session(
        db_session,
        other_user,
        "other-user-agent",
        session_created_at,
        settings,
    )
    anonymous = create_anonymous_session(
        db_session,
        "anonymous-agent",
        session_created_at,
        settings=settings,
    )
    db_session.commit()
    set_client_session_cookie(
        client,
        settings,
        current.raw_token.as_cookie_value(),
    )

    response = client.get("/auth/sessions")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_auth_security_headers(response)
    visible_html = unescape(response.text)
    assert "Joriy sessiya" in visible_html
    assert 'class="badge"' in response.text
    assert "Chrome - Windows" in visible_html
    assert "Firefox - Linux" in visible_html
    assert "Safari - iOS" in visible_html
    assert "Edge - Android" in visible_html
    assert "Faol" in visible_html
    assert "Muddati tugagan" in visible_html
    assert "Bekor qilingan" in visible_html
    assert "2026-07-19 10:20 UTC" in visible_html
    assert "2026-08-18 10:20 UTC" in visible_html
    assert f'action="/auth/sessions/{other_active.session.id}/revoke"' in response.text
    assert f'action="/auth/sessions/{current.session.id}/revoke"' not in response.text
    assert f'action="/auth/sessions/{expired.session.id}/revoke"' not in response.text
    assert f'action="/auth/sessions/{revoked.session.id}/revoke"' not in response.text
    assert 'action="/auth/sessions/revoke-others"' in response.text
    csrf_tokens = extract_hidden_csrf_tokens(response.text)
    assert csrf_tokens
    assert set(csrf_tokens) == {get_csrf_token(current.session).as_form_value()}
    assert str(other_user_session.session.id) not in response.text
    assert "other-user-agent" not in response.text
    assert str(anonymous.session.id) not in response.text
    assert "anonymous-agent" not in response.text


def test_sessions_html_has_mobile_accessible_controls_and_no_business_placeholders(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    current = create_session(db_session, user, "Chrome/126.0 Windows", now, settings)
    other = create_session(db_session, user, "Firefox/126.0 Linux", now, settings)
    db_session.commit()
    set_client_session_cookie(client, settings, current.raw_token.as_cookie_value())

    response = client.get("/auth/sessions")
    html = response.text
    html_lower = html.casefold()
    visible_html = unescape(html)

    assert response.status_code == 200
    assert '<section aria-labelledby="session-list-heading">' in html
    assert '<h2 id="session-list-heading">' in html
    assert '<article class="session-card">' in html
    assert '<p class="badge">Joriy sessiya</p>' in html
    assert "Holat belgisi" in visible_html
    assert "Oxirgi faollik vaqti" in visible_html
    assert "Sessiya tugash vaqti" in visible_html
    assert "Ushbu sessiyani bekor qilish" in visible_html
    assert "Barcha boshqa sessiyalarni bekor qilish" in visible_html
    assert 'class="button-danger"' in html
    assert "<script" not in html_lower
    assert "<style" not in html_lower
    assert "style=" not in html_lower
    assert "shop" not in html_lower
    assert "customer" not in html_lower
    assert "dashboard" not in html_lower
    assert str(current.session.id) not in visible_html
    assert str(other.session.id) not in visible_html.replace(
        f'/auth/sessions/{other.session.id}/revoke',
        "",
    )


def test_sessions_css_has_mobile_card_focus_and_touch_target_rules() -> None:
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert ".session-card" in css
    assert "overflow-wrap: anywhere;" in css
    assert ".badge" in css
    assert ".button-danger" in css
    assert "min-height: 44px;" in css
    assert ":focus-visible" in css
    assert "@media (max-width: 430px)" in css


def test_get_sessions_does_not_leak_secret_material_or_raw_html_user_agent(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    dangerous_user_agent = "<script>alert('ua')</script> Chrome/126.0 Windows"
    current = create_session(
        db_session,
        user,
        dangerous_user_agent,
        now,
        settings,
    )
    db_session.commit()
    raw_cookie = current.raw_token.as_cookie_value()
    password_hash = user.password_hash
    assert password_hash is not None
    set_client_session_cookie(client, settings, raw_cookie)

    response = client.get("/auth/sessions")

    assert response.status_code == 200
    assert raw_cookie not in response.text
    assert current.session.token_hash not in response.text
    assert current.session.csrf_secret not in response.text
    assert password_hash not in response.text
    assert "password_hash" not in response.text
    assert "<script" not in response.text.casefold()
    assert "&lt;script&gt;alert(&#39;ua&#39;)&lt;/script&gt;" in response.text


@pytest.mark.parametrize("with_cookie", [False, True])
def test_get_sessions_requires_authenticated_user(
    m2_test_database: Engine,
    db_session: Session,
    with_cookie: bool,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    if with_cookie:
        anonymous = create_anonymous_session(
            db_session,
            "anonymous-agent",
            now,
            settings=settings,
        )
        db_session.commit()
        set_client_session_cookie(
            client,
            settings,
            anonymous.raw_token.as_cookie_value(),
        )

    response = client.get("/auth/sessions", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_auth_security_headers(response)
