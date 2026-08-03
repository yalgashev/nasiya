import inspect
import re
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.auth.router as auth_router_module
from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import hash_session_token, resolve_by_raw_token
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.code import OtpCode
from app.otp.contracts import OtpChallengeEventAction, OtpChallengeStatus, OtpPurpose
from app.otp.crypto import compute_otp_code_mac, derive_browser_binding_digest
from app.otp.models import OtpChallenge, OtpChallengeEvent
from app.otp.repository import activate_challenge, create_pending_challenge
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 29, 20, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-verify-post"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-verify-post-at-least-32"
VALID_CODE = OtpCode("123456")


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine, *, with_otp_key: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        anonymous_session_ttl_minutes=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=TEST_OTP_HMAC_KEY if with_otp_key else None,
    )


def make_client(
    engine: Engine,
    now: datetime = NOW,
    *,
    with_otp_key: bool = True,
) -> tuple[TestClient, Settings]:
    settings = make_settings(engine, with_otp_key=with_otp_key)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application, client=("203.0.113.70", 50_000)), settings


def get_verify_form(client: TestClient, settings: Settings) -> tuple[str, str]:
    response = client.get("/auth/otp/verify")
    assert response.status_code == 200
    csrf_token = extract_hidden_csrf_token(response.text)
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    return csrf_token, raw_cookie


def post_otp_verify(
    client: TestClient,
    *,
    csrf_token: str,
    code: str,
    next_url: str | None = None,
):
    data = {"csrf_token": csrf_token, "code": code}
    if next_url is not None:
        data["next"] = next_url
    return client.post(
        "/auth/otp/verify",
        data=data,
        follow_redirects=False,
    )


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


def add_user_and_link(
    session: Session,
    *,
    phone: str = "+998900009501",
) -> tuple[User, TelegramLink]:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=9_985_000_501,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    return user, link


def seed_active_challenge(
    session: Session,
    settings: Settings,
    auth_session: AuthSession,
    *,
    phone: str = "+998900009501",
    code: OtpCode = VALID_CODE,
    expires_at: datetime | None = None,
    failed_attempts: int = 0,
    link_changed: bool = False,
) -> tuple[User, OtpChallenge]:
    user, link = add_user_and_link(session, phone=phone)
    digest = derive_browser_binding_digest(
        otp_hmac_key=settings.require_otp_hmac_key(),
        session_id=auth_session.id,
        csrf_secret=auth_session.csrf_secret,
    )
    challenge = create_pending_challenge(
        session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=digest,
        now=NOW - timedelta(seconds=10),
    )
    code_mac = compute_otp_code_mac(
        otp_hmac_key=SecretStr(TEST_OTP_HMAC_KEY),
        challenge_id=challenge.id,
        user_id=user.id,
        purpose=OtpPurpose.LOGIN,
        code=code,
    )
    activate_challenge(
        session,
        challenge=challenge,
        code_mac=code_mac,
        activated_at=NOW - timedelta(seconds=9),
        expires_at=expires_at or NOW + timedelta(minutes=3),
    )
    challenge.failed_attempts = failed_attempts
    if link_changed:
        link.linked_at = NOW + timedelta(seconds=1)
        link.phone_verified_at = link.linked_at
        link.updated_at = link.linked_at
    session.flush()
    return user, challenge


def count_authenticated_sessions(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id.is_not(None))
        )
        or 0
    )


def assert_otp_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_invalid_redirect(
    response, *, location: str = "/auth/otp/verify?error=invalid"
):
    assert response.status_code == 303
    assert response.headers["location"] == location
    assert response.text == ""
    assert "set-cookie" not in response.headers
    assert_otp_security_headers(response)


