import inspect
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.otp.verification as verification_module
from app.auth.models import User
from app.db import create_database_session_factory
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpInternalOutcome,
    OtpPublicOutcome,
    OtpPurpose,
    map_internal_outcome_to_public,
)
from app.otp.crypto import compute_otp_code_mac, verify_otp_code_mac
from app.otp.models import OtpChallenge, OtpChallengeEvent
from app.otp.repository import (
    activate_challenge,
    create_pending_challenge,
    create_pending_dispatch,
    mark_dispatch_prepared,
    mark_dispatch_sent,
)
from app.otp.verification import (
    OtpVerificationCandidateCheck,
    check_login_otp_candidate,
    verify_login_otp,
)
from app.settings import Settings
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)
OTP_HMAC_KEY = "test-otp-verification-hmac-key-at-least-32-chars"
RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-verification"
VALID_DIGEST = "e" * 64
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
        rate_limit_hmac_key=RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=SecretStr(OTP_HMAC_KEY) if with_otp_key else None,
    )


def add_user_and_link(
    session: Session,
    *,
    phone: str = "+998900009201",
) -> tuple[User, TelegramLink]:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=9_982_000_201,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    return user, link


def create_active_challenge(
    session: Session,
    *,
    code: OtpCode = VALID_CODE,
    digest: str = VALID_DIGEST,
    phone: str = "+998900009201",
) -> OtpChallenge:
    user, link = add_user_and_link(session, phone=phone)
    challenge = create_pending_challenge(
        session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=digest,
        now=NOW,
    )
    code_mac = compute_otp_code_mac(
        otp_hmac_key=SecretStr(OTP_HMAC_KEY),
        challenge_id=challenge.id,
        user_id=user.id,
        purpose=OtpPurpose.LOGIN,
        code=code,
    )
    return activate_challenge(
        session,
        challenge=challenge,
        code_mac=code_mac,
        activated_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=3),
    )


def seed_committed_active_challenge(
    engine: Engine,
    *,
    digest: str = VALID_DIGEST,
    phone: str = "+998900009299",
) -> UUID:
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        challenge = create_active_challenge(session, digest=digest, phone=phone)
        return challenge.id


def public_outcome(result: OtpVerificationCandidateCheck) -> OtpPublicOutcome:
    return map_internal_outcome_to_public(result.outcome)


def dummy_recorder(calls: list[str]):
    def record(_key: SecretStr, candidate_code: OtpCode | None) -> None:
        calls.append(
            "<missing>"
            if candidate_code is None
            else candidate_code.as_internal_value()
        )

    return record


@pytest.mark.integration
@pytest.mark.parametrize(
    "candidate_input",
    ["", "12345", "1234567", "123-456", "abcdef", "１２３４５６"],
)
def test_malformed_candidate_runs_dummy_work_and_maps_generic_invalid(
    db_session: Session,
    m2_test_database: Engine,
    candidate_input: str,
) -> None:
    dummy_calls: list[str] = []

    result = check_login_otp_candidate(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input=candidate_input,
        now=NOW,
        dummy_work=dummy_recorder(dummy_calls),
    )

    assert result.outcome is OtpInternalOutcome.OTP_INVALID
    assert public_outcome(result) is OtpPublicOutcome.GENERIC_INVALID
    assert result.accepted_for_consume is False
    assert dummy_calls == ["<missing>"]
    if candidate_input:
        assert candidate_input not in repr(result)


