import inspect
import re
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
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
    create_authenticated_session,
    hash_session_token,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
)
from app.otp.crypto import derive_browser_binding_digest
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import create_pending_challenge, create_pending_dispatch
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 29, 21, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-new-code-post"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-new-code-post-at-least-32"
CLIENT_IP = ResolvedClientIp("203.0.113.80")


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
        otp_hmac_key=TEST_OTP_HMAC_KEY if with_otp_key else None,
        otp_login_rate_limit_user_attempts=user_attempts,
        otp_login_rate_limit_ip_attempts=ip_attempts,
    )


def make_client(
    engine: Engine,
    now_provider: Callable[[], datetime],
    *,
    with_otp_key: bool = True,
    user_attempts: int = 3,
    ip_attempts: int = 20,
) -> tuple[TestClient, Settings]:
    settings = make_settings(
        engine,
        with_otp_key=with_otp_key,
        user_attempts=user_attempts,
        ip_attempts=ip_attempts,
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = now_provider
    return TestClient(application, client=("203.0.113.80", 50_000)), settings


def fixed_now(value: datetime = NOW) -> Callable[[], datetime]:
    return lambda: value


def mutable_now(
    initial: datetime = NOW,
) -> tuple[dict[str, datetime], Callable[[], datetime]]:
    state = {"now": initial}
    return state, lambda: state["now"]


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


def get_verify_form(client: TestClient, settings: Settings) -> tuple[str, str]:
    response = client.get("/auth/otp/verify")
    assert response.status_code == 200
    csrf_token = extract_hidden_csrf_token(response.text)
    raw_cookie = client.cookies.get(settings.session_cookie_name)
    assert raw_cookie is not None
    return csrf_token, raw_cookie


def post_new_code(
    client: TestClient,
    *,
    csrf_token: str,
    next_url: str | None = None,
):
    data = {"csrf_token": csrf_token}
    if next_url is not None:
        data["next"] = next_url
    return client.post(
        "/auth/otp/new-code",
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
    phone: str = "+998900009601",
) -> tuple[User, TelegramLink]:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=9_986_000_601,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    return user, link


def seed_pending_challenge(
    session: Session,
    settings: Settings,
    auth_session: AuthSession,
    *,
    created_at: datetime = NOW - timedelta(seconds=70),
) -> OtpChallenge:
    user, link = add_user_and_link(session)
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
        now=created_at,
    )
    create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=created_at,
    )
    session.flush()
    return challenge


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


def outstanding_challenges(session: Session) -> list[OtpChallenge]:
    return list(
        session.scalars(
            select(OtpChallenge)
            .where(
                OtpChallenge.status.in_(
                    [
                        OtpChallengeStatus.PENDING_DISPATCH.value,
                        OtpChallengeStatus.ACTIVE.value,
                    ]
                )
            )
            .order_by(OtpChallenge.created_at)
        ).all()
    )


def assert_otp_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_new_code_redirect(response, *, location: str = "/auth/otp/verify") -> None:
    assert response.status_code == 303
    assert response.headers["location"] == location
    assert response.text == ""
    assert "set-cookie" not in response.headers
    assert_otp_security_headers(response)


