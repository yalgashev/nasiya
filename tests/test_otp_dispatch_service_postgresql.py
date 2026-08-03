from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.otp.code import OtpCode
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDeliveryFailureCode,
    OtpDispatchStatus,
    OtpPurpose,
)
from app.otp.crypto import compute_otp_code_mac
from app.otp.dispatch_service import (
    prepare_next_otp_dispatch,
    record_otp_delivery_result,
    recover_stale_prepared_dispatches,
)
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.provider import OtpDeliverySendResult, OtpDeliverySendStatus
from app.otp.repository import (
    create_pending_challenge,
)
from app.otp.repository import (
    create_pending_dispatch as repository_create_pending_dispatch,
)
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 29, 13, 0, tzinfo=UTC)
OTP_HMAC_KEY = SecretStr("test-otp-dispatcher-hmac-key-at-least-32-chars")
VALID_DIGEST = "c" * 64


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def add_user(
    session: Session,
    phone: str = "+998900009001",
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
    linked_at: datetime = NOW,
    telegram_chat_id: int = 9_980_900_001,
    unlinked_at: datetime | None = None,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        phone_verified_at=linked_at if unlinked_at is None else None,
        updated_at=unlinked_at or linked_at,
    )
    session.add(link)
    session.flush()
    return link


def create_pending_challenge_and_dispatch(
    session: Session,
    *,
    phone: str = "+998900009001",
    digest: str = VALID_DIGEST,
) -> tuple[User, TelegramLink, OtpChallenge, OtpDispatch]:
    user = add_user(session, phone)
    link = add_link(session, user)
    challenge = create_pending_challenge(
        session,
        user_id=user.id,
        telegram_link_id=link.id,
        telegram_linked_at=link.linked_at,
        browser_binding_digest=digest,
        now=NOW,
    )
    dispatch = repository_create_pending_dispatch(
        session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    return user, link, challenge, dispatch


def event_rows(session: Session, challenge: OtpChallenge) -> list[OtpChallengeEvent]:
    return list(
        session.scalars(
            select(OtpChallengeEvent)
            .where(OtpChallengeEvent.challenge_id == challenge.id)
            .order_by(OtpChallengeEvent.occurred_at, OtpChallengeEvent.action)
        ).all()
    )


@pytest.mark.integration
def test_tx_d1_claims_activates_prepares_and_keeps_raw_code_out_of_db(
    db_session: Session,
) -> None:
    user, _link, challenge, dispatch = create_pending_challenge_and_dispatch(db_session)

    prepared = prepare_next_otp_dispatch(
        db_session,
        otp_hmac_key=OTP_HMAC_KEY,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=180,
        claim_stale_seconds=60,
        code_generator=lambda _upper: 4271,
    )

    assert prepared is not None
    assert prepared.dispatch_id == dispatch.id
    assert prepared.challenge_id == challenge.id
    assert prepared.code.as_internal_value() == "004271"
    assert prepared.target.chat_identity.as_bigint() == 9_980_900_001
    assert "004271" not in repr(prepared)
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert challenge.failed_attempts == 0
    assert challenge.activated_at == NOW + timedelta(seconds=1)
    assert challenge.expires_at == NOW + timedelta(seconds=181)
    assert (
        challenge.code_mac
        == compute_otp_code_mac(
            otp_hmac_key=OTP_HMAC_KEY,
            challenge_id=challenge.id,
            user_id=user.id,
            purpose=OtpPurpose.LOGIN,
            code=OtpCode("004271"),
        ).as_stored_value()
    )
    assert dispatch.status == OtpDispatchStatus.PREPARED.value
    assert dispatch.claimed_at == NOW + timedelta(seconds=1)
    assert dispatch.prepared_at == NOW + timedelta(seconds=1)

    events = event_rows(db_session, challenge)
    assert [event.action for event in events] == [
        OtpChallengeEventAction.DISPATCH_PREPARED.value
    ]
    rendered_db_values = " ".join(
        str(value)
        for value in (
            db_session.scalar(select(OtpChallenge.code_mac)),
            db_session.scalar(select(OtpDispatch.failure_code)),
            db_session.scalar(select(OtpChallengeEvent.safe_code)),
        )
        if value is not None
    )
    assert "004271" not in rendered_db_values


@pytest.mark.integration
def test_tx_d1_invalid_link_cancels_dispatch_without_send_envelope(
    db_session: Session,
) -> None:
    _user, link, challenge, dispatch = create_pending_challenge_and_dispatch(db_session)
    link.unlinked_at = NOW + timedelta(seconds=1)
    link.telegram_chat_id = None
    link.phone_verified_at = None
    link.updated_at = link.unlinked_at

    prepared = prepare_next_otp_dispatch(
        db_session,
        otp_hmac_key=OTP_HMAC_KEY,
        now=NOW + timedelta(seconds=2),
        ttl_seconds=180,
        claim_stale_seconds=60,
        code_generator=lambda _upper: 999999,
    )

    assert prepared is None
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.code_mac is None
    assert dispatch.status == OtpDispatchStatus.CANCELLED.value
    events = event_rows(db_session, challenge)
    assert [(event.action, event.safe_code) for event in events] == [
        (
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
            "OTP_LINK_CHANGED",
        )
    ]


@pytest.mark.integration
def test_tx_d1_legacy_unverified_link_cancels_before_code_generation(
    db_session: Session,
) -> None:
    _user, link, challenge, dispatch = create_pending_challenge_and_dispatch(db_session)
    link.phone_verified_at = None
    db_session.flush()
    generated: list[int] = []

    prepared = prepare_next_otp_dispatch(
        db_session,
        otp_hmac_key=OTP_HMAC_KEY,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=180,
        claim_stale_seconds=60,
        code_generator=lambda upper: generated.append(upper) or 999999,
    )

    assert prepared is None
    assert generated == []
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.failed_attempts == 0
    assert challenge.code_mac is None
    assert dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert [
        (event.action, event.safe_code) for event in event_rows(db_session, challenge)
    ] == [
        (
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
            "OTP_LINK_CHANGED",
        )
    ]


@pytest.mark.integration
def test_tx_d1_cross_owner_verified_link_cancels_before_code_generation(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900009002")
    other_user = add_user(db_session, "+998900009003")
    other_link = add_link(
        db_session,
        other_user,
        telegram_chat_id=9_980_900_003,
    )
    challenge = create_pending_challenge(
        db_session,
        user_id=user.id,
        telegram_link_id=other_link.id,
        telegram_linked_at=other_link.linked_at,
        browser_binding_digest="d" * 64,
        now=NOW,
    )
    dispatch = repository_create_pending_dispatch(
        db_session,
        challenge_id=challenge.id,
        locale="uz-Latn",
        now=NOW,
    )
    generated: list[int] = []

    prepared = prepare_next_otp_dispatch(
        db_session,
        otp_hmac_key=OTP_HMAC_KEY,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=180,
        claim_stale_seconds=60,
        code_generator=lambda upper: generated.append(upper) or 999999,
    )

    assert prepared is None
    assert generated == []
    assert challenge.status == OtpChallengeStatus.INVALIDATED.value
    assert challenge.failed_attempts == 0
    assert challenge.code_mac is None
    assert dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert [
        (event.action, event.safe_code) for event in event_rows(db_session, challenge)
    ] == [
        (
            OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
            "OTP_LINK_CHANGED",
        )
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("send_result", "expected_status", "expected_failure", "expected_safe_code"),
    [
        (
            OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT),
            OtpDispatchStatus.SENT,
            None,
            "OTP_SENT",
        ),
        (
            OtpDeliverySendResult(
                status=OtpDeliverySendStatus.FAILED,
                failure_code=OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER,
            ),
            OtpDispatchStatus.FAILED,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER.value,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_SERVER.value,
        ),
        (
            OtpDeliverySendResult(
                status=OtpDeliverySendStatus.UNKNOWN,
                failure_code=OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_NETWORK,
            ),
            OtpDispatchStatus.UNKNOWN,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_NETWORK.value,
            OtpDeliveryFailureCode.TELEGRAM_TRANSIENT_NETWORK.value,
        ),
    ],
)
def test_tx_d2_records_delivery_result_in_fresh_transition(
    db_session: Session,
    send_result: OtpDeliverySendResult,
    expected_status: OtpDispatchStatus,
    expected_failure: str | None,
    expected_safe_code: str,
) -> None:
    _user, _link, challenge, dispatch = create_pending_challenge_and_dispatch(
        db_session
    )
    prepared = prepare_next_otp_dispatch(
        db_session,
        otp_hmac_key=OTP_HMAC_KEY,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=180,
        claim_stale_seconds=60,
        code_generator=lambda _upper: 123456,
    )
    assert prepared is not None

    assert record_otp_delivery_result(
        db_session,
        dispatch_id=prepared.dispatch_id,
        result=send_result,
        now=NOW + timedelta(seconds=2),
    )

    assert dispatch.status == expected_status.value
    assert dispatch.terminal_at == NOW + timedelta(seconds=2)
    assert dispatch.failure_code == expected_failure
    events = event_rows(db_session, challenge)
    assert [event.action for event in events] == [
        OtpChallengeEventAction.DISPATCH_PREPARED.value,
        OtpChallengeEventAction.DISPATCH_RESULT.value,
    ]
    assert events[-1].safe_code == expected_safe_code


@pytest.mark.integration
def test_stale_prepared_recovery_marks_unknown_once_and_never_resends(
    db_session: Session,
) -> None:
    _user, _link, challenge, dispatch = create_pending_challenge_and_dispatch(
        db_session
    )
    assert prepare_next_otp_dispatch(
        db_session,
        otp_hmac_key=OTP_HMAC_KEY,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=180,
        claim_stale_seconds=60,
        code_generator=lambda _upper: 654321,
    )

    recovered_count = recover_stale_prepared_dispatches(
        db_session,
        now=NOW + timedelta(seconds=62),
        stale_seconds=60,
        limit=20,
    )
    recovered_again = recover_stale_prepared_dispatches(
        db_session,
        now=NOW + timedelta(seconds=63),
        stale_seconds=60,
        limit=20,
    )

    assert recovered_count == 1
    assert recovered_again == 0
    assert challenge.status == OtpChallengeStatus.ACTIVE.value
    assert dispatch.status == OtpDispatchStatus.UNKNOWN.value
    assert dispatch.failure_code == "OTP_DISPATCH_STALE_PREPARED"
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(OtpDispatch)
            .where(OtpDispatch.status == OtpDispatchStatus.PREPARED.value)
        )
        == 0
    )
    assert [
        (event.action, event.safe_code) for event in event_rows(db_session, challenge)
    ] == [
        (OtpChallengeEventAction.DISPATCH_PREPARED.value, None),
        (
            OtpChallengeEventAction.DISPATCH_RESULT.value,
            "OTP_DISPATCH_STALE_PREPARED",
        ),
    ]
