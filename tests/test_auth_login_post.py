import re
from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.login_rate_limit import LOGIN_IP_SCOPE
from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.auth.service import create_user
from app.auth.sessions import hash_session_token, resolve_by_raw_token
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-login-post"
LOGIN_FAILED_MESSAGE = "Telefon raqam yoki parol noto'g'ri."
RATE_LIMITED_MESSAGE = "Juda ko'p urinish. Keyinroq qayta urinib ko'ring."


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
    phone_attempts: int = 5,
    ip_attempts: int = 20,
    app_environment: str = "testing",
    session_cookie_secure: bool = False,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment=app_environment,
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=session_cookie_secure,
        login_rate_limit_phone_attempts=phone_attempts,
        login_rate_limit_ip_attempts=ip_attempts,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_client(
    engine: Engine,
    now: datetime,
    *,
    phone_attempts: int = 5,
    ip_attempts: int = 20,
    app_environment: str = "testing",
    session_cookie_secure: bool = False,
    base_url: str = "http://testserver",
) -> tuple[TestClient, Settings]:
    settings = make_settings(
        engine,
        phone_attempts=phone_attempts,
        ip_attempts=ip_attempts,
        app_environment=app_environment,
        session_cookie_secure=session_cookie_secure,
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application, base_url=base_url), settings


def commit_user(
    db_session: Session,
    phone: str,
    *,
    is_active: bool = True,
) -> User:
    result = create_user(db_session, phone, "Password123", is_active=is_active)
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_user_without_password_hash(db_session: Session, phone: str) -> User:
    user = commit_user(db_session, phone)
    user.password_hash = None
    db_session.commit()
    return user


def get_login_form(client: TestClient, settings: Settings) -> tuple[str, str]:
    response = client.get("/auth/login")
    assert response.status_code == 200
    csrf_token = extract_hidden_csrf_token(response.text)
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    return csrf_token, raw_cookie


def post_login(
    client: TestClient,
    *,
    csrf_token: str,
    phone: str = "901234567",
    password: str = "Password123",
    next_url: str | None = None,
):
    data = {
        "csrf_token": csrf_token,
        "phone": phone,
        "password": password,
    }
    if next_url is not None:
        data["next"] = next_url
    return client.post("/auth/login", data=data, follow_redirects=False)


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def fetch_session_by_cookie(db_session: Session, raw_cookie: str) -> AuthSession:
    session = db_session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(raw_cookie)
        )
    )
    assert session is not None
    return session


def get_rate_limit_records(db_session: Session) -> list[AuthRateLimit]:
    return list(db_session.scalars(select(AuthRateLimit).order_by(AuthRateLimit.scope)))


def count_authenticated_sessions(db_session: Session) -> int:
    return (
        db_session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id.is_not(None))
        )
        or 0
    )