def test_post_new_code_allowed_supersedes_old_and_creates_fresh_dispatch(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database, fixed_now())
    csrf_token, raw_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, raw_cookie)
    old_challenge = seed_pending_challenge(db_session, settings, anonymous_session)
    db_session.commit()

    response = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(response)
    db_session.expire_all()
    persisted_old = db_session.get(OtpChallenge, old_challenge.id)
    assert persisted_old is not None
    assert persisted_old.status == OtpChallengeStatus.SUPERSEDED.value
    assert count_table(db_session, OtpChallenge) == 2
    assert count_table(db_session, OtpDispatch) == 2
    outstanding = outstanding_challenges(db_session)
    assert len(outstanding) == 1
    assert outstanding[0].id != old_challenge.id
    new_dispatch = db_session.scalar(
        select(OtpDispatch).where(OtpDispatch.challenge_id == outstanding[0].id)
    )
    assert new_dispatch is not None
    assert new_dispatch.status == OtpDispatchStatus.PENDING.value
    old_dispatch = db_session.scalar(
        select(OtpDispatch).where(OtpDispatch.challenge_id == old_challenge.id)
    )
    assert old_dispatch is not None
    assert old_dispatch.status == OtpDispatchStatus.CANCELLED.value
    actions = list(
        db_session.scalars(
            select(OtpChallengeEvent.action).order_by(OtpChallengeEvent.occurred_at)
        ).all()
    )
    assert actions == [
        OtpChallengeEventAction.SUPERSEDED.value,
        OtpChallengeEventAction.ISSUED.value,
    ]


@pytest.mark.parametrize("has_current_challenge", [False, True])
def test_post_new_code_missing_browser_or_cooldown_same_redirect(
    m2_test_database: Engine,
    db_session: Session,
    has_current_challenge: bool,
) -> None:
    client, settings = make_client(m2_test_database, fixed_now())
    csrf_token, raw_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, raw_cookie)
    if has_current_challenge:
        seed_pending_challenge(
            db_session,
            settings,
            anonymous_session,
            created_at=NOW,
        )
    db_session.commit()

    response = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(response)
    assert count_table(db_session, OtpChallenge) == (1 if has_current_challenge else 0)
    assert count_table(db_session, OtpDispatch) == (1 if has_current_challenge else 0)


def test_post_new_code_rapid_repeat_hits_cooldown_before_duplicate_issue(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    csrf_token, raw_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, raw_cookie)
    seed_pending_challenge(
        db_session,
        settings,
        anonymous_session,
        created_at=NOW - timedelta(seconds=70),
    )
    db_session.commit()
    first = post_new_code(client, csrf_token=csrf_token)
    assert_new_code_redirect(first)
    now_state["now"] = NOW + timedelta(seconds=1)

    second = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(second)
    assert count_table(db_session, OtpChallenge) == 2
    assert len(outstanding_challenges(db_session)) == 1


def test_post_new_code_third_window_attempt_is_rate_limited_and_preserves_current(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(
        m2_test_database,
        now_provider,
        user_attempts=3,
        ip_attempts=20,
    )
    csrf_token, raw_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, raw_cookie)
    seed_pending_challenge(
        db_session,
        settings,
        anonymous_session,
        created_at=NOW - timedelta(seconds=70),
    )
    db_session.commit()
    first = post_new_code(client, csrf_token=csrf_token)
    assert_new_code_redirect(first)
    now_state["now"] = NOW + timedelta(seconds=61)

    second = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(second)
    now_state["now"] = NOW + timedelta(seconds=122)
    third = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(third)
    assert count_table(db_session, OtpChallenge) == 3
    assert len(outstanding_challenges(db_session)) == 1


def test_post_new_code_safe_next_policy_and_no_raw_identifiers(
    m2_test_database: Engine,
) -> None:
    client, settings = make_client(m2_test_database, fixed_now())
    csrf_token, _raw_cookie = get_verify_form(client, settings)

    safe_response = post_new_code(
        client,
        csrf_token=csrf_token,
        next_url="/customer/profile",
    )
    unsafe_response = post_new_code(
        client,
        csrf_token=csrf_token,
        next_url="https://example.test/customer",
    )

    assert_new_code_redirect(
        safe_response,
        location="/auth/otp/verify?next=%2Fcustomer%2Fprofile",
    )
    assert_new_code_redirect(unsafe_response)
    for response in (safe_response, unsafe_response):
        assert "challenge" not in response.headers["location"]
        assert "phone" not in response.headers["location"]
        assert "example.test" not in response.headers["location"]