@pytest.mark.integration
def test_missing_challenge_runs_dummy_work_with_valid_candidate_and_same_mapping(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    dummy_calls: list[str] = []

    result = check_login_otp_candidate(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input=" 123456 ",
        now=NOW,
        dummy_work=dummy_recorder(dummy_calls),
    )

    assert result.outcome is OtpInternalOutcome.OTP_INVALID
    assert public_outcome(result) is OtpPublicOutcome.GENERIC_INVALID
    assert dummy_calls == ["123456"]
    assert "123456" not in repr(result)


@pytest.mark.integration
def test_wrong_candidate_compares_mac_runs_neutral_work_and_does_not_mutate_yet(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    dummy_calls: list[str] = []
    mac_calls: list[str] = []

    def mac_verifier(**kwargs) -> bool:
        mac_calls.append(kwargs["code"].as_internal_value())
        return verify_otp_code_mac(**kwargs)

    result = check_login_otp_candidate(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="000000",
        now=NOW + timedelta(seconds=2),
        dummy_work=dummy_recorder(dummy_calls),
        mac_verifier=mac_verifier,
    )

    assert result.outcome is OtpInternalOutcome.OTP_INVALID
    assert public_outcome(result) is OtpPublicOutcome.GENERIC_INVALID
    assert result.challenge is challenge
    assert result.code is not None
    assert result.code.as_internal_value() == "000000"
    assert result.accepted_for_consume is False
    assert mac_calls == ["000000"]
    assert dummy_calls == ["000000"]
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert challenge.failed_attempts == 0
    assert db_session.scalar(select(func.count()).select_from(OtpChallengeEvent)) == 0


@pytest.mark.integration
def test_correct_candidate_returns_consume_ready_without_session_side_effect(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    dummy_calls: list[str] = []

    result = check_login_otp_candidate(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
        dummy_work=dummy_recorder(dummy_calls),
    )

    assert result.outcome is OtpInternalOutcome.OTP_PENDING
    assert result.accepted_for_consume is True
    assert result.challenge is challenge
    assert result.code is not None
    assert result.code.as_internal_value() == "123456"
    assert dummy_calls == []
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OtpChallengeEvent)
            .where(OtpChallengeEvent.action == OtpChallengeEventAction.CONSUMED.value)
        )
        == 0
    )
    assert "123456" not in repr(result)


@pytest.mark.integration
def test_wrong_attempts_increment_then_burn_and_later_correct_code_fails(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    settings = make_settings(m2_test_database)
    outcomes = []

    for attempt in range(5):
        result = verify_login_otp(
            db_session,
            settings,
            browser_binding_digest=VALID_DIGEST,
            candidate_code_input="000000",
            now=NOW + timedelta(seconds=attempt + 2),
        )
        outcomes.append(result.outcome)

    later_correct = verify_login_otp(
        db_session,
        settings,
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=10),
    )

    assert outcomes == [
        OtpInternalOutcome.OTP_INVALID,
        OtpInternalOutcome.OTP_INVALID,
        OtpInternalOutcome.OTP_INVALID,
        OtpInternalOutcome.OTP_INVALID,
        OtpInternalOutcome.OTP_BURNED,
    ]
    assert later_correct.outcome is OtpInternalOutcome.OTP_INVALID
    assert (
        public_outcome(OtpVerificationCandidateCheck(outcome=later_correct.outcome))
        is OtpPublicOutcome.GENERIC_INVALID
    )
    assert challenge.failed_attempts == 5
    assert challenge.status == OtpChallengeStatus.BURNED.value
    events = list(
        db_session.scalars(
            select(OtpChallengeEvent)
            .where(OtpChallengeEvent.challenge_id == challenge.id)
            .order_by(OtpChallengeEvent.occurred_at, OtpChallengeEvent.action)
        ).all()
    )
    assert [event.action for event in events] == [
        OtpChallengeEventAction.VERIFY_FAILED.value,
        OtpChallengeEventAction.VERIFY_FAILED.value,
        OtpChallengeEventAction.VERIFY_FAILED.value,
        OtpChallengeEventAction.VERIFY_FAILED.value,
        OtpChallengeEventAction.BURNED.value,
        OtpChallengeEventAction.VERIFY_FAILED.value,
    ]
    assert [event.safe_code for event in events].count("OTP_INVALID") == 5
    assert [event.safe_code for event in events].count("OTP_BURNED") == 1


@pytest.mark.integration
def test_wrong_attempt_event_failure_rolls_back_attempt_increment(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch,
) -> None:
    challenge = create_active_challenge(db_session)
    challenge_id = challenge.id
    db_session.commit()

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("event insert failed")

    monkeypatch.setattr(verification_module, "append_challenge_event", fail_event)

    with pytest.raises(RuntimeError, match="event insert failed"):
        verify_login_otp(
            db_session,
            make_settings(m2_test_database),
            browser_binding_digest=VALID_DIGEST,
            candidate_code_input="000000",
            now=NOW + timedelta(seconds=2),
        )

    db_session.rollback()
    persisted = db_session.get(OtpChallenge, challenge_id)
    assert persisted is not None
    assert persisted.failed_attempts == 0
    assert persisted.status == OtpChallengeStatus.ACTIVE.value
    assert db_session.scalar(select(func.count()).select_from(OtpChallengeEvent)) == 0


