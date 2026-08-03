import re
import subprocess
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.db import create_database_session_factory
from app.main import create_app
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
    OtpPurpose,
)
from app.otp.crypto import compute_otp_code_mac
from app.otp.dispatch_service import (
    PreparedOtpDispatch,
    prepare_next_otp_dispatch,
    record_otp_delivery_result,
)
from app.otp.models import (
    OtpChallenge,
    OtpChallengeEvent,
    OtpDispatch,
    OtpDispatcherState,
)
from app.otp.provider import OtpDeliverySendResult, OtpDeliverySendStatus
from app.otp.repository import activate_challenge, create_pending_challenge
from app.otp.verification import verify_login_otp
from app.settings import Settings
from app.telegram.models import TelegramLink

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)
PHONE = "+998900009991"
CLIENT_IP = "198.51.100.62"
CHAT_ID = 9_989_000_991
RAW_CODE = "456789"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-sensitive-audit"
OLD_OTP_HMAC_KEY = "old-otp-sensitive-audit-key-at-least-32-chars"
NEW_OTP_HMAC_KEY = "new-otp-sensitive-audit-key-at-least-32-chars"
TEST_OTP_HMAC_KEY = "test-otp-sensitive-audit-key-at-least-32-chars"
VALID_DIGEST = "7" * 64
REAL_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35,}\b")


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
    otp_hmac_key: str = TEST_OTP_HMAC_KEY,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        anonymous_session_ttl_minutes=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=otp_hmac_key,
    )


def make_client(
    engine: Engine,
    now_provider: Callable[[], datetime],
) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = now_provider
    return TestClient(application, client=(CLIENT_IP, 50_000)), settings


