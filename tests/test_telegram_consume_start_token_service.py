import hmac
import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from inspect import signature

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.shop.models  # noqa: F401
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.db import create_database_session_factory
from app.otp.contracts import (
    OtpChallengeEventAction,
    OtpChallengeStatus,
    OtpDispatchStatus,
    OtpPurpose,
)
from app.otp.models import OtpChallenge, OtpChallengeEvent, OtpDispatch
from app.otp.repository import (
    create_pending_challenge,
    create_pending_dispatch,
    create_pending_registration_challenge,
)
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    ConsumedTelegramStartToken,
    PendingTelegramContactBinding,
    TelegramChatAlreadyLinkedError,
    TelegramContactVerificationError,
    TelegramLinkOutcome,
    TelegramLinkTokenConsumeError,
    bind_start_token_for_contact,
    consume_start_token,
)
from app.telegram.token import (
    RawTelegramLinkToken,
    derive_telegram_contact_binding_mac,
    hash_telegram_link_token,
)
from tests.m11_seed import NOW as SNAPSHOT_NOW
from tests.m11_seed import seed_registration_snapshot

CONTACT_BINDING_KEY = SecretStr(
    "test-contact-binding-rate-limit-key-at-least-32-characters"
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


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def add_token(
    session: Session,
    user: User,
    *,
    raw_token: str,
    created_at: datetime,
    expires_at: datetime,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token)),
        created_at=created_at,
        expires_at=expires_at,
    )
    session.add(token)
    session.flush()
    return token


def add_active_link(
    session: Session,
    user: User,
    *,
    telegram_chat_id: int,
    linked_at: datetime,
    phone_verified: bool = True,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        phone_verified_at=linked_at if phone_verified else None,
        updated_at=linked_at,
    )
    session.add(link)
    session.flush()
    return link


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _bind_contact(
    session: Session,
    *,
    token: TelegramLinkToken,
    raw_token: str,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    sender_identity: TelegramUserIdentity,
    now: datetime,
) -> str:
    result = bind_start_token_for_contact(
        session,
        RawTelegramLinkToken(raw_token),
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now,
    )
    session.refresh(token)
    assert isinstance(result, PendingTelegramContactBinding)
    assert token.pending_contact_binding_mac is not None
    assert token.contact_requested_at == now
    return token.pending_contact_binding_mac


def _consume_contact(
    session: Session,
    *,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    sender_identity: TelegramUserIdentity,
    contact_identity: TelegramUserIdentity | None = None,
    contact_phone: str,
    now: datetime,
) -> ConsumedTelegramStartToken:
    return consume_start_token(
        session,
        chat_identity,
        sender_identity,
        contact_identity or sender_identity,
        SensitiveTelegramContactPhone(contact_phone),
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now,
    )


def _assert_pending_mac_unchanged(
    token: TelegramLinkToken,
    expected_mac: str,
) -> None:
    if token.pending_contact_binding_mac is None or not hmac.compare_digest(
        token.pending_contact_binding_mac,
        expected_mac,
    ):
        pytest.fail("pending contact binding changed", pytrace=False)


def _assert_zero_contact_domain_side_effects(session: Session) -> None:
    assert count_table(session, TelegramLink) == 0
    assert count_table(session, TelegramLinkEvent) == 0
    assert count_table(session, OtpChallenge) == 0
    assert count_table(session, OtpDispatch) == 0
    assert count_table(session, OtpChallengeEvent) == 0
    assert count_table(session, Customer) == 0
    assert session.scalar(text("SELECT count(*) FROM sessions")) == 0
    assert session.scalar(text("SELECT count(*) FROM audit_log")) == 0
    assert session.scalar(text("SELECT count(*) FROM auth_rate_limits")) == 0