def test_post_new_code_authenticated_user_redirects_without_otp_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database, fixed_now())
    user = User(phone="+998900009611")
    db_session.add(user)
    db_session.flush()
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-otp-new-code",
        NOW,
        settings=settings,
    )
    db_session.commit()
    set_client_session_cookie(client, settings, created)
    csrf_token = get_csrf_token(created.session).as_form_value()

    response = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(response, location="/auth/account")
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0


def test_post_new_code_missing_otp_key_degrades_generic_without_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database, fixed_now(), with_otp_key=False)
    csrf_token, _raw_cookie = get_verify_form(client, settings)

    response = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(response)
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0


def test_post_new_code_requires_csrf_before_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, _settings = make_client(m2_test_database, fixed_now())

    response = client.post("/auth/otp/new-code", data={}, follow_redirects=False)

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert_otp_security_headers(response)
    assert count_table(db_session, OtpChallenge) == 0
    assert count_table(db_session, OtpDispatch) == 0


@pytest.mark.integration
def test_post_new_code_http_cooldown_and_closed_rate_to_domain_phases(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert m2_test_database.dialect.name == "postgresql"
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    csrf_token, raw_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, raw_cookie)
    old_challenge = seed_pending_challenge(
        db_session,
        settings,
        anonymous_session,
        created_at=NOW,
    )
    assert old_challenge.user_id is not None
    ip_key = f"otp-login-new-code:ip:{CLIENT_IP.as_hmac_input()}"
    user_key = f"otp-login-new-code:user:{old_challenge.user_id}"
    seed_rate_limit(
        db_session,
        settings,
        scope=issuance_module.OTP_LOGIN_NEW_CODE_IP_SCOPE,
        raw_key=ip_key,
        attempt_count=1,
    )
    seed_rate_limit(
        db_session,
        settings,
        scope=issuance_module.OTP_LOGIN_NEW_CODE_USER_SCOPE,
        raw_key=user_key,
        attempt_count=1,
    )
    db_session.commit()

    lifecycle = _SessionLifecycle()
    client.app.state.database_session_factory = tracking_session_factory(
        m2_test_database,
        lifecycle,
    )
    domain_call_start = {"value": 0}
    original_domain = issuance_module._request_new_login_code_domain

    def tracked_domain(session: Session, **kwargs: Any):
        lifecycle.enter_domain(
            session,
            call_start=domain_call_start["value"],
            predecessor_count=3,
        )
        return original_domain(session, **kwargs)

    monkeypatch.setattr(
        issuance_module,
        "_request_new_login_code_domain",
        tracked_domain,
    )
    now_state["now"] = NOW + timedelta(seconds=59)

    early = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(early)
    assert lifecycle.domain_entries == 0
    assert len(lifecycle.sessions) == 2
    lifecycle.assert_all_closed()
    db_session.expire_all()
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_NEW_CODE_IP_SCOPE,
            raw_key=ip_key,
        )
        == 1
    )
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_NEW_CODE_USER_SCOPE,
            raw_key=user_key,
        )
        == 1
    )
    assert count_table(db_session, OtpChallenge) == 1
    assert count_table(db_session, OtpDispatch) == 1
    assert count_table(db_session, OtpChallengeEvent) == 0

    domain_call_start["value"] = len(lifecycle.sessions)
    now_state["now"] = NOW + timedelta(seconds=60)
    allowed = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(allowed)
    assert (
        allowed.status_code,
        allowed.headers["location"],
        allowed.text,
        allowed.headers["cache-control"],
    ) == (
        early.status_code,
        early.headers["location"],
        early.text,
        early.headers["cache-control"],
    )
    assert lifecycle.domain_entries == 1
    assert len(lifecycle.sessions[domain_call_start["value"] :]) == 4
    lifecycle.assert_all_closed(call_start=domain_call_start["value"])
    db_session.expire_all()
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_NEW_CODE_IP_SCOPE,
            raw_key=ip_key,
        )
        == 2
    )
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_NEW_CODE_USER_SCOPE,
            raw_key=user_key,
        )
        == 2
    )
    persisted_old = db_session.get(OtpChallenge, old_challenge.id)
    assert persisted_old is not None
    assert persisted_old.status == OtpChallengeStatus.SUPERSEDED.value
    assert count_table(db_session, OtpChallenge) == 2
    assert count_table(db_session, OtpDispatch) == 2
    assert count_table(db_session, OtpChallengeEvent) == 2


