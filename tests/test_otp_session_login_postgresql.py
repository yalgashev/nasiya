import inspect
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.otp.session_login as session_login_module
from app.auth.csrf import get_csrf_token
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import (
    create_anonymous_session,
    create_authenticated_session,
    resolve_by_raw_token,
    revoke_session,
)
from app.db import create_database_session_factory
from app.otp.code import OtpCode
from app.otp.contracts import OtpChallengeStatus, OtpInternalOutcome, OtpPurpose
from app.otp.crypto import compute_otp_code_mac
from app.otp.models import OtpChallenge, OtpChallengeEvent
from app.otp.repository import activate_challenge, create_pending_challenge
from app.otp.session_login import (
    get_otp_success_redirect_target,
    rotate_session_after_otp_consume,
)
from app.otp.verification import OtpVerificationResult, verify_login_otp
from app.settings import Settings
from app.shop.models import Shop
from app.telegram.models import TelegramLink
from app.telegram.service import unlink as unlink_telegram

NOW = datetime(2026, 7, 29, 16, 0, tzinfo=UTC)
OTP_HMAC_KEY = "test-otp-session-login-hmac-key-at-least-32-chars"
RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-session-login"
VALID_DIGEST = "2" * 64
_SHOP_METADATA_TABLE = Shop.__table__


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
        rate_limit_hmac_key=RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=SecretStr(OTP_HMAC_KEY),
    )


def create_active_challenge(session: Session) -> OtpChallenge:
    user = User(phone="+998900009301")
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=9_983_000_301,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    challenge = create_pending_challenge(
        session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=VALID_DIGEST,
        now=NOW,
    )
    code_mac = compute_otp_code_mac(
        otp_hmac_key=SecretStr(OTP_HMAC_KEY),
        challenge_id=challenge.id,
        user_id=user.id,
        purpose=OtpPurpose.LOGIN,
        code=OtpCode("123456"),
    )
    return activate_challenge(
        session,
        challenge=challenge,
        code_mac=code_mac,
        activated_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=3),
    )


@pytest.mark.integration
def test_otp_consume_reuses_existing_session_and_csrf_rotation(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    challenge = create_active_challenge(db_session)
    anonymous = create_anonymous_session(
        db_session,
        "old anonymous ua",
        NOW,
        settings=settings,
    )
    other = create_authenticated_session(
        db_session,
        challenge.user_id,
        "other user session",
        NOW,
        settings=settings,
    )
    old_raw_token = anonymous.raw_token
    old_csrf_secret = anonymous.session.csrf_secret
    old_csrf_token = get_csrf_token(anonymous.session)

    verification_result = verify_login_otp(
        db_session,
        settings,
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
    )
    created = rotate_session_after_otp_consume(
        db_session,
        verification_result=verification_result,
        current_session=anonymous.session,
        user_agent="otp login ua",
        now=NOW + timedelta(seconds=3),
        settings=settings,
    )

    assert created is not None
    assert verification_result.consumed is True
    assert anonymous.session.revoked_at == NOW + timedelta(seconds=3)
    assert created.raw_token.as_cookie_value() != old_raw_token.as_cookie_value()
    assert created.session.user_id == challenge.user_id
    assert created.session.user_agent == "otp login ua"
    assert created.session.active_shop_id is None
    assert created.session.csrf_secret != old_csrf_secret
    assert get_csrf_token(created.session) != old_csrf_token
    assert other.session.revoked_at is None
    assert (
        resolve_by_raw_token(
            db_session,
            old_raw_token,
            NOW + timedelta(seconds=4),
        )
        is None
    )
    resolved_new = resolve_by_raw_token(
        db_session,
        created.raw_token,
        NOW + timedelta(seconds=4),
    )
    assert resolved_new is not None
    assert resolved_new.authenticated_user is not None
    assert resolved_new.authenticated_user.id == challenge.user_id


@pytest.mark.integration
def test_unlink_first_invalidates_otp_and_prevents_session_login(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    challenge = create_active_challenge(db_session)
    anonymous = create_anonymous_session(db_session, "ua", NOW, settings=settings)
    user = db_session.get(User, challenge.user_id)
    assert user is not None

    unlink_telegram(db_session, user, NOW + timedelta(seconds=2))
    verification_result = verify_login_otp(
        db_session,
        settings,
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=3),
    )
    created = rotate_session_after_otp_consume(
        db_session,
        verification_result=verification_result,
        current_session=anonymous.session,
        user_agent="otp login ua",
        now=NOW + timedelta(seconds=4),
        settings=settings,
    )

    assert verification_result.outcome is OtpInternalOutcome.OTP_INVALID
    assert created is None
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert anonymous.session.revoked_at is None
    assert db_session.scalar(select(func.count()).select_from(AuthSession)) == 1


@pytest.mark.integration
def test_cross_owner_verified_link_never_rotates_login_session(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    challenge = create_active_challenge(db_session)
    anonymous = create_anonymous_session(db_session, "ua", NOW, settings=settings)
    link = db_session.get(TelegramLink, challenge.telegram_link_id)
    assert link is not None
    other_user = User(phone="+998900009302")
    db_session.add(other_user)
    db_session.flush()
    link.user_id = other_user.id
    db_session.flush()

    verification_result = verify_login_otp(
        db_session,
        settings,
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
    )
    created = rotate_session_after_otp_consume(
        db_session,
        verification_result=verification_result,
        current_session=anonymous.session,
        user_agent="otp login ua",
        now=NOW + timedelta(seconds=3),
        settings=settings,
    )

    assert verification_result.outcome is OtpInternalOutcome.OTP_LINK_CHANGED
    assert created is None
    assert anonymous.session.revoked_at is None
    assert anonymous.session.user_id is None
    assert db_session.scalar(select(func.count()).select_from(AuthSession)) == 1


@pytest.mark.integration
def test_verify_win_then_unlink_preserves_created_session_and_replay_creates_none(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    challenge = create_active_challenge(db_session)
    anonymous = create_anonymous_session(db_session, "ua", NOW, settings=settings)
    user = db_session.get(User, challenge.user_id)
    assert user is not None

    verification_result = verify_login_otp(
        db_session,
        settings,
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
    )
    created = rotate_session_after_otp_consume(
        db_session,
        verification_result=verification_result,
        current_session=anonymous.session,
        user_agent="otp login ua",
        now=NOW + timedelta(seconds=3),
        settings=settings,
    )
    assert created is not None
    created_raw_token = created.raw_token
    db_session.commit()

    replay_result = verify_login_otp(
        db_session,
        settings,
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=4),
    )
    replay_created = rotate_session_after_otp_consume(
        db_session,
        verification_result=replay_result,
        current_session=anonymous.session,
        user_agent="otp login ua",
        now=NOW + timedelta(seconds=5),
        settings=settings,
    )
    unlink_telegram(db_session, user, NOW + timedelta(seconds=6))
    db_session.commit()

    assert replay_result.outcome is OtpInternalOutcome.OTP_INVALID
    assert replay_created is None
    resolved = resolve_by_raw_token(
        db_session,
        created_raw_token,
        NOW + timedelta(seconds=7),
    )
    assert resolved is not None
    assert resolved.authenticated_user is not None
    assert resolved.authenticated_user.id == challenge.user_id
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.user_id == challenge.user_id)
        )
        == 1
    )