def _seed_both_pending_otp_purposes(
    session: Session,
    *,
    snapshot,
    now: datetime,
) -> tuple[OtpChallenge, OtpDispatch, OtpChallenge, OtpDispatch]:
    registration = create_pending_registration_challenge(
        session,
        snapshot=snapshot,
        now=now,
    )
    registration_dispatch = create_pending_dispatch(
        session,
        challenge_id=registration.id,
        locale="uz-Latn",
        now=now,
    )
    login = create_pending_challenge(
        session,
        browser_binding_digest="b" * 64,
        now=now,
        purpose=OtpPurpose.LOGIN,
        user_id=snapshot.user_id,
        telegram_link_id=snapshot.telegram_link_id,
        telegram_linked_at=snapshot.telegram_linked_at,
    )
    login_dispatch = create_pending_dispatch(
        session,
        challenge_id=login.id,
        locale="ru",
        now=now,
    )
    return registration, registration_dispatch, login, login_dispatch


def test_consume_start_token_public_api_has_only_typed_contact_authority() -> None:
    parameters = signature(consume_start_token).parameters

    assert list(parameters) == [
        "session",
        "chat_identity",
        "sender_identity",
        "contact_identity",
        "contact_phone",
        "rate_limit_hmac_key",
        "now",
    ]
    for forbidden in (
        "raw_token",
        "user_id",
        "current_user",
        "chat_id",
        "telegram_chat_id",
        "request",
        "payload",
        "update_json",
    ):
        assert forbidden not in parameters


@pytest.mark.integration
def test_matching_self_contact_consumes_bound_token_and_creates_verified_link(
    caplog,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    raw_token = "matching_self_contact_token"
    stored_phone = "+998900011121"
    submitted_phone = "+998 (90) 001-11-21"
    issued_at = datetime(2026, 8, 2, 19, 0, tzinfo=UTC)
    bound_at = issued_at + timedelta(seconds=1)
    verified_at = issued_at + timedelta(seconds=2)
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_121)
    sender_identity = TelegramUserIdentity(19_121)

    with session_factory.begin() as session:
        user = add_user(session, stored_phone)
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=issued_at,
            expires_at=issued_at + timedelta(minutes=10),
        )
        user_id = user.id
        token_id = token.id
        _bind_contact(
            session,
            token=token,
            raw_token=raw_token,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            now=bound_at,
        )

    with session_factory.begin() as session, caplog.at_level(logging.INFO):
        result = _consume_contact(
            session,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            contact_phone=submitted_phone,
            now=verified_at,
        )

        assert result.outcome is TelegramLinkOutcome.LINKED
        assert result.token.id == token_id
        assert result.token.user_id == user_id
        assert result.token.consumed_at == verified_at
        assert result.token.invalidated_at is None
        assert result.token.pending_contact_binding_mac is None
        assert result.token.contact_requested_at is None
        assert result.link.user_id == user_id
        assert result.link.telegram_chat_id == chat_identity.as_bigint()
        assert result.link.linked_at == verified_at
        assert result.link.phone_verified_at == verified_at
        assert result.link.updated_at == verified_at
        assert result.link.unlinked_at is None
        assert result.event is not None
        assert result.event.action == "linked"
        assert result.event.occurred_at == verified_at
        assert count_table(session, TelegramLink) == 1
        assert count_table(session, TelegramLinkEvent) == 1
        assert count_table(session, Customer) == 0
        rendered = f"{result!r} {caplog.text}"
        for forbidden in (
            raw_token,
            submitted_phone,
            str(chat_identity.as_bigint()),
        ):
            assert forbidden not in rendered

    with session_factory() as session:
        token = session.get(TelegramLinkToken, token_id)
        link = session.scalar(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        )
        assert token is not None
        assert token.consumed_at == verified_at
        assert token.pending_contact_binding_mac is None
        assert token.contact_requested_at is None
        assert link is not None
        assert link.linked_at == link.phone_verified_at == verified_at
        assert count_table(session, TelegramLinkEvent) == 1


