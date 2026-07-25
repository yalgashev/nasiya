import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from inspect import signature
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.service as telegram_service
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    ConsumedTelegramStartToken,
    TelegramChatAlreadyLinkedError,
    TelegramLinkTokenConsumeError,
    TelegramStartTokenConsumeOutcome,
    consume_start_token,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token


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


def add_tombstone_link(
    session: Session,
    user: User,
    *,
    linked_at: datetime,
    unlinked_at: datetime,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=None,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        updated_at=unlinked_at,
    )
    session.add(link)
    session.flush()
    return link


def add_active_link(
    session: Session,
    user: User,
    *,
    telegram_chat_id: int,
    linked_at: datetime,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        updated_at=linked_at,
    )
    session.add(link)
    session.flush()
    return link


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def stored_token_text(session: Session) -> str:
    rows = session.execute(
        text(
            "SELECT token_hash, created_at::text, expires_at::text, "
            "consumed_at::text, invalidated_at::text FROM telegram_link_tokens"
        )
    ).all()
    return "|".join(str(value) for row in rows for value in row)


def stored_link_and_event_text(session: Session) -> str:
    queries = (
        (
            "telegram_links",
            "SELECT telegram_chat_id::text, linked_at::text, "
            "unlinked_at::text, updated_at::text FROM telegram_links",
        ),
        (
            "telegram_link_events",
            "SELECT action, occurred_at::text FROM telegram_link_events",
        ),
    )
    values: list[str] = []
    for table_name, query in queries:
        for row in session.execute(text(query)).all():
            values.append(table_name)
            values.extend(str(value) for value in row)
    return "|".join(values)


def seed_committed_user_and_token(
    engine: Engine,
    *,
    phone: str,
    raw_token: str,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = add_user(session, phone)
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=created_at,
            expires_at=expires_at,
        )
        user_id = user.id
        token_id = token.id
        session.commit()
        return user_id, token_id
    finally:
        session.close()