def mutable_now() -> tuple[dict[str, datetime], Callable[[], datetime]]:
    state = {"now": NOW}
    return state, lambda: state["now"]


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def add_user_with_link(session: Session) -> User:
    user = User(phone=PHONE)
    session.add(user)
    session.flush()
    session.add(
        TelegramLink(
            user_id=user.id,
            telegram_chat_id=CHAT_ID,
            linked_at=NOW,
            phone_verified_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()
    return user


def run_fake_dispatch(
    session: Session,
    settings: Settings,
    *,
    now: datetime,
) -> PreparedOtpDispatch:
    prepared = prepare_next_otp_dispatch(
        session,
        otp_hmac_key=settings.require_otp_hmac_key(),
        now=now,
        ttl_seconds=settings.otp_login_ttl_seconds,
        claim_stale_seconds=settings.otp_dispatch_claim_stale_seconds,
        code_generator=lambda _upper: int(RAW_CODE),
    )
    assert prepared is not None
    assert prepared.code.as_internal_value() == RAW_CODE
    session.commit()
    assert record_otp_delivery_result(
        session,
        dispatch_id=prepared.dispatch_id,
        result=OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT),
        now=now + timedelta(seconds=1),
    )
    session.commit()
    return prepared


def post_request(client: TestClient, *, csrf_token: str):
    return client.post(
        "/auth/otp/request",
        data={"csrf_token": csrf_token, "phone": PHONE},
        follow_redirects=False,
    )


def post_verify(client: TestClient, *, csrf_token: str):
    return client.post(
        "/auth/otp/verify",
        data={"csrf_token": csrf_token, "code": RAW_CODE},
        follow_redirects=False,
    )


def render_model_values(session: Session, *models) -> str:
    rendered: list[str] = []
    for model in models:
        rows = session.scalars(select(model)).all()
        for row in rows:
            for column in model.__table__.columns:
                value = getattr(row, column.name)
                if value is not None:
                    rendered.append(str(value))
    return "\n".join(rendered)


def test_e2e_flow_keeps_sensitive_values_out_of_db_html_urls_and_logs(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now_state, now_provider = mutable_now()
    client, settings = make_client(m2_test_database, now_provider)
    user = add_user_with_link(db_session)
    db_session.commit()

    with caplog.at_level("DEBUG"):
        request_page = client.get("/auth/otp")
        assert request_page.status_code == 200
        request_csrf = extract_hidden_csrf_token(request_page.text)
        old_cookie = client.cookies.get(settings.session_cookie_name)
        assert old_cookie is not None
        request_response = post_request(client, csrf_token=request_csrf)
        assert request_response.status_code == 303
        prepared = run_fake_dispatch(
            db_session,
            settings,
            now=NOW + timedelta(seconds=2),
        )
        verify_page = client.get("/auth/otp/verify")
        assert verify_page.status_code == 200
        verify_csrf = extract_hidden_csrf_token(verify_page.text)
        now_state["now"] = NOW + timedelta(seconds=4)
        verify_response = post_verify(client, csrf_token=verify_csrf)
        assert verify_response.status_code == 303
        account_response = client.get("/auth/account")
        assert account_response.status_code == 200

    new_cookie = client.cookies.get(settings.session_cookie_name)
    assert new_cookie is not None
    assert new_cookie != old_cookie
    public_html_and_urls = "\n".join(
        [
            request_page.text,
            request_response.headers["location"],
            verify_page.text,
            verify_response.headers["location"],
            account_response.text,
        ]
    )
    m7_values = render_model_values(
        db_session,
        OtpChallenge,
        OtpDispatch,
        OtpChallengeEvent,
        OtpDispatcherState,
    )
    auth_session_values = render_model_values(db_session, AuthSession)
    log_values = caplog.text

    for sensitive in (
        PHONE,
        CLIENT_IP,
        str(CHAT_ID),
        TEST_OTP_HMAC_KEY,
        old_cookie,
        new_cookie,
    ):
        assert sensitive not in m7_values
        assert sensitive not in auth_session_values
        assert sensitive not in log_values

    for sensitive in (PHONE, CLIENT_IP, str(CHAT_ID), TEST_OTP_HMAC_KEY, RAW_CODE):
        assert sensitive not in public_html_and_urls
        assert sensitive not in log_values

    assert RAW_CODE not in m7_values
    assert RAW_CODE not in auth_session_values
    challenge = db_session.get(OtpChallenge, prepared.challenge_id)
    assert challenge is not None
    assert challenge.user_id == user.id
    assert challenge.status == OtpChallengeStatus.CONSUMED.value
    assert challenge.code_mac is not None
    assert re.fullmatch(r"[0-9a-f]{64}", challenge.code_mac)
    assert RAW_CODE not in challenge.code_mac
    dispatch = db_session.get(OtpDispatch, prepared.dispatch_id)
    assert dispatch is not None
    assert dispatch.status == OtpDispatchStatus.SENT.value
    assert not hasattr(dispatch, "payload")


def test_rotated_otp_key_invalidates_existing_active_challenge_without_consuming(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    old_settings = make_settings(m2_test_database, otp_hmac_key=OLD_OTP_HMAC_KEY)
    new_settings = make_settings(m2_test_database, otp_hmac_key=NEW_OTP_HMAC_KEY)
    user = add_user_with_link(db_session)
    link = db_session.scalar(
        select(TelegramLink).where(TelegramLink.user_id == user.id)
    )
    assert link is not None
    challenge = create_pending_challenge(
        db_session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=VALID_DIGEST,
        now=NOW,
    )
    code_mac = compute_otp_code_mac(
        otp_hmac_key=SecretStr(old_settings.require_otp_hmac_key().get_secret_value()),
        challenge_id=challenge.id,
        user_id=user.id,
        purpose=OtpPurpose.LOGIN,
        code=OtpCode(RAW_CODE),
    )
    activate_challenge(
        db_session,
        challenge=challenge,
        code_mac=code_mac,
        activated_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=3),
    )

    result = verify_login_otp(
        db_session,
        new_settings,
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input=RAW_CODE,
        now=NOW + timedelta(seconds=2),
    )

    assert result.outcome is OtpInternalOutcome.OTP_INVALID
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert challenge.consumed_at is None
    assert challenge.failed_attempts == 1
    assert RAW_CODE not in render_model_values(
        db_session,
        OtpChallenge,
        OtpChallengeEvent,
    )
    event = db_session.scalar(select(OtpChallengeEvent))
    assert event is not None
    assert event.action == OtpChallengeEventAction.VERIFY_FAILED.value
    assert event.safe_code == OtpInternalOutcome.OTP_INVALID.value


def test_tracked_files_do_not_contain_real_telegram_bot_token_literals() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    offenders = []
    for relative_path in tracked:
        path = PROJECT_ROOT / relative_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        realish_tokens = [
            token
            for token in REAL_TELEGRAM_TOKEN_RE.findall(text)
            if not any(
                marker in token.casefold()
                for marker in ("test", "sensitive", "example", "dummy", "fake")
            )
        ]
        if realish_tokens:
            offenders.append(relative_path)

    assert offenders == []