@pytest.mark.integration
def test_contact_phone_mismatch_is_generic_and_zero_write(
    caplog,
    db_session: Session,
) -> None:
    raw_token = "mismatching_contact_token"
    mismatch_phone = "+998 (90) 999-88-77"
    issued_at = datetime(2026, 8, 2, 19, 10, tzinfo=UTC)
    bound_at = issued_at + timedelta(seconds=1)
    user = add_user(db_session, "+998900011122")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_122)
    sender_identity = TelegramUserIdentity(19_122)
    pending_mac = _bind_contact(
        db_session,
        token=token,
        raw_token=raw_token,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        now=bound_at,
    )

    with (
        caplog.at_level(logging.INFO),
        pytest.raises(TelegramContactVerificationError) as exc_info,
    ):
        _consume_contact(
            db_session,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            contact_phone=mismatch_phone,
            now=bound_at + timedelta(seconds=1),
        )
    db_session.refresh(token)

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_PHONE_MISMATCH
    _assert_pending_mac_unchanged(token, pending_mac)
    assert token.contact_requested_at == bound_at
    assert token.consumed_at is None
    assert token.invalidated_at is None
    _assert_zero_contact_domain_side_effects(db_session)
    rendered = f"{exc_info.value!r} {exc_info.value} {caplog.text}"
    for forbidden in (
        mismatch_phone,
        raw_token,
        str(chat_identity.as_bigint()),
        pending_mac,
        CONTACT_BINDING_KEY.get_secret_value(),
    ):
        assert forbidden not in rendered


@pytest.mark.integration
@pytest.mark.parametrize("terminal_state", ("expired", "consumed", "invalidated"))
def test_expired_consumed_or_invalidated_bound_token_is_rejected(
    db_session: Session,
    terminal_state: str,
) -> None:
    raw_token = f"contact_lifecycle_{terminal_state}"
    now = datetime(2026, 8, 2, 19, 20, tzinfo=UTC)
    issued_at = now - timedelta(minutes=2)
    user = add_user(
        db_session,
        {
            "expired": "+998900011123",
            "consumed": "+998900011124",
            "invalidated": "+998900011125",
        }[terminal_state],
    )
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=(now if terminal_state == "expired" else now + timedelta(minutes=8)),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_123)
    sender_identity = TelegramUserIdentity(19_123)
    pending_mac = _bind_contact(
        db_session,
        token=token,
        raw_token=raw_token,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        now=issued_at + timedelta(seconds=1),
    )
    if terminal_state != "expired":
        token.pending_contact_binding_mac = None
        token.contact_requested_at = None
        if terminal_state == "consumed":
            token.consumed_at = now - timedelta(seconds=1)
        else:
            token.invalidated_at = now - timedelta(seconds=1)
        db_session.flush()

    with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
        _consume_contact(
            db_session,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            contact_phone=user.phone,
            now=now,
        )
    db_session.refresh(token)

    assert exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    if terminal_state == "expired":
        _assert_pending_mac_unchanged(token, pending_mac)
        assert token.contact_requested_at == issued_at + timedelta(seconds=1)
    else:
        assert token.pending_contact_binding_mac is None
        assert token.contact_requested_at is None
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, OtpChallengeEvent) == 0


@pytest.mark.integration
def test_verified_contact_token_replay_is_rejected(db_session: Session) -> None:
    raw_token = "verified_contact_replay"
    issued_at = datetime(2026, 8, 2, 19, 30, tzinfo=UTC)
    verified_at = issued_at + timedelta(seconds=2)
    user = add_user(db_session, "+998900011126")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_126)
    sender_identity = TelegramUserIdentity(19_126)
    _bind_contact(
        db_session,
        token=token,
        raw_token=raw_token,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        now=issued_at + timedelta(seconds=1),
    )
    first = _consume_contact(
        db_session,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        contact_phone=user.phone,
        now=verified_at,
    )
    link_state = (
        first.link.telegram_chat_id,
        first.link.linked_at,
        first.link.phone_verified_at,
        first.link.updated_at,
    )

    with pytest.raises(TelegramLinkTokenConsumeError):
        _consume_contact(
            db_session,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            contact_phone=user.phone,
            now=verified_at + timedelta(seconds=1),
        )
    db_session.refresh(token)
    db_session.refresh(first.link)

    assert token.consumed_at == verified_at
    assert token.pending_contact_binding_mac is None
    assert token.contact_requested_at is None
    if (
        first.link.telegram_chat_id,
        first.link.linked_at,
        first.link.phone_verified_at,
        first.link.updated_at,
    ) != link_state:
        pytest.fail("verified link changed on contact replay", pytrace=False)
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 1
    assert count_table(db_session, OtpChallengeEvent) == 0


