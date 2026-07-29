import asyncio
import re
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import hash_session_token, resolve_by_raw_token
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.code import OtpCode
from app.otp.contracts import OtpChallengeStatus, OtpDispatchStatus
from app.otp.dispatch_service import (
    PreparedOtpDispatch,
    prepare_next_otp_dispatch,
    record_otp_delivery_result,
)
from app.otp.models import OtpChallenge, OtpDispatch
from app.otp.provider import (
    OtpDeliverySendResult,
    OtpDeliverySendStatus,
    TelegramOtpTarget,
)
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.telegram.models import TelegramLink
from app.telegram.service import unlink as unlink_telegram

NOW = datetime(2026, 7, 29, 23, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-web-flow-e2e"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-web-flow-e2e-at-least-32"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@dataclass
class FakeOtpProvider:
    calls: list[dict[str, object]] = field(default_factory=list)

    async def send_otp(
        self,
        *,
        target: TelegramOtpTarget,
        code: OtpCode,
        locale: str,
        ttl_seconds: int,
    ) -> OtpDeliverySendResult:
        self.calls.append(
            {
                "target": target,
                "code": code,
                "locale": locale,
                "ttl_seconds": ttl_seconds,
            }
        )
        return OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT)


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


def mutable_now(
    initial: datetime = NOW,
) -> tuple[dict[str, datetime], Callable[[], datetime]]:
    state = {"now": initial}
    return state, lambda: state["now"]


def make_client(
    engine: Engine,
    now_provider: Callable[[], datetime],
) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = now_provider
    return TestClient(application, client=("203.0.113.100", 50_000)), settings


def add_user_with_link(
    session: Session,
    *,
    phone: str,
    is_active: bool = True,
    linked: bool = True,
) -> User:
    user = User(phone=phone, is_active=is_active)
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=9_987_000_000
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


def get_otp_request_csrf(client: TestClient) -> str:
    response = client.get("/auth/otp")
    assert response.status_code == 200
    return extract_hidden_csrf_token(response.text)


def get_otp_verify_csrf(client: TestClient) -> str:
    response = client.get("/auth/otp/verify")
    assert response.status_code == 200
    return extract_hidden_csrf_token(response.text)


def post_otp_request(
    client: TestClient,
    *,
    csrf_token: str,
    phone: str,
    follow_redirects: bool = False,
):
    return client.post(
        "/auth/otp/request",
        data={"csrf_token": csrf_token, "phone": phone},
        follow_redirects=follow_redirects,
    )


def post_otp_verify(
    client: TestClient,
    *,
    csrf_token: str,
    code: str,
):
    return client.post(
        "/auth/otp/verify",
        data={"csrf_token": csrf_token, "code": code},
        follow_redirects=False,
    )


def post_new_code(client: TestClient, *, csrf_token: str):
    return client.post(
        "/auth/otp/new-code",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def fetch_session_by_cookie(session: Session, raw_cookie: str) -> AuthSession:
    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == hash_session_token(raw_cookie)
        )
    )
    assert auth_session is not None
    return auth_session


def count_authenticated_sessions(session: Session) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id.is_not(None))
        )
        or 0
    )


def assert_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def run_fake_dispatch(
    session: Session,
    settings: Settings,
    provider: FakeOtpProvider,
    *,
    now: datetime,
    code_value: int,
) -> PreparedOtpDispatch:
    prepared = prepare_next_otp_dispatch(
        session,
        otp_hmac_key=settings.require_otp_hmac_key(),
        now=now,
        ttl_seconds=settings.otp_login_ttl_seconds,
        claim_stale_seconds=settings.otp_dispatch_claim_stale_seconds,
        code_generator=lambda _upper: code_value,
    )
    assert prepared is not None
    session.commit()
    send_result = asyncio.run(
        provider.send_otp(
            target=prepared.target,
            code=prepared.code,
            locale=prepared.locale,
            ttl_seconds=prepared.ttl_seconds,
        )
    )
    assert record_otp_delivery_result(
        session,
        dispatch_id=prepared.dispatch_id,
        result=send_result,
        now=now + timedelta(seconds=1),
    )
    session.commit()
    return prepared


def latest_challenge(session: Session) -> OtpChallenge:
    challenge = session.scalar(
        select(OtpChallenge).order_by(OtpChallenge.created_at.desc()).limit(1)
    )
    assert challenge is not None
    return challenge