@pytest.mark.integration
def test_parallel_wrong_attempts_are_bounded_by_configured_max(
    m2_test_database: Engine,
) -> None:
    digest = "f" * 64
    challenge_id = seed_committed_active_challenge(
        m2_test_database,
        digest=digest,
        phone="+998900009298",
    )
    session_factory = create_database_session_factory(m2_test_database)
    settings = make_settings(m2_test_database)
    barrier = Barrier(8)

    def attempt_wrong(index: int) -> OtpInternalOutcome:
        with session_factory.begin() as session:
            barrier.wait()
            return verify_login_otp(
                session,
                settings,
                browser_binding_digest=digest,
                candidate_code_input="000000",
                now=NOW + timedelta(seconds=index + 2),
            ).outcome

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt_wrong, range(8)))

    with session_factory() as session:
        challenge = session.get(OtpChallenge, challenge_id)
        assert challenge is not None
        assert challenge.failed_attempts == 5
        assert challenge.status == OtpChallengeStatus.BURNED.value
        event_actions = list(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            ).all()
        )

    assert outcomes.count(OtpInternalOutcome.OTP_BURNED) == 1
    assert set(outcomes).issubset(
        {OtpInternalOutcome.OTP_INVALID, OtpInternalOutcome.OTP_BURNED}
    )
    assert event_actions.count(OtpChallengeEventAction.VERIFY_FAILED.value) == 5
    assert event_actions.count(OtpChallengeEventAction.BURNED.value) == 1


@pytest.mark.integration
def test_verify_revalidates_active_user_and_cancels_open_dispatch(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    dispatch = create_pending_dispatch(
        db_session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    user = db_session.get(User, challenge.user_id)
    assert user is not None
    user.is_active = False

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
    )

    assert result.outcome is OtpInternalOutcome.OTP_LINK_CHANGED
    assert map_internal_outcome_to_public(result.outcome) is (
        OtpPublicOutcome.GENERIC_INVALID
    )
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert dispatch.status == OtpDispatchStatus.CANCELLED.value
    events = list(db_session.scalars(select(OtpChallengeEvent)).all())
    assert [(event.action, event.safe_code) for event in events] == [
        (
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
            "OTP_LINK_CHANGED",
        )
    ]


@pytest.mark.integration
def test_verify_revalidates_unlink_and_preserves_terminal_dispatch(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    dispatch = create_pending_dispatch(
        db_session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    dispatch.claimed_at = NOW + timedelta(seconds=1)
    dispatch.updated_at = dispatch.claimed_at
    db_session.flush()
    mark_dispatch_prepared(
        db_session,
        dispatch=dispatch,
        challenge=challenge,
        now=NOW + timedelta(seconds=1),
    )
    mark_dispatch_sent(
        db_session,
        dispatch=dispatch,
        now=NOW + timedelta(seconds=2),
    )
    link = db_session.get(TelegramLink, challenge.telegram_link_id)
    assert link is not None
    link.telegram_chat_id = None
    link.unlinked_at = NOW + timedelta(seconds=3)
    link.phone_verified_at = None
    link.updated_at = link.unlinked_at

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=4),
    )

    assert result.outcome is OtpInternalOutcome.OTP_LINK_CHANGED
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert dispatch.status == OtpDispatchStatus.SENT.value
    assert dispatch.sent_at == NOW + timedelta(seconds=2)


@pytest.mark.integration
def test_verify_revalidates_relink_generation_snapshot(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    link = db_session.get(TelegramLink, challenge.telegram_link_id)
    assert link is not None
    link.linked_at = NOW + timedelta(seconds=30)
    link.phone_verified_at = link.linked_at
    link.updated_at = link.linked_at

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=31),
    )

    assert result.outcome is OtpInternalOutcome.OTP_LINK_CHANGED
    assert map_internal_outcome_to_public(result.outcome) is (
        OtpPublicOutcome.GENERIC_INVALID
    )
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.terminal_at == NOW + timedelta(seconds=31)