@pytest.mark.integration
@pytest.mark.parametrize("authority_change", ("chat", "sender", "contact"))
def test_bound_token_rejects_other_chat_or_sender(
    db_session: Session,
    authority_change: str,
) -> None:
    raw_token = f"bound_contact_authority_{authority_change}"
    issued_at = datetime(2026, 8, 2, 19, 40, tzinfo=UTC)
    user = add_user(db_session, "+998900011127")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    bound_chat = VerifiedPrivateTelegramChatIdentity(19_127)
    bound_sender = TelegramUserIdentity(19_127)
    pending_mac = _bind_contact(
        db_session,
        token=token,
        raw_token=raw_token,
        chat_identity=bound_chat,
        sender_identity=bound_sender,
        now=issued_at + timedelta(seconds=1),
    )
    submitted_chat = (
        VerifiedPrivateTelegramChatIdentity(19_128)
        if authority_change == "chat"
        else bound_chat
    )
    submitted_sender = (
        TelegramUserIdentity(19_128) if authority_change == "sender" else bound_sender
    )
    submitted_contact = (
        TelegramUserIdentity(19_129)
        if authority_change == "contact"
        else submitted_sender
    )

    with pytest.raises(
        (TelegramLinkTokenConsumeError, TelegramContactVerificationError)
    ):
        _consume_contact(
            db_session,
            chat_identity=submitted_chat,
            sender_identity=submitted_sender,
            contact_identity=submitted_contact,
            contact_phone=user.phone,
            now=issued_at + timedelta(seconds=2),
        )
    db_session.refresh(token)

    _assert_pending_mac_unchanged(token, pending_mac)
    assert token.contact_requested_at == issued_at + timedelta(seconds=1)
    assert token.consumed_at is None
    assert token.invalidated_at is None
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_contact_chat_collision_preserves_token_and_existing_link(
    db_session: Session,
) -> None:
    raw_token = "verified_contact_chat_collision"
    linked_at = datetime(2026, 8, 2, 19, 50, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=1)
    collision_chat = 19_130
    collision_owner = add_user(db_session, "+998900011130")
    target_user = add_user(db_session, "+998900011131")
    existing_link = add_active_link(
        db_session,
        collision_owner,
        telegram_chat_id=collision_chat,
        linked_at=linked_at,
    )
    token = add_token(
        db_session,
        target_user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(collision_chat)
    sender_identity = TelegramUserIdentity(collision_chat)
    pending_mac = _bind_contact(
        db_session,
        token=token,
        raw_token=raw_token,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        now=issued_at + timedelta(seconds=1),
    )
    link_state = (
        existing_link.telegram_chat_id,
        existing_link.linked_at,
        existing_link.phone_verified_at,
        existing_link.unlinked_at,
        existing_link.updated_at,
    )

    with pytest.raises(TelegramChatAlreadyLinkedError) as exc_info:
        _consume_contact(
            db_session,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            contact_phone=target_user.phone,
            now=issued_at + timedelta(seconds=2),
        )
    db_session.refresh(token)
    db_session.refresh(existing_link)

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    _assert_pending_mac_unchanged(token, pending_mac)
    assert token.consumed_at is None
    assert token.invalidated_at is None
    if (
        existing_link.telegram_chat_id,
        existing_link.linked_at,
        existing_link.phone_verified_at,
        existing_link.unlinked_at,
        existing_link.updated_at,
    ) != link_state:
        pytest.fail("collision owner link changed", pytrace=False)
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, OtpChallengeEvent) == 0


