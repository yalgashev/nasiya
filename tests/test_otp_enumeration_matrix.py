import re
import time
from collections.abc import Generator
from datetime import UTC, datetime
from statistics import median

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.models import AuthRateLimit, User
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.contracts import OtpInternalOutcome
from app.otp.issuance import request_login_otp
from app.otp.models import OtpChallenge, OtpDispatch
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 30, 0, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-enumeration"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-enumeration-at-least-32"


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
    phone_attempts: int = 3,
    user_attempts: int = 3,
    ip_attempts: int = 20,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        anonymous_session_ttl_minutes=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=TEST_OTP_HMAC_KEY,
        otp_login_rate_limit_phone_attempts=phone_attempts,
        otp_login_rate_limit_user_attempts=user_attempts,
        otp_login_rate_limit_ip_attempts=ip_attempts,
    )


def make_client(
    engine: Engine,
    *,
    phone_attempts: int = 3,
    user_attempts: int = 3,
    ip_attempts: int = 20,
) -> tuple[TestClient, Settings]:
    settings = make_settings(
        engine,
        phone_attempts=phone_attempts,
        user_attempts=user_attempts,
        ip_attempts=ip_attempts,
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    return TestClient(application, client=("203.0.113.110", 50_000)), settings


def add_user(
    session: Session,
    phone: str,
    *,
    is_active: bool = True,
    linked: bool = True,
) -> User:
    user = User(phone=phone, is_active=is_active)
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=9_988_000_000
        + (session.scalar(select(func.count()).select_from(TelegramLink)) or 0)
        + 1
        if linked
        else None,
        linked_at=NOW,
        unlinked_at=None if linked else NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    return user


def get_request_csrf(client: TestClient) -> str:
    response = client.get("/auth/otp")
    assert response.status_code == 200
    return extract_hidden_csrf_token(response.text)


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def normalize_csrf(html: str) -> str:
    return re.sub(
        r'name="csrf_token"\s+value="[^"]+"',
        'name="csrf_token" value="<csrf>"',
        html,
    )


def post_request(
    client: TestClient,
    *,
    csrf_token: str,
    phone: str,
):
    return client.post(
        "/auth/otp/request",
        data={"csrf_token": csrf_token, "phone": phone},
        follow_redirects=False,
    )


def assert_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def response_shape(response, client: TestClient, settings: Settings) -> tuple[str, ...]:
    return (
        str(response.status_code),
        response.headers.get("location", ""),
        response.text,
        response.headers["cache-control"],
        response.headers["content-security-policy"],
        response.headers["x-frame-options"],
        response.headers["x-content-type-options"],
        response.headers["referrer-policy"],
        str("set-cookie" in response.headers),
        str(client.cookies.get(settings.session_cookie_name) is not None),
    )


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_http_request_enumeration_matrix_has_same_public_shape_and_verify_page(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_user(db_session, "+998900009901")
    add_user(db_session, "+998900009902", is_active=False)
    add_user(db_session, "+998900009903", linked=False)
    db_session.commit()
    send_calls = []

    async def fail_if_web_calls_telegram(*_args, **_kwargs):
        send_calls.append("called")
        raise AssertionError("web request must not call Telegram")

    monkeypatch.setattr(
        "app.otp.provider.TelegramOtpProvider.send_otp",
        fail_if_web_calls_telegram,
    )
    scenarios = [
        ("eligible", "+998900009901", {}),
        ("unknown", "+998900009999", {}),
        ("inactive", "+998900009902", {}),
        ("unlinked", "+998900009903", {}),
        ("malformed", "not-a-phone", {}),
        ("rate_limited", "+998900009904", {"phone_attempts": 1}),
    ]
    post_shapes = []
    verify_bodies = []
    for _label, phone, settings_kwargs in scenarios:
        client, settings = make_client(m2_test_database, **settings_kwargs)
        csrf_token = get_request_csrf(client)
        response = post_request(client, csrf_token=csrf_token, phone=phone)
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/otp/verify"
        assert_security_headers(response)
        assert phone not in response.headers["location"]
        post_shapes.append(response_shape(response, client, settings))

        verify_page = client.get(response.headers["location"])
        assert verify_page.status_code == 200
        assert_security_headers(verify_page)
        normalized_body = normalize_csrf(verify_page.text)
        assert "challenge" not in normalized_body.casefold()
        assert "dispatch" not in normalized_body.casefold()
        assert "OTP_" not in normalized_body
        verify_bodies.append(normalized_body)

    assert len(set(post_shapes)) == 1
    assert len(set(verify_bodies)) == 1
    assert send_calls == []
    assert count_table(db_session, OtpChallenge) == 1
    assert count_table(db_session, OtpDispatch) == 1


def test_unknown_service_path_invokes_dummy_work_and_records_phone_ip_limits(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    dummy_calls = []

    result = request_login_otp(
        db_session,
        settings,
        phone_input="+998900009998",
        browser_binding_digest="9" * 64,
        client_ip=ResolvedClientIp("203.0.113.111"),
        locale="uz-Latn",
        now=NOW,
        dummy_work=lambda _key: dummy_calls.append("dummy"),
    )

    assert result.outcome is OtpInternalOutcome.OTP_NOT_ELIGIBLE
    assert dummy_calls == ["dummy"]
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0
    rate_limit_rows = list(db_session.scalars(select(AuthRateLimit)).all())
    assert {row.scope for row in rate_limit_rows} == {
        "otp-login-issue:phone",
        "otp-login-issue:ip",
    }
    rendered_limits = " ".join(f"{row.scope}:{row.key_hash}" for row in rate_limit_rows)
    assert "+998900009998" not in rendered_limits
    assert "203.0.113.111" not in rendered_limits


def test_http_request_latency_is_bounded_without_network_sleep(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    add_user(db_session, "+998900009921")
    db_session.commit()
    durations = []
    for phone in (
        "+998900009921",
        "+998900009991",
        "+998900009992",
        "not-a-phone",
    ):
        client, _settings = make_client(m2_test_database)
        csrf_token = get_request_csrf(client)
        started = time.perf_counter()
        response = post_request(client, csrf_token=csrf_token, phone=phone)
        durations.append(time.perf_counter() - started)
        assert response.status_code == 303

    assert max(durations) < 2.0
    assert median(durations) < 1.0


def test_http_request_logs_do_not_include_target_result_or_phone(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    add_user(db_session, "+998900009931")
    db_session.commit()
    client, _settings = make_client(m2_test_database)
    csrf_token = get_request_csrf(client)

    with caplog.at_level("DEBUG"):
        response = post_request(
            client,
            csrf_token=csrf_token,
            phone="+998900009931",
        )

    assert response.status_code == 303
    log_text = caplog.text
    assert "+998900009931" not in log_text
    assert "OTP_PENDING" not in log_text
    assert "OTP_NOT_ELIGIBLE" not in log_text
    assert "telegram_chat" not in log_text
    assert "challenge" not in log_text.casefold()
    session_cookie = client.cookies.get(
        make_settings(m2_test_database).session_cookie_name
    )
    assert session_cookie is not None
    assert session_cookie not in log_text
