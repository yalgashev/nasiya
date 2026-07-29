import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.web_presentation import OTP_LOCALE_COOKIE_NAME
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OTP_REQUEST_TEMPLATE = PROJECT_ROOT / "app/templates/auth/otp_request.html"
APP_CSS = PROJECT_ROOT / "app/static/css/app.css"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-request-get"


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
        anonymous_session_ttl_minutes=30,
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
        "pytest-otp-request",
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
        "pytest-otp-request",
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


def count_otp_rows(db_session: Session) -> tuple[int, int]:
    return (
        db_session.scalar(select(func.count()).select_from(OtpChallenge)) or 0,
        db_session.scalar(select(func.count()).select_from(OtpDispatch)) or 0,
    )


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def assert_otp_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_get_otp_request_creates_anonymous_session_and_renders_phone_form(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)

    response = client.get("/auth/otp")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_otp_security_headers(response)
    assert settings.session_cookie_name in response.headers["set-cookie"]
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    stored_session = fetch_only_session(db_session)
    assert stored_session.user_id is None
    assert stored_session.token_hash == hash_session_token(raw_cookie)
    assert extract_hidden_csrf_token(response.text) == (
        get_csrf_token(stored_session).as_form_value()
    )
    assert count_otp_rows(db_session) == (0, 0)
    assert '<main class="otp-page">' in response.text
    assert '<form method="post" action="/auth/otp/request" novalidate>' in (
        response.text
    )
    assert '<label for="otp-phone">Telefon raqam</label>' in response.text
    assert 'name="phone"' in response.text
    assert 'type="tel"' in response.text
    assert 'autocomplete="tel"' in response.text
    assert 'inputmode="tel"' in response.text
    assert 'aria-describedby="otp-phone-help"' in response.text
    assert 'id="otp-request-error"' in response.text
    assert 'role="alert"' in response.text
    assert ">Kod olish</button>" in response.text
    assert '<a href="/auth/login">Parol bilan kirish</a>' in response.text
    for forbidden in (
        "challenge_id",
        "dispatch_id",
        "telegram_chat",
        "chat_id",
        "delivery status",
        "bog'langan",
        "topilmadi",
    ):
        assert forbidden not in response.text.casefold()


def test_get_otp_request_reuses_valid_anonymous_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    csrf_token = get_csrf_token(created.session).as_form_value()
    set_client_session_cookie(client, settings, created)

    response = client.get("/auth/otp")

    assert response.status_code == 200
    assert count_sessions(db_session) == 1
    assert client.cookies.get(settings.session_cookie_name) == (
        created.raw_token.as_cookie_value()
    )
    assert extract_hidden_csrf_token(response.text) == csrf_token
    assert count_otp_rows(db_session) == (0, 0)
    assert_otp_security_headers(response)


@pytest.mark.parametrize("expired", [True, False])
def test_get_otp_request_replaces_expired_or_revoked_anonymous_session(
    m2_test_database: Engine,
    db_session: Session,
    expired: bool,
) -> None:
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now - timedelta(hours=1), settings)
    old_cookie = created.raw_token.as_cookie_value()
    if expired:
        created.session.expires_at = now - timedelta(seconds=1)
    else:
        revoke_session(db_session, created.session, now - timedelta(seconds=1))
    db_session.commit()
    set_client_session_cookie(client, settings, created)

    response = client.get("/auth/otp")

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
    assert count_otp_rows(db_session) == (0, 0)


def test_get_otp_request_redirects_authenticated_session_to_account(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/auth/otp", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/account"
    assert_otp_security_headers(response)
    assert count_sessions(db_session) == 1
    assert count_otp_rows(db_session) == (0, 0)


def test_get_otp_request_keeps_only_safe_relative_next(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    safe_response = client.get("/auth/otp", params={"next": "/customer/profile"})
    unsafe_response = client.get(
        "/auth/otp",
        params={"next": "https://example.test/customer"},
    )

    assert safe_response.status_code == 200
    assert (
        '<input type="hidden" name="next" value="/customer/profile">'
        in safe_response.text
    )
    assert unsafe_response.status_code == 200
    assert 'name="next"' not in unsafe_response.text
    assert "example.test" not in unsafe_response.text


def test_get_otp_request_uses_bounded_ru_locale_cookie(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)
    client.cookies.set(
        OTP_LOCALE_COOKIE_NAME,
        "ru",
        domain="testserver.local",
        path="/",
    )

    response = client.get("/auth/otp", headers={"Accept-Language": "uz;q=1"})

    assert response.status_code == 200
    assert '<html lang="ru">' in response.text
    assert "Получить код входа" in response.text
    assert '<label for="otp-phone">Номер телефона</label>' in response.text
    assert "Войти с паролем" in response.text


def test_get_otp_request_falls_back_to_uz_for_unknown_language(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)
    client.cookies.set(
        OTP_LOCALE_COOKIE_NAME,
        "fr",
        domain="testserver.local",
        path="/",
    )

    response = client.get("/auth/otp", headers={"Accept-Language": "fr;q=1"})

    assert response.status_code == 200
    assert '<html lang="uz">' in response.text
    assert "Kirish kodini olish" in response.text


def test_otp_request_template_has_no_inline_script_or_unsafe_rendering() -> None:
    template = OTP_REQUEST_TEMPLATE.read_text(encoding="utf-8")

    assert "<script" not in template
    assert "<style" not in template
    assert "style=" not in template
    assert "|safe" not in template
    assert "localStorage" not in template
    assert "sessionStorage" not in template
    for forbidden in (
        "challenge_id",
        "dispatch_id",
        "telegram_chat_id",
        "delivery_status",
        "active_shop",
        "require_shop_staff",
        "resolve_current_shop",
    ):
        assert forbidden not in template


def test_otp_mobile_styles_cover_320_to_430_and_touch_targets() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 320px)" in css
    assert ".otp-page" in css
    assert "min-height: 44px" in css
    assert "focus-visible" in css


def test_auth_otp_has_get_route_only_for_m7_53() -> None:
    methods_for_otp = set()
    for route in auth_router.routes:
        if isinstance(route, APIRoute) and route.path_format == "/auth/otp":
            methods_for_otp.update(route.methods or set())

    assert methods_for_otp == {"GET"}
