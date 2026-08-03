import inspect
import re
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.auth.router as auth_router_module
import app.otp.issuance as issuance_module
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time, validate_csrf
from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.auth.rate_limit import hash_rate_limit_key
from app.auth.sessions import (
    CreatedSession,
    create_anonymous_session,
    create_authenticated_session,
    hash_session_token,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.contracts import OtpChallengeEventAction, OtpChallengeStatus
from app.otp.crypto import derive_browser_binding_digest
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.web_presentation import OTP_LOCALE_COOKIE_NAME
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-request-post"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-request-post-at-least-32"
CLIENT_IP = ResolvedClientIp("203.0.113.50")


class _TrackingSession(Session):
    def __init__(
        self,
        *args: Any,
        lifecycle: "_SessionLifecycle",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lifecycle = lifecycle
        self.tracking_closed = False
        lifecycle.sessions.append(self)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self.tracking_closed = True


class _SessionLifecycle:
    def __init__(self) -> None:
        self.sessions: list[_TrackingSession] = []
        self.domain_entries = 0

    def enter_domain(
        self,
        session: Session,
        *,
        call_start: int,
        predecessor_count: int,
    ) -> None:
        assert isinstance(session, _TrackingSession)
        current_call = self.sessions[call_start:]
        assert current_call[-1] is session
        assert len(current_call) == predecessor_count + 1
        assert all(candidate.tracking_closed for candidate in current_call[:-1])
        assert all(not candidate.in_transaction() for candidate in current_call[:-1])
        assert not session.tracking_closed
        assert session.in_transaction()
        assert len({id(candidate) for candidate in current_call}) == len(current_call)
        self.domain_entries += 1

    def assert_all_closed(self, *, call_start: int = 0) -> None:
        selected = self.sessions[call_start:]
        assert selected
        assert all(candidate.tracking_closed for candidate in selected)
        assert all(not candidate.in_transaction() for candidate in selected)
        assert len({id(candidate) for candidate in selected}) == len(selected)


def tracking_session_factory(
    engine: Engine,
    lifecycle: _SessionLifecycle,
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        class_=_TrackingSession,
        lifecycle=lifecycle,
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


def make_settings(
    engine: Engine,
    *,
    with_otp_key: bool = True,
    client_ip_mode: str = "direct",
) -> Settings:
    values: dict[str, object] = {
        "app_environment": "testing",
        "debug": False,
        "database_url": engine.url.render_as_string(hide_password=False),
        "session_cookie_secure": False,
        "anonymous_session_ttl_minutes": 30,
        "rate_limit_hmac_key": TEST_RATE_LIMIT_HMAC_KEY,
        "otp_hmac_key": TEST_OTP_HMAC_KEY if with_otp_key else None,
        "client_ip_mode": client_ip_mode,
    }
    if client_ip_mode == "trusted_proxy":
        values["trusted_proxy_cidrs"] = ["198.51.100.0/24"]
    return Settings(_env_file=None, **values)


def make_client(
    engine: Engine,
    *,
    with_otp_key: bool = True,
    client_ip_mode: str = "direct",
) -> tuple[TestClient, Settings]:
    settings = make_settings(
        engine,
        with_otp_key=with_otp_key,
        client_ip_mode=client_ip_mode,
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    return TestClient(application, client=("203.0.113.50", 50_000)), settings


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


def add_user(
    session: Session,
    phone: str,
    *,
    is_active: bool = True,
) -> User:
    user = User(phone=phone, is_active=is_active)
    session.add(user)
    session.flush()
    return user


def add_link(
    session: Session,
    user: User,
    *,
    chat_id: int | None = 9_984_000_001,
    unlinked: bool = False,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=None if unlinked else chat_id,
        linked_at=NOW,
        unlinked_at=NOW if unlinked else None,
        phone_verified_at=None if unlinked else NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    return link


def commit_anonymous_session(
    db_session: Session,
    settings: Settings,
) -> CreatedSession:
    created = create_anonymous_session(
        db_session,
        "pytest-otp-request-post",
        NOW,
        settings=settings,
    )
    db_session.commit()
    return created


def commit_authenticated_session(
    db_session: Session,
    user: User,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-otp-request-post",
        NOW,
        settings=settings,
    )
    db_session.commit()
    return created


def get_otp_form(client: TestClient, settings: Settings) -> tuple[str, str]:
    response = client.get("/auth/otp")
    assert response.status_code == 200
    csrf_token = extract_hidden_csrf_token(response.text)
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    return csrf_token, raw_cookie


def post_otp_request(
    client: TestClient,
    *,
    csrf_token: str,
    phone: str,
    next_url: str | None = None,
):
    data = {"csrf_token": csrf_token, "phone": phone}
    if next_url is not None:
        data["next"] = next_url
    return client.post(
        "/auth/otp/request",
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


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def seed_rate_limit(
    session: Session,
    settings: Settings,
    *,
    scope: str,
    raw_key: str,
    attempt_count: int,
) -> None:
    session.add(
        AuthRateLimit(
            scope=scope,
            key_hash=hash_rate_limit_key(settings, raw_key),
            window_started_at=NOW,
            attempt_count=attempt_count,
            updated_at=NOW,
        )
    )


def rate_limit_attempt_count(
    session: Session,
    settings: Settings,
    *,
    scope: str,
    raw_key: str,
) -> int:
    record = session.get(
        AuthRateLimit,
        {
            "scope": scope,
            "key_hash": hash_rate_limit_key(settings, raw_key),
        },
    )
    assert record is not None
    return record.attempt_count


def assert_otp_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_generic_request_redirect(response, *, location: str = "/auth/otp/verify"):
    assert response.status_code == 303
    assert response.headers["location"] == location
    assert response.text == ""
    assert "set-cookie" not in response.headers
    assert_otp_security_headers(response)


def test_post_otp_request_uniform_redirect_for_target_states(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    eligible = add_user(db_session, "+998900009401")
    add_link(db_session, eligible)
    inactive = add_user(db_session, "+998900009402", is_active=False)
    add_link(db_session, inactive, chat_id=9_984_000_002)
    unlinked = add_user(db_session, "+998900009403")
    add_link(db_session, unlinked, chat_id=9_984_000_003, unlinked=True)
    db_session.commit()

    scenarios = [
        "+998900009401",
        "+998900009499",
        "+998900009402",
        "+998900009403",
        "not-a-phone",
    ]
    response_shapes = []
    for phone in scenarios:
        client, settings = make_client(m2_test_database)
        csrf_token, _raw_cookie = get_otp_form(client, settings)

        response = post_otp_request(client, csrf_token=csrf_token, phone=phone)

        assert_generic_request_redirect(response)
        assert phone not in response.headers["location"]
        response_shapes.append(
            (
                response.status_code,
                response.headers["location"],
                response.text,
                response.headers["cache-control"],
            )
        )

    assert len(set(response_shapes)) == 1
    assert count_table(db_session, OtpChallenge) == 1
    assert count_table(db_session, OtpDispatch) == 1
    challenge = db_session.scalar(select(OtpChallenge))
    assert challenge is not None
    assert challenge.user_id == eligible.id
    assert challenge.status == OtpChallengeStatus.PENDING_DISPATCH.value
    dispatch = db_session.scalar(select(OtpDispatch))
    assert dispatch is not None
    assert dispatch.challenge_id == challenge.id
    event = db_session.scalar(select(OtpChallengeEvent))
    assert event is not None
    assert event.action == OtpChallengeEventAction.ISSUED.value


def test_post_otp_request_derives_browser_binding_and_dispatch_locale(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900009411")
    add_link(db_session, user)
    db_session.commit()
    client, settings = make_client(m2_test_database)
    client.cookies.set(
        OTP_LOCALE_COOKIE_NAME,
        "ru",
        domain="testserver.local",
        path="/",
    )
    csrf_token, raw_cookie = get_otp_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, raw_cookie)
    expected_digest = derive_browser_binding_digest(
        otp_hmac_key=settings.require_otp_hmac_key(),
        session_id=anonymous_session.id,
        csrf_secret=anonymous_session.csrf_secret,
    ).as_stored_value()

    response = post_otp_request(
        client,
        csrf_token=csrf_token,
        phone="+998900009411",
    )

    assert_generic_request_redirect(response)
    challenge = db_session.scalar(select(OtpChallenge))
    assert challenge is not None
    assert challenge.browser_binding_digest == expected_digest
    dispatch = db_session.scalar(select(OtpDispatch))
    assert dispatch is not None
    assert dispatch.locale == "ru"


def test_post_otp_request_safe_next_policy_does_not_leak_phone(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900009421")
    add_link(db_session, user)
    db_session.commit()
    client, settings = make_client(m2_test_database)
    csrf_token, _raw_cookie = get_otp_form(client, settings)

    safe_response = post_otp_request(
        client,
        csrf_token=csrf_token,
        phone="+998900009421",
        next_url="/customer/profile",
    )
    unsafe_response = post_otp_request(
        client,
        csrf_token=csrf_token,
        phone="+998900009421",
        next_url="https://example.test/customer",
    )

    assert_generic_request_redirect(
        safe_response,
        location="/auth/otp/verify?next=%2Fcustomer%2Fprofile",
    )
    assert_generic_request_redirect(unsafe_response)
    for response in (safe_response, unsafe_response):
        assert "+998900009421" not in response.headers["location"]
        assert "example.test" not in response.headers["location"]


def test_post_otp_request_authenticated_user_redirects_without_otp_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900009431")
    db_session.commit()
    client, settings = make_client(m2_test_database)
    created = commit_authenticated_session(db_session, user, settings)
    csrf_token = get_csrf_token(created.session).as_form_value()
    set_client_session_cookie(client, settings, created)

    response = post_otp_request(
        client,
        csrf_token=csrf_token,
        phone="+998900009431",
    )

    assert_generic_request_redirect(response, location="/auth/account")
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0


def test_post_otp_request_missing_otp_key_or_client_ip_failure_degrades_generic(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    for kwargs in (
        {"with_otp_key": False},
        {"client_ip_mode": "trusted_proxy"},
    ):
        client, settings = make_client(m2_test_database, **kwargs)
        csrf_token, _raw_cookie = get_otp_form(client, settings)

        response = post_otp_request(
            client,
            csrf_token=csrf_token,
            phone="+998900009441",
        )

        assert_generic_request_redirect(response)

    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0


def test_post_otp_request_requires_csrf_before_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, _settings = make_client(m2_test_database)

    response = client.post(
        "/auth/otp/request",
        data={"phone": "+998900009451"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert_otp_security_headers(response)
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0


@pytest.mark.integration
def test_post_otp_request_http_phases_close_before_domain_lock(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert m2_test_database.dialect.name == "postgresql"
    phone = "+998900009461"
    client, settings = make_client(m2_test_database)
    csrf_token, _raw_cookie = get_otp_form(client, settings)
    user = add_user(db_session, phone)
    add_link(db_session, user, chat_id=9_984_000_061)
    phone_key = f"otp-login-issue:phone:{phone}"
    ip_key = f"otp-login-issue:ip:{CLIENT_IP.as_hmac_input()}"
    user_key = f"otp-login-issue:user:{user.id}"
    for scope, raw_key in (
        (issuance_module.OTP_LOGIN_ISSUE_PHONE_SCOPE, phone_key),
        (issuance_module.OTP_LOGIN_ISSUE_IP_SCOPE, ip_key),
        (issuance_module.OTP_LOGIN_ISSUE_USER_SCOPE, user_key),
    ):
        seed_rate_limit(
            db_session,
            settings,
            scope=scope,
            raw_key=raw_key,
            attempt_count=1,
        )
    db_session.commit()

    lifecycle = _SessionLifecycle()
    client.app.state.database_session_factory = tracking_session_factory(
        m2_test_database,
        lifecycle,
    )
    original_domain = issuance_module._request_login_otp_domain

    def tracked_domain(session: Session, **kwargs: Any):
        lifecycle.enter_domain(session, call_start=0, predecessor_count=4)
        return original_domain(session, **kwargs)

    monkeypatch.setattr(
        issuance_module,
        "_request_login_otp_domain",
        tracked_domain,
    )

    response = post_otp_request(client, csrf_token=csrf_token, phone=phone)

    assert_generic_request_redirect(response)
    assert lifecycle.domain_entries == 1
    assert len(lifecycle.sessions) == 5
    lifecycle.assert_all_closed()
    db_session.expire_all()
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_ISSUE_PHONE_SCOPE,
            raw_key=phone_key,
        )
        == 2
    )
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_ISSUE_IP_SCOPE,
            raw_key=ip_key,
        )
        == 2
    )
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_ISSUE_USER_SCOPE,
            raw_key=user_key,
        )
        == 2
    )
    assert count_table(db_session, OtpChallenge) == 1
    assert count_table(db_session, OtpDispatch) == 1
    assert count_table(db_session, OtpChallengeEvent) == 1


@pytest.mark.integration
def test_post_otp_request_http_rate_denial_never_enters_domain(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert m2_test_database.dialect.name == "postgresql"
    phone = "+998900009462"
    client, settings = make_client(m2_test_database)
    csrf_token, _raw_cookie = get_otp_form(client, settings)
    user = add_user(db_session, phone)
    add_link(db_session, user, chat_id=9_984_000_062)
    phone_key = f"otp-login-issue:phone:{phone}"
    ip_key = f"otp-login-issue:ip:{CLIENT_IP.as_hmac_input()}"
    user_key = f"otp-login-issue:user:{user.id}"
    seed_rate_limit(
        db_session,
        settings,
        scope=issuance_module.OTP_LOGIN_ISSUE_PHONE_SCOPE,
        raw_key=phone_key,
        attempt_count=settings.otp_login_rate_limit_phone_attempts - 1,
    )
    seed_rate_limit(
        db_session,
        settings,
        scope=issuance_module.OTP_LOGIN_ISSUE_IP_SCOPE,
        raw_key=ip_key,
        attempt_count=1,
    )
    seed_rate_limit(
        db_session,
        settings,
        scope=issuance_module.OTP_LOGIN_ISSUE_USER_SCOPE,
        raw_key=user_key,
        attempt_count=1,
    )
    db_session.commit()

    lifecycle = _SessionLifecycle()
    client.app.state.database_session_factory = tracking_session_factory(
        m2_test_database,
        lifecycle,
    )
    domain_calls: list[None] = []
    original_domain = issuance_module._request_login_otp_domain

    def forbidden_domain(session: Session, **kwargs: Any):
        domain_calls.append(None)
        return original_domain(session, **kwargs)

    monkeypatch.setattr(
        issuance_module,
        "_request_login_otp_domain",
        forbidden_domain,
    )

    response = post_otp_request(client, csrf_token=csrf_token, phone=phone)

    assert_generic_request_redirect(response)
    assert domain_calls == []
    assert len(lifecycle.sessions) == 2
    lifecycle.assert_all_closed()
    db_session.expire_all()
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_ISSUE_PHONE_SCOPE,
            raw_key=phone_key,
        )
        == settings.otp_login_rate_limit_phone_attempts
    )
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_ISSUE_IP_SCOPE,
            raw_key=ip_key,
        )
        == 2
    )
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_ISSUE_USER_SCOPE,
            raw_key=user_key,
        )
        == 1
    )
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0
    assert count_table(db_session, OtpChallengeEvent) == 0


def test_post_otp_request_route_has_csrf_dependency() -> None:
    route = next(
        route
        for route in auth_router_module.router.routes
        if getattr(route, "path_format", None) == "/auth/otp/request"
    )

    dependency_calls = [dependency.call for dependency in route.dependant.dependencies]
    assert auth_router_module.get_detached_otp_mutation_context in dependency_calls
    detached_source = inspect.getsource(
        auth_router_module.get_detached_otp_mutation_context
    )
    assert f"await {validate_csrf.__name__}(" in detached_source


def test_post_otp_request_route_has_no_delivery_or_shop_scope() -> None:
    source = inspect.getsource(auth_router_module.request_login_otp_route).casefold()

    for forbidden in (
        "send_message",
        "telegram_bot_token",
        "issue_link_token",
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