def test_post_otp_verify_success_consumes_and_rotates_session_cookie(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    csrf_token, old_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, old_cookie)
    old_csrf_secret = anonymous_session.csrf_secret
    user, challenge = seed_active_challenge(
        db_session,
        settings,
        anonymous_session,
    )
    db_session.commit()

    response = post_otp_verify(
        client,
        csrf_token=csrf_token,
        code="123456",
        next_url="/customer/profile",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customer/profile"
    assert settings.session_cookie_name in response.headers["set-cookie"]
    assert_otp_security_headers(response)
    assert response.text == ""
    assert "123456" not in response.text
    assert "123456" not in response.headers["set-cookie"]
    new_cookie = client.cookies.get(settings.session_cookie_name)
    assert new_cookie is not None
    assert new_cookie != old_cookie

    db_session.expire_all()
    persisted_challenge = db_session.get(OtpChallenge, challenge.id)
    assert persisted_challenge is not None
    assert persisted_challenge.status == OtpChallengeStatus.CONSUMED.value
    assert persisted_challenge.consumed_at == NOW
    assert persisted_challenge.terminal_at == NOW
    old_session = db_session.get(AuthSession, anonymous_session.id)
    assert old_session is not None
    assert old_session.revoked_at == NOW
    assert resolve_by_raw_token(db_session, old_cookie, NOW) is None
    new_session = fetch_session_by_cookie(db_session, new_cookie)
    assert new_session.user_id == user.id
    assert new_session.csrf_secret != old_csrf_secret
    resolved_new = resolve_by_raw_token(db_session, new_cookie, NOW)
    assert resolved_new is not None
    assert resolved_new.authenticated_user is not None
    assert resolved_new.authenticated_user.id == user.id
    events = list(db_session.scalars(select(OtpChallengeEvent)).all())
    assert [(event.action, event.safe_code) for event in events] == [
        (OtpChallengeEventAction.CONSUMED.value, None)
    ]


def test_post_otp_verify_unsafe_next_falls_back_to_account(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    csrf_token, old_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, old_cookie)
    seed_active_challenge(db_session, settings, anonymous_session)
    db_session.commit()

    response = post_otp_verify(
        client,
        csrf_token=csrf_token,
        code="123456",
        next_url="//example.test/customer",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/account"
    assert "example.test" not in response.headers["location"]
    assert settings.session_cookie_name in response.headers["set-cookie"]
    assert_otp_security_headers(response)


@pytest.mark.parametrize(
    ("scenario", "submitted_code"),
    [
        ("missing", "123456"),
        ("malformed", "123-456"),
        ("wrong", "000000"),
        ("expired", "123456"),
        ("burned", "123456"),
        ("link_changed", "123456"),
    ],
)
def test_post_otp_verify_invalid_matrix_uses_same_prg_without_session_rotation(
    m2_test_database: Engine,
    db_session: Session,
    scenario: str,
    submitted_code: str,
) -> None:
    client, settings = make_client(m2_test_database)
    csrf_token, old_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, old_cookie)
    if scenario == "wrong":
        seed_active_challenge(db_session, settings, anonymous_session)
    elif scenario == "expired":
        seed_active_challenge(
            db_session,
            settings,
            anonymous_session,
            expires_at=NOW - timedelta(seconds=1),
        )
    elif scenario == "burned":
        seed_active_challenge(
            db_session,
            settings,
            anonymous_session,
            failed_attempts=5,
        )
    elif scenario == "link_changed":
        seed_active_challenge(
            db_session,
            settings,
            anonymous_session,
            link_changed=True,
        )
    db_session.commit()

    response = post_otp_verify(
        client,
        csrf_token=csrf_token,
        code=submitted_code,
    )

    assert_invalid_redirect(response)
    assert "123456" not in response.headers["location"]
    assert "000000" not in response.headers["location"]
    db_session.expire_all()
    persisted_anonymous = db_session.get(AuthSession, anonymous_session.id)
    assert persisted_anonymous is not None
    assert persisted_anonymous.revoked_at is None
    assert count_authenticated_sessions(db_session) == 0


def test_post_otp_verify_invalid_redirect_preserves_only_safe_next(
    m2_test_database: Engine,
) -> None:
    client, settings = make_client(m2_test_database)
    csrf_token, _old_cookie = get_verify_form(client, settings)

    safe_response = post_otp_verify(
        client,
        csrf_token=csrf_token,
        code="000000",
        next_url="/customer/profile",
    )
    unsafe_response = post_otp_verify(
        client,
        csrf_token=csrf_token,
        code="000000",
        next_url="https://example.test/customer",
    )

    assert_invalid_redirect(
        safe_response,
        location="/auth/otp/verify?next=%2Fcustomer%2Fprofile&error=invalid",
    )
    assert_invalid_redirect(unsafe_response)
    assert "example.test" not in unsafe_response.headers["location"]


def test_post_otp_verify_missing_otp_key_degrades_generic_without_rotation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database, with_otp_key=False)
    csrf_token, old_cookie = get_verify_form(client, settings)

    response = post_otp_verify(
        client,
        csrf_token=csrf_token,
        code="123456",
    )

    assert_invalid_redirect(response)
    assert client.cookies.get(settings.session_cookie_name) == old_cookie
    assert count_authenticated_sessions(db_session) == 0


def test_post_otp_verify_replay_with_revoked_anonymous_session_creates_no_second_login(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    csrf_token, old_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, old_cookie)
    seed_active_challenge(db_session, settings, anonymous_session)
    db_session.commit()
    first = post_otp_verify(client, csrf_token=csrf_token, code="123456")
    assert first.status_code == 303
    assert count_authenticated_sessions(db_session) == 1

    replay_client, _settings = make_client(m2_test_database)
    replay_client.cookies.set(
        settings.session_cookie_name,
        old_cookie,
        domain="testserver.local",
        path="/",
    )
    replay = post_otp_verify(
        replay_client,
        csrf_token=csrf_token,
        code="123456",
    )

    assert replay.status_code == 403
    assert replay.headers["x-error-code"] == "CSRF_FAILED"
    assert count_authenticated_sessions(db_session) == 1


def test_post_otp_verify_requires_csrf_before_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, _settings = make_client(m2_test_database)

    response = client.post(
        "/auth/otp/verify",
        data={"code": "123456"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert_otp_security_headers(response)
    assert count_authenticated_sessions(db_session) == 0


def test_post_otp_verify_route_has_csrf_dependency() -> None:
    route = next(
        route
        for route in auth_router_module.router.routes
        if getattr(route, "path_format", None) == "/auth/otp/verify"
        and "POST" in getattr(route, "methods", set())
    )

    dependency_calls = [dependency.call for dependency in route.dependant.dependencies]
    assert auth_router_module.get_detached_otp_mutation_context in dependency_calls
    dependency_source = inspect.getsource(
        auth_router_module.get_detached_otp_mutation_context
    )
    assert "await validate_csrf(request, context, now)" in dependency_source


def test_post_otp_verify_route_has_no_delivery_or_shop_scope() -> None:
    source = inspect.getsource(auth_router_module.verify_login_otp_route).casefold()

    for forbidden in (
        "send_message",
        "telegram_bot_token",
        "request_login_otp",
        "request_new_login_code",
        "telegram_chat_id",
        "challenge_id",
        "dispatch_id",
        "active_shop",
        "require_shop_staff",
        "resolve_current_shop",
        "print(",
        "logger",
        ".commit(",
        ".rollback(",
        ".close(",
    ):
        assert forbidden not in source