@pytest.mark.integration
def test_mismatching_relink_preserves_old_verified_generation(
    db_session: Session,
) -> None:
    snapshot = seed_registration_snapshot(
        db_session,
        phone="+998900011132",
    )
    user = db_session.get(User, snapshot.user_id)
    customer = db_session.get(Customer, snapshot.customer_id)
    link = db_session.get(TelegramLink, snapshot.telegram_link_id)
    assert user is not None
    assert customer is not None
    assert link is not None
    link.phone_verified_at = link.linked_at
    customer.onboarding_status = CUSTOMER_ONBOARDING_STATUS_ACTIVE
    customer.activated_at = SNAPSHOT_NOW + timedelta(minutes=1)
    customer.updated_at = customer.activated_at
    registration, registration_dispatch, login, login_dispatch = (
        _seed_both_pending_otp_purposes(
            db_session,
            snapshot=snapshot,
            now=SNAPSHOT_NOW,
        )
    )
    auth_session = AuthSession(
        user_id=user.id,
        token_hash="e" * 64,
        csrf_secret="synthetic-session-csrf-secret",
        created_at=SNAPSHOT_NOW,
        last_seen_at=SNAPSHOT_NOW,
        expires_at=SNAPSHOT_NOW + timedelta(hours=1),
    )
    db_session.add(auth_session)
    issued_at = SNAPSHOT_NOW + timedelta(minutes=2)
    raw_token = "mismatching_protected_relink"
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_132)
    sender_identity = TelegramUserIdentity(19_132)
    pending_mac = _bind_contact(
        db_session,
        token=token,
        raw_token=raw_token,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        now=issued_at + timedelta(seconds=1),
    )
    db_session.flush()
    link_state = (
        link.telegram_chat_id,
        link.linked_at,
        link.phone_verified_at,
        link.unlinked_at,
        link.updated_at,
    )
    customer_state = (
        customer.onboarding_status,
        customer.activated_at,
        customer.updated_at,
    )
    session_state = (
        auth_session.last_seen_at,
        auth_session.expires_at,
        auth_session.revoked_at,
    )

    with pytest.raises(TelegramContactVerificationError):
        _consume_contact(
            db_session,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
            contact_phone="+998900019999",
            now=issued_at + timedelta(seconds=2),
        )
    for row in (
        token,
        link,
        customer,
        registration,
        registration_dispatch,
        login,
        login_dispatch,
        auth_session,
    ):
        db_session.refresh(row)

    _assert_pending_mac_unchanged(token, pending_mac)
    assert token.contact_requested_at == issued_at + timedelta(seconds=1)
    assert token.consumed_at is None
    assert token.invalidated_at is None
    if (
        link.telegram_chat_id,
        link.linked_at,
        link.phone_verified_at,
        link.unlinked_at,
        link.updated_at,
    ) != link_state:
        pytest.fail("verified link changed on mismatching relink", pytrace=False)
    if (
        customer.onboarding_status,
        customer.activated_at,
        customer.updated_at,
    ) != customer_state:
        pytest.fail("customer changed on mismatching relink", pytrace=False)
    assert registration.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert login.status == OtpChallengeStatus.PENDING_DISPATCH.value
    assert registration_dispatch.status == OtpDispatchStatus.PENDING.value
    assert login_dispatch.status == OtpDispatchStatus.PENDING.value
    if (
        auth_session.last_seen_at,
        auth_session.expires_at,
        auth_session.revoked_at,
    ) != session_state:
        pytest.fail("session changed on mismatching relink", pytrace=False)
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, OtpChallengeEvent) == 0
    assert db_session.scalar(text("SELECT count(*) FROM audit_log")) == 0
    assert db_session.scalar(text("SELECT count(*) FROM auth_rate_limits")) == 0


