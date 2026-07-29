import inspect
import re
from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.auth.router as auth_router_module
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import (
    CreatedSession,
    create_authenticated_session,
    hash_session_token,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.web_presentation import OTP_LOCALE_COOKIE_NAME
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OTP_VERIFY_TEMPLATE = PROJECT_ROOT / "app/templates/auth/otp_verify.html"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-verify-get"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-verify-get-at-least-32"
GENERIC_NOTICE = (
    "Agar kiritilgan telefon mos hisobga tegishli bo'lsa, kod Telegramga "
    "yuboriladi. Kod kelmasa, 60 soniyadan keyin yangi kod so'rang yoki parol "
    "bilan kiring."
)


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
        otp_hmac_key=TEST_OTP_HMAC_KEY,
    )


def make_client(engine: Engine, now: datetime) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application, client=("203.0.113.60", 50_000)), settings


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


def extract_hidden_csrf_tokens(html: str) -> list[str]:
    return re.findall(r'name="csrf_token"\s+value="(?P<token>[^"]+)"', html)


def extract_hidden_csrf_token(html: str) -> str:
    tokens = extract_hidden_csrf_tokens(html)
    assert tokens
    return tokens[0]


def assert_otp_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_get_otp_verify_creates_anonymous_session_and_renders_uniform_page(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)

    response = client.get("/auth/otp/verify")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_otp_security_headers(response)
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    stored_session = fetch_session_by_cookie(db_session, raw_cookie)
    assert stored_session.user_id is None
    csrf_tokens = extract_hidden_csrf_tokens(response.text)
    assert csrf_tokens == [get_csrf_token(stored_session).as_form_value()] * 2
    assert count_otp_rows(db_session) == (0, 0)
    assert GENERIC_NOTICE in unescape(response.text)
    assert '<form method="post" action="/auth/otp/verify" novalidate>' in response.text
    assert '<form method="post" action="/auth/otp/new-code" novalidate>' in (
        response.text
    )
    assert '<label for="otp-code">Olti xonali kod</label>' in response.text
    assert 'name="code"' in response.text
    assert 'type="text"' in response.text
    assert 'inputmode="numeric"' in response.text
    assert 'autocomplete="one-time-code"' in response.text
    assert 'maxlength="6"' in response.text
    assert 'pattern="[0-9]{6}"' in response.text
    assert ">Yangi kod so'rash</button>" in unescape(response.text)
    assert '<a href="/auth/login">Parol bilan kirish</a>' in response.text
    for forbidden in (
        "phone=",
        "challenge_id",
        "dispatch_id",
        "telegram_chat",
        "chat_id",
        "delivery status",
        "attempt count",
        "expires_at",
        "active_shop",
    ):
        assert forbidden not in response.text.casefold()


def test_get_otp_verify_shows_same_generic_page_after_request_prg(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    request_page = client.get("/auth/otp")
    csrf_token = extract_hidden_csrf_token(request_page.text)

    response = client.post(
        "/auth/otp/request",
        data={"csrf_token": csrf_token, "phone": "+998900009799"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert str(response.url).endswith("/auth/otp/verify")
    assert GENERIC_NOTICE in unescape(response.text)
    assert count_otp_rows(db_session) == (0, 0)
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    stored_session = fetch_session_by_cookie(db_session, raw_cookie)
    assert (
        extract_hidden_csrf_tokens(response.text)
        == [get_csrf_token(stored_session).as_form_value()] * 2
    )


def test_get_otp_verify_keeps_safe_next_in_both_forms(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    safe_response = client.get(
        "/auth/otp/verify",
        params={"next": "/customer/profile"},
    )
    unsafe_response = client.get(
        "/auth/otp/verify",
        params={"next": "//example.test/customer"},
    )

    assert safe_response.status_code == 200
    assert (
        safe_response.text.count(
            '<input type="hidden" name="next" value="/customer/profile">'
        )
        == 2
    )
    assert unsafe_response.status_code == 200
    assert 'name="next"' not in unsafe_response.text
    assert "example.test" not in unsafe_response.text


def test_get_otp_verify_uses_bounded_ru_locale_cookie(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)
    client.cookies.set(
        OTP_LOCALE_COOKIE_NAME,
        "ru",
        domain="testserver.local",
        path="/",
    )

    response = client.get("/auth/otp/verify")

    assert response.status_code == 200
    assert '<html lang="ru">' in response.text
    assert "Введите код из Telegram" in response.text
    assert '<label for="otp-code">Шестизначный код</label>' in response.text
    assert "Запросить новый код" in response.text


def test_get_otp_verify_invalid_query_uses_single_generic_error_message(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/auth/otp/verify", params={"error": "invalid"})

    assert response.status_code == 200
    assert "Kod noto'g'ri yoki muddati tugagan." in unescape(response.text)
    assert "OTP_INVALID" not in response.text
    assert "OTP_EXPIRED" not in response.text
    assert "OTP_BURNED" not in response.text


def test_get_otp_verify_redirects_authenticated_session_to_account(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = User(phone="+998900009701")
    db_session.add(user)
    db_session.flush()
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-otp-verify-get",
        now,
        settings=settings,
    )
    db_session.commit()
    set_client_session_cookie(client, settings, created)

    response = client.get("/auth/otp/verify", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/account"
    assert_otp_security_headers(response)
    assert count_otp_rows(db_session) == (0, 0)


def test_otp_verify_template_has_no_inline_script_or_unsafe_rendering() -> None:
    template = OTP_VERIFY_TEMPLATE.read_text(encoding="utf-8")

    assert "<script" not in template
    assert "<style" not in template
    assert "style=" not in template
    assert "|safe" not in template
    assert "localStorage" not in template
    assert "sessionStorage" not in template
    for forbidden in (
        "phone",
        "challenge_id",
        "dispatch_id",
        "telegram_chat_id",
        "delivery_status",
        "active_shop",
        "require_shop_staff",
        "resolve_current_shop",
    ):
        assert forbidden not in template


def test_otp_verify_route_has_no_challenge_lookup_or_delivery_scope() -> None:
    source = inspect.getsource(auth_router_module.otp_verify_page).casefold()

    for forbidden in (
        "request_login_otp",
        "request_new_login_code",
        "verify_login_otp",
        "otpchallenge",
        "otpdispatch",
        "load_",
        "send_message",
        "telegram_bot_token",
        "active_shop",
        "require_shop_staff",
        "resolve_current_shop",
        ".commit(",
        ".rollback(",
        ".close(",
    ):
        assert forbidden not in source