@pytest.mark.integration
def test_post_new_code_http_rate_denial_never_enters_domain(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert m2_test_database.dialect.name == "postgresql"
    client, settings = make_client(
        m2_test_database,
        fixed_now(),
        user_attempts=3,
        ip_attempts=20,
    )
    csrf_token, raw_cookie = get_verify_form(client, settings)
    anonymous_session = fetch_session_by_cookie(db_session, raw_cookie)
    old_challenge = seed_pending_challenge(db_session, settings, anonymous_session)
    assert old_challenge.user_id is not None
    ip_key = f"otp-login-new-code:ip:{CLIENT_IP.as_hmac_input()}"
    user_key = f"otp-login-new-code:user:{old_challenge.user_id}"
    seed_rate_limit(
        db_session,
        settings,
        scope=issuance_module.OTP_LOGIN_NEW_CODE_IP_SCOPE,
        raw_key=ip_key,
        attempt_count=1,
    )
    seed_rate_limit(
        db_session,
        settings,
        scope=issuance_module.OTP_LOGIN_NEW_CODE_USER_SCOPE,
        raw_key=user_key,
        attempt_count=settings.otp_login_rate_limit_user_attempts - 1,
    )
    db_session.commit()

    lifecycle = _SessionLifecycle()
    client.app.state.database_session_factory = tracking_session_factory(
        m2_test_database,
        lifecycle,
    )
    domain_calls: list[None] = []
    original_domain = issuance_module._request_new_login_code_domain

    def forbidden_domain(session: Session, **kwargs: Any):
        domain_calls.append(None)
        return original_domain(session, **kwargs)

    monkeypatch.setattr(
        issuance_module,
        "_request_new_login_code_domain",
        forbidden_domain,
    )

    response = post_new_code(client, csrf_token=csrf_token)

    assert_new_code_redirect(response)
    assert domain_calls == []
    assert len(lifecycle.sessions) == 3
    lifecycle.assert_all_closed()
    db_session.expire_all()
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_NEW_CODE_IP_SCOPE,
            raw_key=ip_key,
        )
        == 2
    )
    assert (
        rate_limit_attempt_count(
            db_session,
            settings,
            scope=issuance_module.OTP_LOGIN_NEW_CODE_USER_SCOPE,
            raw_key=user_key,
        )
        == settings.otp_login_rate_limit_user_attempts
    )
    persisted_old = db_session.get(OtpChallenge, old_challenge.id)
    assert persisted_old is not None
    assert persisted_old.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert count_table(db_session, OtpChallenge) == 1
    assert count_table(db_session, OtpDispatch) == 1
    assert count_table(db_session, OtpChallengeEvent) == 0


def test_post_new_code_route_has_csrf_dependency() -> None:
    route = next(
        route
        for route in auth_router_module.router.routes
        if getattr(route, "path_format", None) == "/auth/otp/new-code"
    )

    dependency_calls = [dependency.call for dependency in route.dependant.dependencies]
    assert auth_router_module.get_detached_otp_mutation_context in dependency_calls
    detached_source = inspect.getsource(
        auth_router_module.get_detached_otp_mutation_context
    )
    assert f"await {validate_csrf.__name__}(" in detached_source


def test_post_new_code_route_has_no_phone_delivery_or_shop_scope() -> None:
    source = inspect.getsource(
        auth_router_module.request_new_login_otp_route
    ).casefold()

    for forbidden in (
        "phone",
        "send_message",
        "telegram_bot_token",
        "request_login_otp(",
        "verify_login_otp",
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