@pytest.mark.integration
def test_verify_rejects_legacy_unverified_link_before_mac_or_attempt(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    link = db_session.get(TelegramLink, challenge.telegram_link_id)
    assert link is not None
    link.phone_verified_at = None
    db_session.flush()
    mac_calls: list[str] = []

    def mac_verifier(**kwargs) -> bool:
        mac_calls.append(kwargs["code"].as_internal_value())
        return verify_otp_code_mac(**kwargs)

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
        mac_verifier=mac_verifier,
    )

    assert result.outcome is OtpInternalOutcome.OTP_LINK_CHANGED
    assert map_internal_outcome_to_public(result.outcome) is (
        OtpPublicOutcome.GENERIC_INVALID
    )
    assert mac_calls == []
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.failed_attempts == 0
    assert challenge.consumed_at is None
    assert [
        (event.action, event.safe_code)
        for event in db_session.scalars(
            select(OtpChallengeEvent).where(
                OtpChallengeEvent.challenge_id == challenge.id
            )
        )
    ] == [
        (
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
            "OTP_LINK_CHANGED",
        )
    ]


@pytest.mark.integration
def test_verify_rejects_cross_owner_verified_link_before_mac_or_attempt(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    link = db_session.get(TelegramLink, challenge.telegram_link_id)
    assert link is not None
    other_user = User(phone="+998900009202")
    db_session.add(other_user)
    db_session.flush()
    link.user_id = other_user.id
    db_session.flush()
    mac_calls: list[str] = []

    def mac_verifier(**kwargs) -> bool:
        mac_calls.append(kwargs["code"].as_internal_value())
        return verify_otp_code_mac(**kwargs)

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
        mac_verifier=mac_verifier,
    )

    assert result.outcome is OtpInternalOutcome.OTP_LINK_CHANGED
    assert map_internal_outcome_to_public(result.outcome) is (
        OtpPublicOutcome.GENERIC_INVALID
    )
    assert mac_calls == []
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.failed_attempts == 0
    assert challenge.consumed_at is None
    assert [
        (event.action, event.safe_code)
        for event in db_session.scalars(
            select(OtpChallengeEvent).where(
                OtpChallengeEvent.challenge_id == challenge.id
            )
        )
    ] == [
        (
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
            "OTP_LINK_CHANGED",
        )
    ]


@pytest.mark.integration
def test_expired_active_candidate_is_terminalized_before_mac_success(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(minutes=4),
    )

    assert result.outcome is OtpInternalOutcome.OTP_EXPIRED
    assert map_internal_outcome_to_public(result.outcome) is (
        OtpPublicOutcome.GENERIC_INVALID
    )
    assert challenge.status == OtpChallengeStatus.EXPIRED.value
    assert challenge.terminal_at == NOW + timedelta(minutes=4)
    events = list(db_session.scalars(select(OtpChallengeEvent)).all())
    assert [(event.action, event.safe_code) for event in events] == [
        (OtpChallengeEventAction.EXPIRED.value, "OTP_EXPIRED")
    ]


@pytest.mark.integration
def test_active_candidate_at_attempt_cap_is_burned_before_mac_success(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)
    challenge.failed_attempts = 5
    db_session.flush()

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
    )

    assert result.outcome is OtpInternalOutcome.OTP_BURNED
    assert map_internal_outcome_to_public(result.outcome) is (
        OtpPublicOutcome.GENERIC_INVALID
    )
    assert challenge.status == OtpChallengeStatus.BURNED.value
    assert challenge.failed_attempts == 5
    events = list(db_session.scalars(select(OtpChallengeEvent)).all())
    assert [(event.action, event.safe_code) for event in events] == [
        (OtpChallengeEventAction.BURNED.value, "OTP_BURNED")
    ]