@pytest.mark.integration
def test_same_phone_reverify_rotates_generation_and_invalidates_both_purposes(
    db_session: Session,
) -> None:
    snapshot = seed_registration_snapshot(
        db_session,
        phone="+998900011133",
    )
    user = db_session.get(User, snapshot.user_id)
    customer = db_session.get(Customer, snapshot.customer_id)
    link = db_session.get(TelegramLink, snapshot.telegram_link_id)
    assert user is not None
    assert customer is not None
    assert link is not None
    old_linked_at = link.linked_at
    link.phone_verified_at = old_linked_at
    customer.onboarding_status = CUSTOMER_ONBOARDING_STATUS_ACTIVE
    customer.activated_at = SNAPSHOT_NOW + timedelta(minutes=1)
    customer.updated_at = customer.activated_at
    registration, registration_dispatch, login, login_dispatch = (
        _seed_both_pending_otp_purposes(
            db_session,
            snapshot=snapshot,
            now=SNAPSHOT_NOW,
        )
    )
    issued_at = SNAPSHOT_NOW + timedelta(minutes=2)
    verified_at = issued_at + timedelta(seconds=2)
    raw_token = "same_phone_protected_reverify"
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_133)
    sender_identity = TelegramUserIdentity(19_133)
    _bind_contact(
        db_session,
        token=token,
        raw_token=raw_token,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        now=issued_at + timedelta(seconds=1),
    )

    result = _consume_contact(
        db_session,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
        contact_phone=user.phone,
        now=verified_at,
    )
    for row in (
        token,
        link,
        customer,
        registration,
        registration_dispatch,
        login,
        login_dispatch,
    ):
        db_session.refresh(row)

    assert result.outcome is TelegramLinkOutcome.RELINKED
    assert token.consumed_at == verified_at
    assert token.pending_contact_binding_mac is None
    assert token.contact_requested_at is None
    assert link.linked_at == link.phone_verified_at == verified_at
    assert link.linked_at != old_linked_at
    assert link.telegram_chat_id == chat_identity.as_bigint()
    assert link.unlinked_at is None
    assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_ACTIVE
    assert registration.status == OtpChallengeStatus.INVALIDATED.value
    assert login.status == OtpChallengeStatus.INVALIDATED.value
    assert registration_dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert login_dispatch.status == OtpDispatchStatus.CANCELLED.value
    assert tuple(
        db_session.scalars(
            select(OtpChallengeEvent.action).order_by(OtpChallengeEvent.id)
        ).all()
    ) == (
        OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
        OtpChallengeEventAction.INVALIDATED_BY_LINK_CHANGE.value,
    )
    assert tuple(
        db_session.scalars(
            select(TelegramLinkEvent.action).order_by(TelegramLinkEvent.id)
        ).all()
    ) == ("relinked",)


def _assert_contact_binding_domain_is_empty(session: Session) -> None:
    assert count_table(session, TelegramLink) == 0
    assert count_table(session, TelegramLinkEvent) == 0
    assert count_table(session, Customer) == 0
    for table_name in ("otp_challenges", "otp_dispatches", "sessions", "audit_log"):
        count = session.scalar(text(f"SELECT count(*) FROM {table_name}"))
        assert count == 0


@pytest.mark.integration
def test_start_binding_writes_only_pending_state_and_replay_is_idempotent(
    db_session: Session,
) -> None:
    raw_token = "pending_contact_happy_path"
    issued_at = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
    requested_at = issued_at + timedelta(minutes=1)
    user = add_user(db_session, "+998900011101")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_101)
    sender_identity = TelegramUserIdentity(29_101)
    expected_mac = derive_telegram_contact_binding_mac(
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
    )

    result = bind_start_token_for_contact(
        db_session,
        RawTelegramLinkToken(raw_token),
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=requested_at,
    )
    db_session.refresh(token)

    assert isinstance(result, PendingTelegramContactBinding)
    assert token.pending_contact_binding_mac is not None
    if not hmac.compare_digest(
        token.pending_contact_binding_mac,
        expected_mac.as_stored_value(),
    ):
        pytest.fail("pending contact binding MAC mismatch", pytrace=False)
    assert token.contact_requested_at == requested_at
    assert token.consumed_at is None
    assert token.invalidated_at is None
    assert raw_token not in repr(result)
    assert str(chat_identity.as_bigint()) not in repr(result)
    assert str(sender_identity.as_bigint()) not in repr(result)
    _assert_contact_binding_domain_is_empty(db_session)

    replay_result = bind_start_token_for_contact(
        db_session,
        RawTelegramLinkToken(raw_token),
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=requested_at + timedelta(minutes=1),
    )
    db_session.refresh(token)

    assert isinstance(replay_result, PendingTelegramContactBinding)
    assert token.contact_requested_at == requested_at
    assert token.consumed_at is None
    assert token.invalidated_at is None
    _assert_contact_binding_domain_is_empty(db_session)