@pytest.mark.integration
def test_non_consumed_verification_result_does_not_rotate_session(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    anonymous = create_anonymous_session(db_session, "ua", NOW, settings=settings)

    created = rotate_session_after_otp_consume(
        db_session,
        verification_result=OtpVerificationResult(
            outcome=OtpInternalOutcome.OTP_INVALID
        ),
        current_session=anonymous.session,
        user_agent="otp login ua",
        now=NOW + timedelta(seconds=1),
        settings=settings,
    )

    assert created is None
    assert anonymous.session.revoked_at is None
    assert db_session.scalar(select(func.count()).select_from(AuthSession)) == 1


@pytest.mark.integration
def test_session_rotation_failure_rolls_back_otp_consume_and_session_revoke(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch,
) -> None:
    settings = make_settings(m2_test_database)
    challenge = create_active_challenge(db_session)
    anonymous = create_anonymous_session(db_session, "ua", NOW, settings=settings)
    db_session.flush()
    challenge_id = challenge.id
    anonymous_id = anonymous.session.id
    db_session.commit()

    def fail_after_revoke(
        db: Session,
        current_session: AuthSession | None,
        user_id,
        user_agent,
        now: datetime,
        settings: Settings,
    ):
        assert user_id == challenge.user_id
        assert user_agent == "otp login ua"
        assert settings.otp_hmac_key is not None
        if current_session is not None:
            revoke_session(db, current_session, now)
        raise RuntimeError("session rotation failed")

    monkeypatch.setattr(session_login_module, "rotate_session", fail_after_revoke)

    with pytest.raises(RuntimeError, match="session rotation failed"):
        verification_result = verify_login_otp(
            db_session,
            settings,
            browser_binding_digest=VALID_DIGEST,
            candidate_code_input="123456",
            now=NOW + timedelta(seconds=2),
        )
        rotate_session_after_otp_consume(
            db_session,
            verification_result=verification_result,
            current_session=anonymous.session,
            user_agent="otp login ua",
            now=NOW + timedelta(seconds=3),
            settings=settings,
        )

    db_session.rollback()
    persisted_challenge = db_session.get(OtpChallenge, challenge_id)
    persisted_session = db_session.get(AuthSession, anonymous_id)
    assert persisted_challenge is not None
    assert persisted_challenge.status == OtpChallengeStatus.ACTIVE.value
    assert persisted_challenge.consumed_at is None
    assert persisted_challenge.terminal_at is None
    assert persisted_session is not None
    assert persisted_session.revoked_at is None
    assert db_session.scalar(select(func.count()).select_from(OtpChallengeEvent)) == 0


@pytest.mark.parametrize(
    ("next_url", "expected"),
    [
        (None, "/auth/account"),
        ("", "/auth/account"),
        ("/auth/account", "/auth/account"),
        ("/customer/profile?tab=overview", "/customer/profile?tab=overview"),
        ("customer/profile", "/auth/account"),
        ("//example.test/customer", "/auth/account"),
        ("https://example.test/customer", "/auth/account"),
        ("javascript:alert(1)", "/auth/account"),
    ],
)
def test_otp_success_redirect_reuses_safe_relative_policy(
    next_url: str | None,
    expected: str,
) -> None:
    assert get_otp_success_redirect_target(next_url) == expected


def test_session_login_adapter_has_no_route_shop_or_telegram_scope() -> None:
    source = inspect.getsource(session_login_module).casefold()

    for forbidden in (
        "fastapi",
        "request",
        "active_shop",
        "shop",
        "telegram",
        "send_message",
        "browser_binding",
        ".commit(",
        ".rollback(",
        ".close(",
        "logger",
        "print(",
    ):
        assert forbidden not in source