def test_e2e_fake_dispatch_success_rotates_session_and_opens_account(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    user = add_user_with_link(db_session, phone="+998900009801")
    db_session.commit()
    request_csrf = get_otp_request_csrf(client)
    old_cookie = client.cookies.get(settings.session_cookie_name)
    assert old_cookie is not None
    old_session = fetch_session_by_cookie(db_session, old_cookie)

    request_response = post_otp_request(
        client,
        csrf_token=request_csrf,
        phone="+998900009801",
    )
    assert request_response.status_code == 303
    assert request_response.headers["location"] == "/auth/otp/verify"
    provider = FakeOtpProvider()
    prepared = run_fake_dispatch(
        db_session,
        settings,
        provider,
        now=NOW + timedelta(seconds=2),
        code_value=271828,
    )
    assert len(provider.calls) == 1
    assert provider.calls[0]["code"].as_internal_value() == "271828"
    assert provider.calls[0]["locale"] == "uz-Latn"
    assert "271828" not in repr(prepared)
    verify_csrf = get_otp_verify_csrf(client)
    now_state["now"] = NOW + timedelta(seconds=4)

    verify_response = post_otp_verify(
        client,
        csrf_token=verify_csrf,
        code="271828",
    )

    assert verify_response.status_code == 303
    assert verify_response.headers["location"] == "/auth/account"
    assert settings.session_cookie_name in verify_response.headers["set-cookie"]
    assert_security_headers(verify_response)
    new_cookie = client.cookies.get(settings.session_cookie_name)
    assert new_cookie is not None
    assert new_cookie != old_cookie
    account_response = client.get("/auth/account")
    assert account_response.status_code == 200
    assert_security_headers(account_response)
    db_session.expire_all()
    assert count_authenticated_sessions(db_session) == 1
    assert (
        resolve_by_raw_token(db_session, old_cookie, NOW + timedelta(seconds=5)) is None
    )
    new_session = fetch_session_by_cookie(db_session, new_cookie)
    assert new_session.user_id == user.id
    assert new_session.active_shop_id is None
    persisted_old = db_session.get(AuthSession, old_session.id)
    assert persisted_old is not None
    assert persisted_old.revoked_at == NOW + timedelta(seconds=4)
    challenge = db_session.get(OtpChallenge, prepared.challenge_id)
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.CONSUMED.value
    assert "271828" not in verify_response.text
    assert "271828" not in account_response.text


def test_e2e_other_browser_and_replay_never_create_second_login(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    user = add_user_with_link(db_session, phone="+998900009802")
    db_session.commit()
    request_csrf = get_otp_request_csrf(client)
    assert (
        post_otp_request(
            client,
            csrf_token=request_csrf,
            phone="+998900009802",
        ).status_code
        == 303
    )
    provider = FakeOtpProvider()
    run_fake_dispatch(
        db_session,
        settings,
        provider,
        now=NOW + timedelta(seconds=2),
        code_value=314159,
    )

    other_client, _other_settings = make_client(m2_test_database, now_provider)
    other_csrf = get_otp_verify_csrf(other_client)
    other_response = post_otp_verify(
        other_client,
        csrf_token=other_csrf,
        code="314159",
    )
    assert other_response.status_code == 303
    assert other_response.headers["location"] == "/auth/otp/verify?error=invalid"
    assert count_authenticated_sessions(db_session) == 0

    old_csrf = get_otp_verify_csrf(client)
    old_cookie = client.cookies.get(settings.session_cookie_name)
    assert old_cookie is not None
    now_state["now"] = NOW + timedelta(seconds=4)
    success = post_otp_verify(client, csrf_token=old_csrf, code="314159")
    assert success.status_code == 303
    assert count_authenticated_sessions(db_session) == 1
    replay = post_otp_verify(client, csrf_token=old_csrf, code="314159")
    assert replay.status_code == 403
    assert replay.headers["x-error-code"] == "CSRF_FAILED"
    assert count_authenticated_sessions(db_session) == 1
    new_cookie = client.cookies.get(settings.session_cookie_name)
    assert new_cookie is not None
    assert new_cookie != old_cookie
    assert fetch_session_by_cookie(db_session, new_cookie).user_id == user.id


def test_e2e_new_code_supersedes_old_code_and_new_code_succeeds(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    user = add_user_with_link(db_session, phone="+998900009803")
    db_session.commit()
    request_csrf = get_otp_request_csrf(client)
    assert (
        post_otp_request(
            client,
            csrf_token=request_csrf,
            phone="+998900009803",
        ).status_code
        == 303
    )
    provider = FakeOtpProvider()
    first = run_fake_dispatch(
        db_session,
        settings,
        provider,
        now=NOW + timedelta(seconds=2),
        code_value=111111,
    )
    verify_csrf = get_otp_verify_csrf(client)
    now_state["now"] = NOW + timedelta(seconds=61)
    assert post_new_code(client, csrf_token=verify_csrf).status_code == 303
    second = run_fake_dispatch(
        db_session,
        settings,
        provider,
        now=NOW + timedelta(seconds=62),
        code_value=222222,
    )
    assert len(provider.calls) == 2
    now_state["now"] = NOW + timedelta(seconds=64)

    old_code_response = post_otp_verify(
        client,
        csrf_token=verify_csrf,
        code="111111",
    )
    new_code_response = post_otp_verify(
        client,
        csrf_token=verify_csrf,
        code="222222",
    )

    assert old_code_response.status_code == 303
    assert old_code_response.headers["location"] == "/auth/otp/verify?error=invalid"
    assert new_code_response.status_code == 303
    assert new_code_response.headers["location"] == "/auth/account"
    db_session.expire_all()
    old_challenge = db_session.get(OtpChallenge, first.challenge_id)
    new_challenge = db_session.get(OtpChallenge, second.challenge_id)
    assert old_challenge is not None
    assert new_challenge is not None
    assert old_challenge.status == OtpChallengeStatus.SUPERSEDED.value
    assert new_challenge.status == OtpChallengeStatus.CONSUMED.value
    assert (
        fetch_session_by_cookie(
            db_session,
            client.cookies.get(settings.session_cookie_name),
        ).user_id
        == user.id
    )


def test_e2e_unlink_after_dispatch_invalidates_without_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    user = add_user_with_link(db_session, phone="+998900009804")
    db_session.commit()
    request_csrf = get_otp_request_csrf(client)
    assert (
        post_otp_request(
            client,
            csrf_token=request_csrf,
            phone="+998900009804",
        ).status_code
        == 303
    )
    provider = FakeOtpProvider()
    prepared = run_fake_dispatch(
        db_session,
        settings,
        provider,
        now=NOW + timedelta(seconds=2),
        code_value=987654,
    )
    unlink_telegram(db_session, user, NOW + timedelta(seconds=3))
    db_session.commit()
    verify_csrf = get_otp_verify_csrf(client)
    now_state["now"] = NOW + timedelta(seconds=4)

    response = post_otp_verify(client, csrf_token=verify_csrf, code="987654")

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/otp/verify?error=invalid"
    assert count_authenticated_sessions(db_session) == 0
    db_session.expire_all()
    challenge = db_session.get(OtpChallenge, prepared.challenge_id)
    assert challenge is not None
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value


def test_e2e_unknown_unlinked_inactive_and_dispatcher_stopped_are_uniform(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now_state, now_provider = mutable_now()
    client, _settings = make_client(m2_test_database, now_provider)
    inactive = add_user_with_link(
        db_session,
        phone="+998900009805",
        is_active=False,
    )
    unlinked = add_user_with_link(
        db_session,
        phone="+998900009806",
        linked=False,
    )
    eligible = add_user_with_link(db_session, phone="+998900009807")
    db_session.commit()
    _ = inactive, unlinked, eligible
    phones = [
        "+998900009899",
        "+998900009805",
        "+998900009806",
        "+998900009807",
    ]
    shapes = []
    for phone in phones:
        csrf = get_otp_request_csrf(client)
        response = post_otp_request(client, csrf_token=csrf, phone=phone)
        assert response.status_code == 303
        assert_security_headers(response)
        shapes.append(
            (response.status_code, response.headers["location"], response.text)
        )
    assert len(set(shapes)) == 1
    verify_page = client.get(shapes[-1][1])
    assert verify_page.status_code == 200
    assert "Parol bilan kirish" in verify_page.text
    assert "Agar kiritilgan telefon" in verify_page.text
    dispatch = db_session.scalar(select(OtpDispatch))
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.PENDING.value


def test_no_public_otp_status_routes_exist(m2_test_database: Engine) -> None:
    client, _settings = make_client(m2_test_database, lambda: NOW)

    for path in (
        "/auth/otp/status",
        "/auth/otp/delivery-status",
        "/auth/otp/dispatch/status",
    ):
        response = client.get(path)
        assert response.status_code == 404