@pytest.mark.integration
@pytest.mark.parametrize("terminal_state", ("expired", "consumed", "invalidated"))
def test_start_binding_rejects_expired_consumed_or_invalidated_token_zero_write(
    db_session: Session,
    terminal_state: str,
) -> None:
    raw_token = f"pending_contact_{terminal_state}"
    now = datetime(2026, 8, 2, 18, 20, tzinfo=UTC)
    user = add_user(
        db_session,
        {
            "expired": "+998900011102",
            "consumed": "+998900011103",
            "invalidated": "+998900011104",
        }[terminal_state],
    )
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=now - timedelta(minutes=2),
        expires_at=(now if terminal_state == "expired" else now + timedelta(minutes=8)),
    )
    if terminal_state == "consumed":
        token.consumed_at = now - timedelta(seconds=1)
    if terminal_state == "invalidated":
        token.invalidated_at = now - timedelta(seconds=1)
    db_session.flush()
    before = (
        token.consumed_at,
        token.invalidated_at,
        token.pending_contact_binding_mac,
        token.contact_requested_at,
    )

    with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
        bind_start_token_for_contact(
            db_session,
            RawTelegramLinkToken(raw_token),
            VerifiedPrivateTelegramChatIdentity(19_102),
            TelegramUserIdentity(29_102),
            rate_limit_hmac_key=CONTACT_BINDING_KEY,
            now=now,
        )
    db_session.refresh(token)

    assert exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert (
        token.consumed_at,
        token.invalidated_at,
        token.pending_contact_binding_mac,
        token.contact_requested_at,
    ) == before
    _assert_contact_binding_domain_is_empty(db_session)


@pytest.mark.integration
def test_start_binding_rejects_same_token_for_different_identity_zero_write(
    db_session: Session,
) -> None:
    raw_token = "pending_contact_identity_rebind"
    now = datetime(2026, 8, 2, 18, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900011105")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    bind_start_token_for_contact(
        db_session,
        RawTelegramLinkToken(raw_token),
        VerifiedPrivateTelegramChatIdentity(19_103),
        TelegramUserIdentity(29_103),
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now + timedelta(seconds=1),
    )
    db_session.refresh(token)
    before = (
        token.pending_contact_binding_mac,
        token.contact_requested_at,
        token.consumed_at,
        token.invalidated_at,
    )

    with pytest.raises(TelegramLinkTokenConsumeError):
        bind_start_token_for_contact(
            db_session,
            RawTelegramLinkToken(raw_token),
            VerifiedPrivateTelegramChatIdentity(19_104),
            TelegramUserIdentity(29_104),
            rate_limit_hmac_key=CONTACT_BINDING_KEY,
            now=now + timedelta(seconds=2),
        )
    db_session.refresh(token)

    assert (
        token.pending_contact_binding_mac,
        token.contact_requested_at,
        token.consumed_at,
        token.invalidated_at,
    ) == before
    _assert_contact_binding_domain_is_empty(db_session)


@pytest.mark.integration
def test_start_binding_invalidates_prior_pending_token_for_same_identity_first(
    db_session: Session,
) -> None:
    first_raw_token = "pending_contact_first_token"
    second_raw_token = "pending_contact_second_token"
    now = datetime(2026, 8, 2, 18, 40, tzinfo=UTC)
    first_user = add_user(db_session, "+998900011106")
    second_user = add_user(db_session, "+998900011107")
    first = add_token(
        db_session,
        first_user,
        raw_token=first_raw_token,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    second = add_token(
        db_session,
        second_user,
        raw_token=second_raw_token,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    chat_identity = VerifiedPrivateTelegramChatIdentity(19_105)
    sender_identity = TelegramUserIdentity(29_105)
    bind_start_token_for_contact(
        db_session,
        RawTelegramLinkToken(first_raw_token),
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now + timedelta(seconds=1),
    )

    bind_start_token_for_contact(
        db_session,
        RawTelegramLinkToken(second_raw_token),
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now + timedelta(seconds=2),
    )
    db_session.refresh(first)
    db_session.refresh(second)

    assert first.invalidated_at == now + timedelta(seconds=2)
    assert first.pending_contact_binding_mac is None
    assert first.contact_requested_at is None
    assert second.invalidated_at is None
    assert second.pending_contact_binding_mac is not None
    assert second.contact_requested_at == now + timedelta(seconds=2)
    _assert_contact_binding_domain_is_empty(db_session)