def seed_committed_user_link_and_token(
    engine: Engine,
    *,
    phone: str,
    telegram_chat_id: int,
    raw_token: str,
    linked_at: datetime,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[UUID, UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = add_user(session, phone)
        link = add_active_link(
            session,
            user,
            telegram_chat_id=telegram_chat_id,
            linked_at=linked_at,
        )
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=created_at,
            expires_at=expires_at,
        )
        user_id = user.id
        link_id = link.id
        token_id = token.id
        session.commit()
        return user_id, link_id, token_id
    finally:
        session.close()


def test_consume_start_token_public_api_has_no_external_user_or_payload() -> None:
    parameters = signature(consume_start_token).parameters

    assert list(parameters) == ["session", "raw_token", "chat_identity", "now"]
    assert "user_id" not in parameters
    assert "current_user" not in parameters
    assert "chat_id" not in parameters
    assert "telegram_chat_id" not in parameters
    assert "request" not in parameters
    assert "payload" not in parameters
    assert "update_json" not in parameters


@pytest.mark.integration
def test_consume_start_token_first_link_creates_state_consumes_token_and_event(
    caplog,
    m2_test_database: Engine,
) -> None:
    raw_token = "first_successful_link_token"
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    issued_at = datetime(2026, 7, 24, 18, 45, tzinfo=UTC)
    now = issued_at + timedelta(minutes=2)
    user_id, token_id = seed_committed_user_and_token(
        m2_test_database,
        phone="+998900011001",
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        with caplog.at_level(logging.INFO):
            result = consume_start_token(
                first_session,
                RawTelegramLinkToken(raw_token),
                VerifiedPrivateTelegramChatIdentity(11_001),
                now,
            )
        token_in_transaction = first_session.get(TelegramLinkToken, token_id)

        assert isinstance(result, ConsumedTelegramStartToken)
        assert result.token is token_in_transaction
        assert result.token.id == token_id
        assert result.token.user_id == user_id
        assert result.token.token_hash == token_hash
        assert result.token.consumed_at == now
        assert result.token.invalidated_at is None
        assert result.link.user_id == user_id
        assert result.link.telegram_chat_id == 11_001
        assert result.link.linked_at == now
        assert result.link.updated_at == now
        assert result.link.unlinked_at is None
        assert result.event.user_id == user_id
        assert result.event.action == "linked"
        assert result.event.occurred_at == now
        assert result.outcome is TelegramStartTokenConsumeOutcome.LINKED
        assert count_table(first_session, TelegramLink) == 1
        assert count_table(first_session, TelegramLinkEvent) == 1
        assert count_table(first_session, Customer) == 0
        assert count_table(second_session, TelegramLink) == 0
        assert count_table(second_session, TelegramLinkEvent) == 0
        assert second_session.get(TelegramLinkToken, token_id).consumed_at is None
        assert raw_token not in stored_token_text(first_session)
        assert raw_token not in repr(result)
        assert raw_token not in caplog.text
        assert "11001" not in caplog.text

        first_session.commit()
    finally:
        first_session.close()
        second_session.close()

    verify_session = session_factory()
    try:
        stored_token = verify_session.get(TelegramLinkToken, token_id)
        stored_link = verify_session.scalar(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        )
        stored_events = verify_session.scalars(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        ).all()

        assert stored_token is not None
        assert stored_token.consumed_at == now
        assert stored_link is not None
        assert stored_link.telegram_chat_id == 11_001
        assert stored_link.linked_at == now
        assert stored_link.updated_at == now
        assert stored_link.unlinked_at is None
        assert len(stored_events) == 1
        assert stored_events[0].action == "linked"
        assert stored_events[0].occurred_at == now
        assert count_table(verify_session, Customer) == 0
    finally:
        verify_session.rollback()
        verify_session.close()


@pytest.mark.integration
def test_consume_start_token_first_link_reactivates_tombstone_row(
    db_session: Session,
) -> None:
    raw_token = "first_link_tombstone_token"
    issued_at = datetime(2026, 7, 24, 18, 50, tzinfo=UTC)
    unlinked_at = issued_at - timedelta(minutes=5)
    now = issued_at + timedelta(minutes=1)
    user = add_user(db_session, "+998900011002")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    tombstone = add_tombstone_link(
        db_session,
        user,
        linked_at=issued_at - timedelta(minutes=10),
        unlinked_at=unlinked_at,
    )

    result = consume_start_token(
        db_session,
        RawTelegramLinkToken(raw_token),
        VerifiedPrivateTelegramChatIdentity(11_002),
        now,
    )
    events = db_session.scalars(select(TelegramLinkEvent)).all()

    assert result.token is token
    assert result.link is tombstone
    assert token.consumed_at == now
    assert tombstone.telegram_chat_id == 11_002
    assert tombstone.linked_at == now
    assert tombstone.updated_at == now
    assert tombstone.unlinked_at is None
    assert len(events) == 1
    assert events[0] is result.event
    assert events[0].action == "linked"
    assert events[0].occurred_at == now
    assert result.outcome is TelegramStartTokenConsumeOutcome.LINKED
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, Customer) == 0


@pytest.mark.integration
def test_consume_start_token_relinks_active_chat_consumes_token_and_event(
    caplog,
    m2_test_database: Engine,
) -> None:
    raw_token = "successful_relink_token"
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    chat_a = 98_765_431
    chat_b = 98_765_432
    linked_at = datetime(2026, 7, 24, 19, 0, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=3)
    now = issued_at + timedelta(minutes=2)
    user_id, link_id, token_id = seed_committed_user_link_and_token(
        m2_test_database,
        phone="+998900011003",
        telegram_chat_id=chat_a,
        raw_token=raw_token,
        linked_at=linked_at,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        with caplog.at_level(logging.INFO):
            result = consume_start_token(
                first_session,
                RawTelegramLinkToken(raw_token),
                VerifiedPrivateTelegramChatIdentity(chat_b),
                now,
            )
        token_in_transaction = first_session.get(TelegramLinkToken, token_id)
        link_in_transaction = first_session.get(TelegramLink, link_id)
        old_chat_count_in_transaction = first_session.scalar(
            select(func.count())
            .select_from(TelegramLink)
            .where(TelegramLink.telegram_chat_id == chat_a)
        )

        assert isinstance(result, ConsumedTelegramStartToken)
        assert result.token is token_in_transaction
        assert result.link is link_in_transaction
        assert result.token.id == token_id
        assert result.token.user_id == user_id
        assert result.token.token_hash == token_hash
        assert result.token.consumed_at == now
        assert result.token.invalidated_at is None
        assert result.link.id == link_id
        assert result.link.user_id == user_id
        assert result.link.telegram_chat_id == chat_b
        assert result.link.linked_at == now
        assert result.link.updated_at == now
        assert result.link.unlinked_at is None
        assert result.event.user_id == user_id
        assert result.event.action == "relinked"
        assert result.event.occurred_at == now
        assert result.outcome is TelegramStartTokenConsumeOutcome.RELINKED
        assert old_chat_count_in_transaction == 0
        assert count_table(first_session, TelegramLink) == 1
        assert count_table(first_session, TelegramLinkEvent) == 1
        assert count_table(first_session, Customer) == 0
        assert str(chat_a) not in stored_link_and_event_text(first_session)
        assert raw_token not in stored_token_text(first_session)
        assert raw_token not in repr(result)
        assert raw_token not in caplog.text
        assert str(chat_a) not in caplog.text
        assert str(chat_b) not in caplog.text

        second_session_token = second_session.get(TelegramLinkToken, token_id)
        second_session_link = second_session.get(TelegramLink, link_id)
        assert second_session_token is not None
        assert second_session_token.consumed_at is None
        assert second_session_link is not None
        assert second_session_link.telegram_chat_id == chat_a
        assert count_table(second_session, TelegramLinkEvent) == 0

        first_session.commit()
    finally:
        first_session.close()
        second_session.close()

    verify_session = session_factory()
    try:
        stored_token = verify_session.get(TelegramLinkToken, token_id)
        stored_link = verify_session.get(TelegramLink, link_id)
        stored_events = verify_session.scalars(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        ).all()
        old_chat_count = verify_session.scalar(
            select(func.count())
            .select_from(TelegramLink)
            .where(TelegramLink.telegram_chat_id == chat_a)
        )

        assert stored_token is not None
        assert stored_token.consumed_at == now
        assert stored_link is not None
        assert stored_link.telegram_chat_id == chat_b
        assert stored_link.linked_at == now
        assert stored_link.updated_at == now
        assert stored_link.unlinked_at is None
        assert old_chat_count == 0
        assert len(stored_events) == 1
        assert stored_events[0].action == "relinked"
        assert stored_events[0].occurred_at == now
        assert str(chat_a) not in stored_link_and_event_text(verify_session)
        assert count_table(verify_session, Customer) == 0
    finally:
        verify_session.rollback()
        verify_session.close()


@pytest.mark.integration
def test_consume_start_token_same_chat_relink_consumes_token_without_event(
    caplog,
    m2_test_database: Engine,
) -> None:
    raw_token = "same_chat_relink_token"
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    chat_id = 98_765_433
    linked_at = datetime(2026, 7, 24, 19, 20, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=2)
    now = issued_at + timedelta(minutes=1)
    user_id, link_id, token_id = seed_committed_user_link_and_token(
        m2_test_database,
        phone="+998900011004",
        telegram_chat_id=chat_id,
        raw_token=raw_token,
        linked_at=linked_at,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    try:
        with caplog.at_level(logging.INFO):
            result = consume_start_token(
                first_session,
                RawTelegramLinkToken(raw_token),
                VerifiedPrivateTelegramChatIdentity(chat_id),
                now,
            )
        token_in_transaction = first_session.get(TelegramLinkToken, token_id)
        link_in_transaction = first_session.get(TelegramLink, link_id)

        assert isinstance(result, ConsumedTelegramStartToken)
        assert result.outcome is (
            TelegramStartTokenConsumeOutcome.ALREADY_LINKED_TO_THIS_CHAT
        )
        assert result.token is token_in_transaction
        assert result.link is link_in_transaction
        assert result.event is None
        assert result.token.id == token_id
        assert result.token.user_id == user_id
        assert result.token.token_hash == token_hash
        assert result.token.consumed_at == now
        assert result.token.invalidated_at is None
        assert result.link.id == link_id
        assert result.link.user_id == user_id
        assert result.link.telegram_chat_id == chat_id
        assert result.link.linked_at == linked_at
        assert result.link.updated_at == linked_at
        assert result.link.unlinked_at is None
        assert count_table(first_session, TelegramLink) == 1
        assert count_table(first_session, TelegramLinkEvent) == 0
        assert count_table(first_session, Customer) == 0
        assert raw_token not in stored_token_text(first_session)
        assert raw_token not in repr(result)
        assert raw_token not in caplog.text
        assert str(chat_id) not in caplog.text

        first_session.commit()
    finally:
        first_session.close()

    verify_session = session_factory()
    try:
        stored_token = verify_session.get(TelegramLinkToken, token_id)
        stored_link = verify_session.get(TelegramLink, link_id)

        assert stored_token is not None
        assert stored_token.consumed_at == now
        assert stored_link is not None
        assert stored_link.telegram_chat_id == chat_id
        assert stored_link.linked_at == linked_at
        assert stored_link.updated_at == linked_at
        assert stored_link.unlinked_at is None
        assert count_table(verify_session, TelegramLinkEvent) == 0

        with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
            consume_start_token(
                verify_session,
                RawTelegramLinkToken(raw_token),
                VerifiedPrivateTelegramChatIdentity(chat_id),
                now + timedelta(seconds=1),
            )
        continuation_user = add_user(verify_session, "+998900011005")

        assert exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
        assert exc_info.value.public_error["code"] == "LINK_TOKEN_INVALID"
        assert continuation_user.id is not None
        assert count_table(verify_session, TelegramLinkEvent) == 0
        assert raw_token not in str(exc_info.value)
        assert raw_token not in repr(exc_info.value)
        assert str(chat_id) not in str(exc_info.value)
        assert str(chat_id) not in repr(exc_info.value)
    finally:
        verify_session.rollback()
        verify_session.close()


@pytest.mark.integration
def test_consume_start_token_first_link_chat_collision_preserves_token_for_retry(
    db_session: Session,
) -> None:
    raw_token = "first_link_collision_token"
    linked_at = datetime(2026, 7, 24, 19, 40, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=2)
    collision_at = issued_at + timedelta(minutes=1)
    retry_at = collision_at + timedelta(seconds=1)
    chat_x = 98_765_434
    retry_chat = 98_765_435
    user_a = add_user(db_session, "+998900011006")
    user_b = add_user(db_session, "+998900011007")
    user_a_link = add_active_link(
        db_session,
        user_a,
        telegram_chat_id=chat_x,
        linked_at=linked_at,
    )
    token = add_token(
        db_session,
        user_b,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    original_user_a_state = (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    )
    original_token_state = (
        token.consumed_at,
        token.invalidated_at,
        token.expires_at,
    )

    with pytest.raises(TelegramChatAlreadyLinkedError) as exc_info:
        consume_start_token(
            db_session,
            RawTelegramLinkToken(raw_token),
            VerifiedPrivateTelegramChatIdentity(chat_x),
            collision_at,
        )
    db_session.refresh(user_a_link)
    db_session.refresh(token)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
    continuation_user = add_user(db_session, "+998900011008")

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    assert exc_info.value.public_error == {
        "code": "TELEGRAM_CHAT_ALREADY_LINKED",
        "message": "Bu Telegram chat allaqachon bog'langan.",
    }
    assert (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    ) == original_user_a_state
    assert (token.consumed_at, token.invalidated_at, token.expires_at) == (
        original_token_state
    )
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert continuation_user.id is not None
    assert raw_token not in error_text
    assert user_a.phone not in error_text
    assert str(user_a.id) not in error_text
    assert str(chat_x) not in error_text

    retry_result = consume_start_token(
        db_session,
        RawTelegramLinkToken(raw_token),
        VerifiedPrivateTelegramChatIdentity(retry_chat),
        retry_at,
    )
    db_session.refresh(user_a_link)
    db_session.refresh(token)
    user_b_link = db_session.scalar(
        select(TelegramLink).where(TelegramLink.user_id == user_b.id)
    )
    events = db_session.scalars(
        select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_b.id)
    ).all()

    assert retry_result.outcome is TelegramStartTokenConsumeOutcome.LINKED
    assert retry_result.token is token
    assert retry_result.link is user_b_link
    assert retry_result.event is events[0]
    assert token.consumed_at == retry_at
    assert token.invalidated_at is None
    assert (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    ) == original_user_a_state
    assert user_b_link is not None
    assert user_b_link.telegram_chat_id == retry_chat
    assert len(events) == 1
    assert events[0].action == "linked"


@pytest.mark.integration
def test_consume_start_token_relink_chat_collision_preserves_link_and_retry_token(
    db_session: Session,
) -> None:
    raw_token = "relink_collision_token"
    linked_at = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=2)
    collision_at = issued_at + timedelta(minutes=1)
    retry_at = collision_at + timedelta(seconds=1)
    chat_x = 98_765_436
    user_b_old_chat = 98_765_437
    retry_chat = 98_765_438
    user_a = add_user(db_session, "+998900011009")
    user_b = add_user(db_session, "+998900011010")
    user_a_link = add_active_link(
        db_session,
        user_a,
        telegram_chat_id=chat_x,
        linked_at=linked_at,
    )
    user_b_link = add_active_link(
        db_session,
        user_b,
        telegram_chat_id=user_b_old_chat,
        linked_at=linked_at + timedelta(minutes=1),
    )
    token = add_token(
        db_session,
        user_b,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    original_user_a_state = (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    )
    original_user_b_state = (
        user_b_link.telegram_chat_id,
        user_b_link.linked_at,
        user_b_link.unlinked_at,
        user_b_link.updated_at,
    )
    original_token_state = (
        token.consumed_at,
        token.invalidated_at,
        token.expires_at,
    )

    with pytest.raises(TelegramChatAlreadyLinkedError) as exc_info:
        consume_start_token(
            db_session,
            RawTelegramLinkToken(raw_token),
            VerifiedPrivateTelegramChatIdentity(chat_x),
            collision_at,
        )
    db_session.refresh(user_a_link)
    db_session.refresh(user_b_link)
    db_session.refresh(token)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
    continuation_user = add_user(db_session, "+998900011011")

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    assert (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    ) == original_user_a_state
    assert (
        user_b_link.telegram_chat_id,
        user_b_link.linked_at,
        user_b_link.unlinked_at,
        user_b_link.updated_at,
    ) == original_user_b_state
    assert (token.consumed_at, token.invalidated_at, token.expires_at) == (
        original_token_state
    )
    assert count_table(db_session, TelegramLink) == 2
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert continuation_user.id is not None
    assert raw_token not in error_text
    assert user_a.phone not in error_text
    assert str(user_a.id) not in error_text
    assert str(chat_x) not in error_text

    retry_result = consume_start_token(
        db_session,
        RawTelegramLinkToken(raw_token),
        VerifiedPrivateTelegramChatIdentity(retry_chat),
        retry_at,
    )
    db_session.refresh(user_a_link)
    db_session.refresh(user_b_link)
    db_session.refresh(token)
    events = db_session.scalars(
        select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_b.id)
    ).all()

    assert retry_result.outcome is TelegramStartTokenConsumeOutcome.RELINKED
    assert retry_result.token is token
    assert retry_result.link is user_b_link
    assert retry_result.event is events[0]
    assert token.consumed_at == retry_at
    assert token.invalidated_at is None
    assert (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    ) == original_user_a_state
    assert user_b_link.telegram_chat_id == retry_chat
    assert user_b_link.linked_at == retry_at
    assert user_b_link.updated_at == retry_at
    assert user_b_link.unlinked_at is None
    assert len(events) == 1
    assert events[0].action == "relinked"


@pytest.mark.integration
def test_consume_start_token_chat_unique_conflict_uses_savepoint(
    monkeypatch,
    db_session: Session,
) -> None:
    raw_token = "chat_unique_conflict_token"
    linked_at = datetime(2026, 7, 24, 20, 20, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=2)
    collision_at = issued_at + timedelta(minutes=1)
    chat_x = 98_765_439
    user_a = add_user(db_session, "+998900011012")
    user_b = add_user(db_session, "+998900011013")
    user_a_link = add_active_link(
        db_session,
        user_a,
        telegram_chat_id=chat_x,
        linked_at=linked_at,
    )
    token = add_token(
        db_session,
        user_b,
        raw_token=raw_token,
        created_at=issued_at,
        expires_at=issued_at + timedelta(minutes=10),
    )
    original_user_a_state = (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    )

    monkeypatch.setattr(
        telegram_service,
        "get_other_active_telegram_link_by_chat_identity_for_update",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(TelegramChatAlreadyLinkedError) as exc_info:
        consume_start_token(
            db_session,
            RawTelegramLinkToken(raw_token),
            VerifiedPrivateTelegramChatIdentity(chat_x),
            collision_at,
        )
    db_session.refresh(user_a_link)
    db_session.refresh(token)
    continuation_user = add_user(db_session, "+998900011014")

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    assert (
        user_a_link.telegram_chat_id,
        user_a_link.linked_at,
        user_a_link.unlinked_at,
        user_a_link.updated_at,
    ) == original_user_a_state
    assert token.consumed_at is None
    assert token.invalidated_at is None
    assert continuation_user.id is not None
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