def assert_login_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_session_cookie_flags(response, settings: Settings, *, secure: bool) -> None:
    set_cookie = response.headers["set-cookie"]

    assert set_cookie.startswith(f"{settings.session_cookie_name}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert ("Secure" in set_cookie) is secure


def assert_failure_response_is_safe(response, *secrets: str) -> None:
    assert "Traceback" not in response.text
    assert "password_hash" not in response.text
    assert "argon2" not in response.text.casefold()
    for secret in secrets:
        assert secret not in response.text


def assert_generic_login_failure(
    response,
    secret: str | None = None,
    *,
    error_code: ErrorCode = ErrorCode.UNAUTHORIZED,
) -> None:
    assert response.status_code == 200
    assert response.headers["x-error-code"] == error_code.value
    assert LOGIN_FAILED_MESSAGE in unescape(response.text)
    assert_failure_response_is_safe(response, *(secret,) if secret else ())
    assert_login_security_headers(response)


def normalized_failure_body(response) -> str:
    visible_body = unescape(response.text)
    return re.sub(
        r'name="csrf_token"\s+value="[^"]+"',
        'name="csrf_token" value="<csrf>"',
        visible_body,
    )


def test_post_login_success_rotates_session_sets_cookie_and_redirects_account(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234567")
    csrf_token, old_cookie = get_login_form(client, settings)
    old_session = fetch_session_by_cookie(db_session, old_cookie)
    old_csrf_secret = old_session.csrf_secret

    response = post_login(client, csrf_token=csrf_token)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/account"
    assert_login_security_headers(response)
    new_cookie = client.cookies.get(settings.session_cookie_name)
    assert new_cookie is not None
    assert new_cookie != old_cookie
    assert new_cookie in response.headers["set-cookie"]
    assert_session_cookie_flags(response, settings, secure=False)
    db_session.expire_all()
    old_session = db_session.get(AuthSession, old_session.id)
    assert old_session is not None
    assert old_session.revoked_at == now
    assert resolve_by_raw_token(db_session, old_cookie, now) is None
    new_session = fetch_session_by_cookie(db_session, new_cookie)
    assert new_session.user_id == user.id
    assert new_session.revoked_at is None
    resolved_new_session = resolve_by_raw_token(db_session, new_cookie, now)
    assert resolved_new_session is not None
    assert resolved_new_session.authenticated_user is not None
    assert resolved_new_session.authenticated_user.id == user.id
    assert new_session.csrf_secret != old_csrf_secret
    assert response.text == ""
    assert "Password123" not in response.text
    assert "password_hash" not in response.text
    assert new_cookie not in response.text
    assert old_cookie not in response.text
    assert new_session.token_hash not in response.text
    assert old_cookie not in response.headers["set-cookie"]


def test_post_login_success_sets_secure_cookie_for_production_environment(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(
        m2_test_database,
        now,
        app_environment="production",
        session_cookie_secure=True,
        base_url="https://testserver",
    )
    commit_user(db_session, "+998901234567")
    csrf_token, _old_cookie = get_login_form(client, settings)

    response = post_login(client, csrf_token=csrf_token)

    assert response.status_code == 303
    assert_session_cookie_flags(response, settings, secure=True)


def test_post_login_success_clears_phone_failure_bucket(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)
    commit_user(db_session, "+998901234567")
    csrf_token, _old_cookie = get_login_form(client, _settings)

    first_failure = post_login(
        client,
        csrf_token=csrf_token,
        password="wrong-password",
    )
    second_csrf = extract_hidden_csrf_token(first_failure.text)
    success = post_login(client, csrf_token=second_csrf)

    assert success.status_code == 303
    db_session.expire_all()
    records = get_rate_limit_records(db_session)
    assert [record.scope for record in records] == [LOGIN_IP_SCOPE]


@pytest.mark.parametrize(
    ("next_url", "expected_location"),
    [
        ("/health", "/health"),
        ("https://evil.example/account", "/auth/account"),
        ("//evil.example/account", "/auth/account"),
        ("account", "/auth/account"),
    ],
)
def test_post_login_redirects_only_to_safe_local_next(
    m2_test_database: Engine,
    db_session: Session,
    next_url: str,
    expected_location: str,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    commit_user(db_session, "+998901234567")
    csrf_token, _old_cookie = get_login_form(client, settings)

    response = post_login(
        client,
        csrf_token=csrf_token,
        next_url=next_url,
    )

    assert response.status_code == 303
    assert response.headers["location"] == expected_location


def test_post_login_unknown_wrong_null_hash_and_inactive_share_generic_message(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    active_user = commit_user(db_session, "+998901234567")
    null_hash_user = commit_user_without_password_hash(db_session, "+998901234568")
    inactive_user = commit_user(db_session, "+998901234570", is_active=False)
    cases = [
        (active_user.phone, "wrong-password"),
        ("+998901234569", "Password123"),
        (null_hash_user.phone, "Password123"),
        (inactive_user.phone, "Password123"),
    ]
    normalized_bodies = []

    for phone, password in cases:
        client, settings = make_client(m2_test_database, now)
        csrf_token, _old_cookie = get_login_form(client, settings)
        response = post_login(
            client,
            csrf_token=csrf_token,
            phone=phone,
            password=password,
        )
        assert_generic_login_failure(response, password)
        assert count_authenticated_sessions(db_session) == 0
        normalized_bodies.append(normalized_failure_body(response))

    assert len(set(normalized_bodies)) == 1
    assert len({len(body) for body in normalized_bodies}) == 1


def test_post_login_invalid_phone_records_ip_only_and_does_not_store_raw_values(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    csrf_token, _old_cookie = get_login_form(client, settings)
    raw_phone = "+99890abc4567"
    raw_password = "Password123"

    response = post_login(
        client,
        csrf_token=csrf_token,
        phone=raw_phone,
        password=raw_password,
    )

    assert_generic_login_failure(
        response,
        raw_password,
        error_code=ErrorCode.VALIDATION_ERROR,
    )
    assert count_authenticated_sessions(db_session) == 0
    db_session.expire_all()
    records = get_rate_limit_records(db_session)
    assert [record.scope for record in records] == [LOGIN_IP_SCOPE]
    stored_values = db_session.execute(
        text(
            "SELECT scope, key_hash, window_started_at::text, "
            "attempt_count::text, updated_at::text "
            "FROM auth_rate_limits"
        )
    ).all()
    stored_text = "|".join(str(value) for row in stored_values for value in row)
    assert raw_phone not in stored_text
    assert raw_password not in stored_text
    assert "testclient" not in stored_text


def test_post_login_missing_fields_are_safe_validation_failure(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    csrf_token, _old_cookie = get_login_form(client, settings)

    response = client.post(
        "/auth/login",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert_generic_login_failure(response, error_code=ErrorCode.VALIDATION_ERROR)
    assert "field required" not in response.text.casefold()
    assert count_authenticated_sessions(db_session) == 0


def test_post_login_phone_rate_limited_response_is_safe_and_does_not_rotate_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now, phone_attempts=1)
    commit_user(db_session, "+998901234567")
    csrf_token, old_cookie = get_login_form(client, settings)
    first_response = post_login(
        client,
        csrf_token=csrf_token,
        password="wrong-password",
    )
    csrf_token = extract_hidden_csrf_token(first_response.text)

    response = post_login(client, csrf_token=csrf_token)

    assert response.status_code == 429
    assert response.headers["x-error-code"] == ErrorCode.RATE_LIMITED.value
    assert RATE_LIMITED_MESSAGE in unescape(response.text)
    assert ErrorCode.RATE_LIMITED.value not in response.text
    assert client.cookies.get(settings.session_cookie_name) == old_cookie
    assert count_authenticated_sessions(db_session) == 0
    assert_login_security_headers(response)


def test_post_login_ip_rate_limited_response_is_safe_and_stores_no_raw_ip(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now, ip_attempts=1)
    csrf_token, old_cookie = get_login_form(client, settings)

    response = post_login(
        client,
        csrf_token=csrf_token,
        phone="+99890abc4567",
        password="Password123",
    )

    assert response.status_code == 429
    assert response.headers["x-error-code"] == ErrorCode.RATE_LIMITED.value
    assert RATE_LIMITED_MESSAGE in unescape(response.text)
    assert "Traceback" not in response.text
    assert "password_hash" not in response.text
    assert client.cookies.get(settings.session_cookie_name) == old_cookie
    assert count_authenticated_sessions(db_session) == 0
    stored_values = db_session.execute(
        text(
            "SELECT scope, key_hash, window_started_at::text, "
            "attempt_count::text, updated_at::text "
            "FROM auth_rate_limits"
        )
    ).all()
    stored_text = "|".join(str(value) for row in stored_values for value in row)
    assert "testclient" not in stored_text
    assert "+99890abc4567" not in stored_text


def test_post_login_missing_csrf_fails_before_credentials_are_processed(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    commit_user(db_session, "+998901234567")
    _csrf_token, old_cookie = get_login_form(client, settings)

    response = client.post(
        "/auth/login",
        data={"phone": "901234567", "password": "Password123"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert client.cookies.get(settings.session_cookie_name) == old_cookie
    assert count_authenticated_sessions(db_session) == 0
    assert len(get_rate_limit_records(db_session)) == 0


def test_post_login_wrong_csrf_fails_before_credentials_are_processed(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    commit_user(db_session, "+998901234567")
    _csrf_token, old_cookie = get_login_form(client, settings)

    response = client.post(
        "/auth/login",
        data={
            "csrf_token": "wrong-csrf-token",
            "phone": "901234567",
            "password": "Password123",
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert client.cookies.get(settings.session_cookie_name) == old_cookie
    assert count_authenticated_sessions(db_session) == 0
    assert len(get_rate_limit_records(db_session)) == 0
    assert "wrong-csrf-token" not in response.text
    assert "Traceback" not in response.text


def test_login_route_does_not_query_or_hash_credentials_directly() -> None:
    router_source = Path("app/auth/router.py").read_text(encoding="utf-8")

    assert "select(" not in router_source
    assert "password_hash" not in router_source
    assert "hash_password" not in router_source
    assert "verify_password" not in router_source
    assert "password_service" not in router_source