@pytest.mark.integration
def test_correct_code_consumes_once_without_attempt_increment(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    challenge = create_active_challenge(db_session)

    result = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=2),
    )
    replay = verify_login_otp(
        db_session,
        make_settings(m2_test_database),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW + timedelta(seconds=3),
    )

    assert result.outcome is OtpInternalOutcome.OTP_CONSUMED
    assert result.consumed is True
    assert result.user_id == challenge.user_id
    assert replay.outcome is OtpInternalOutcome.OTP_INVALID
    assert map_internal_outcome_to_public(replay.outcome) is (
        OtpPublicOutcome.GENERIC_INVALID
    )
    assert challenge.status == OtpChallengeStatus.CONSUMED.value
    assert challenge.failed_attempts == 0
    assert challenge.consumed_at == NOW + timedelta(seconds=2)
    assert challenge.terminal_at == NOW + timedelta(seconds=2)
    events = list(db_session.scalars(select(OtpChallengeEvent)).all())
    assert [(event.action, event.safe_code) for event in events] == [
        (OtpChallengeEventAction.CONSUMED.value, None)
    ]


@pytest.mark.integration
def test_consume_event_failure_rolls_back_consumed_transition(
    db_session: Session,
    m2_test_database: Engine,
    monkeypatch,
) -> None:
    challenge = create_active_challenge(db_session)
    challenge_id = challenge.id
    db_session.commit()

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("consume event failed")

    monkeypatch.setattr(verification_module, "append_challenge_event", fail_event)

    with pytest.raises(RuntimeError, match="consume event failed"):
        verify_login_otp(
            db_session,
            make_settings(m2_test_database),
            browser_binding_digest=VALID_DIGEST,
            candidate_code_input="123456",
            now=NOW + timedelta(seconds=2),
        )

    db_session.rollback()
    persisted = db_session.get(OtpChallenge, challenge_id)
    assert persisted is not None
    assert persisted.status == OtpChallengeStatus.ACTIVE.value
    assert persisted.consumed_at is None
    assert persisted.terminal_at is None
    assert db_session.scalar(select(func.count()).select_from(OtpChallengeEvent)) == 0


@pytest.mark.integration
def test_parallel_correct_verification_has_exactly_one_winner(
    m2_test_database: Engine,
) -> None:
    digest = "1" * 64
    challenge_id = seed_committed_active_challenge(
        m2_test_database,
        digest=digest,
        phone="+998900009297",
    )
    session_factory = create_database_session_factory(m2_test_database)
    settings = make_settings(m2_test_database)
    barrier = Barrier(6)

    def attempt_correct(index: int) -> OtpInternalOutcome:
        with session_factory.begin() as session:
            barrier.wait()
            return verify_login_otp(
                session,
                settings,
                browser_binding_digest=digest,
                candidate_code_input="123456",
                now=NOW + timedelta(seconds=index + 2),
            ).outcome

    with ThreadPoolExecutor(max_workers=6) as executor:
        outcomes = list(executor.map(attempt_correct, range(6)))

    with session_factory() as session:
        challenge = session.get(OtpChallenge, challenge_id)
        assert challenge is not None
        assert challenge.status == OtpChallengeStatus.CONSUMED.value
        assert challenge.failed_attempts == 0
        event_actions = list(
            session.scalars(
                select(OtpChallengeEvent.action).where(
                    OtpChallengeEvent.challenge_id == challenge_id
                )
            ).all()
        )

    assert outcomes.count(OtpInternalOutcome.OTP_CONSUMED) == 1
    assert outcomes.count(OtpInternalOutcome.OTP_INVALID) == 5
    assert event_actions == [OtpChallengeEventAction.CONSUMED.value]


@pytest.mark.integration
def test_missing_otp_key_degrades_without_mutation_or_dummy_secret_work(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    dummy_calls: list[str] = []

    result = check_login_otp_candidate(
        db_session,
        make_settings(m2_test_database, with_otp_key=False),
        browser_binding_digest=VALID_DIGEST,
        candidate_code_input="123456",
        now=NOW,
        dummy_work=dummy_recorder(dummy_calls),
    )

    assert result.outcome is OtpInternalOutcome.OTP_CONFIGURATION_UNAVAILABLE
    assert public_outcome(result) is OtpPublicOutcome.GENERIC_ACCEPTED
    assert dummy_calls == []


def test_verification_precheck_boundary_has_no_route_rate_limit_or_logging_scope() -> (
    None
):
    source = inspect.getsource(verification_module).casefold()

    for forbidden in (
        "fastapi",
        "request",
        "csrf",
        "rate_limit",
        "logger",
        "logging",
        "print(",
        ".commit(",
        ".rollback(",
        ".close(",
        "telegram_bot_token",
        "send_message",
    ):
        assert forbidden not in source
