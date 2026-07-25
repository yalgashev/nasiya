import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramLinkTokenConsumeError,
    TelegramStartTokenConsumeOutcome,
    consume_start_token,
)
from app.telegram.service import (
    unlink as unlink_telegram,
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


def add_token(
    session: Session,
    user: User,
    *,
    raw_token: RawTelegramLinkToken,
    created_at: datetime,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(raw_token),
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=10),
    )
    session.add(token)
    session.flush()
    return token


def link_snapshot(link: TelegramLink) -> tuple:
    return (
        link.id,
        link.user_id,
        link.telegram_chat_id,
        link.linked_at,
        link.unlinked_at,
        link.updated_at,
    )


def event_snapshot(
    session: Session,
    user: User,
) -> list[tuple]:
    events = session.scalars(
        select(TelegramLinkEvent)
        .where(TelegramLinkEvent.user_id == user.id)
        .order_by(TelegramLinkEvent.occurred_at, TelegramLinkEvent.id)
    ).all()
    return [
        (event.id, event.user_id, event.action, event.occurred_at)
        for event in events
    ]


def assert_invalid_replay_is_safe(
    session: Session,
    *,
    raw_token: RawTelegramLinkToken,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    now: datetime,
) -> TelegramLinkTokenConsumeError:
    with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
        consume_start_token(session, raw_token, chat_identity, now)

    error = exc_info.value
    assert error.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert error.public_error["code"] == "LINK_TOKEN_INVALID"
    assert raw_token.as_internal_value() not in str(error)
    assert raw_token.as_internal_value() not in repr(error)
    assert str(chat_identity.as_bigint()) not in str(error)
    assert str(chat_identity.as_bigint()) not in repr(error)
    assert session.scalar(select(1)) == 1
    return error


@pytest.mark.integration
def test_first_link_token_sequential_replay_is_exactly_once(
    caplog,
    db_session: Session,
) -> None:
    issued_at = datetime(2026, 7, 25, 11, 0, tzinfo=UTC)
    consumed_at = issued_at + timedelta(minutes=1)
    raw_token = RawTelegramLinkToken("sequential_first_link_token")
    chat_identity = VerifiedPrivateTelegramChatIdentity(17_001)
    user = add_user(db_session, "+998900017001")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
    )

    with caplog.at_level(logging.DEBUG):
        first_result = consume_start_token(
            db_session,
            raw_token,
            chat_identity,
            consumed_at,
        )
        link_after_first = link_snapshot(first_result.link)
        events_after_first = event_snapshot(db_session, user)
        error = assert_invalid_replay_is_safe(
            db_session,
            raw_token=raw_token,
            chat_identity=chat_identity,
            now=consumed_at + timedelta(seconds=1),
        )

    db_session.refresh(token)
    db_session.refresh(first_result.link)

    assert first_result.outcome is TelegramStartTokenConsumeOutcome.LINKED
    assert token.consumed_at == consumed_at
    assert link_snapshot(first_result.link) == link_after_first
    assert first_result.link.telegram_chat_id == chat_identity.as_bigint()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(TelegramLink)
            .where(TelegramLink.user_id == user.id)
        )
        == 1
    )
    assert len(events_after_first) == 1
    assert events_after_first[0][2:] == ("linked", consumed_at)
    assert event_snapshot(db_session, user) == events_after_first
    assert raw_token.as_internal_value() not in caplog.text
    assert raw_token.as_internal_value() not in f"{error!r} {error}"


@pytest.mark.parametrize(
    ("target_chat_id", "expected_outcome", "expected_event_actions"),
    [
        (17_003, TelegramStartTokenConsumeOutcome.RELINKED, ["relinked"]),
        (
            17_002,
            TelegramStartTokenConsumeOutcome.ALREADY_LINKED_TO_THIS_CHAT,
            [],
        ),
    ],
    ids=["different-chat", "same-chat"],
)
@pytest.mark.integration
def test_relink_token_sequential_replay_is_exactly_once(
    caplog,
    db_session: Session,
    target_chat_id: int,
    expected_outcome: TelegramStartTokenConsumeOutcome,
    expected_event_actions: list[str],
) -> None:
    linked_at = datetime(2026, 7, 25, 11, 10, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=1)
    consumed_at = issued_at + timedelta(minutes=1)
    raw_token = RawTelegramLinkToken(
        f"sequential_relink_{expected_outcome.value.lower()}_token"
    )
    target_identity = VerifiedPrivateTelegramChatIdentity(target_chat_id)
    user = add_user(db_session, "+998900017002")
    link = add_active_link(
        db_session,
        user,
        telegram_chat_id=17_002,
        linked_at=linked_at,
    )
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
    )

    with caplog.at_level(logging.DEBUG):
        first_result = consume_start_token(
            db_session,
            raw_token,
            target_identity,
            consumed_at,
        )
        link_after_first = link_snapshot(link)
        events_after_first = event_snapshot(db_session, user)
        error = assert_invalid_replay_is_safe(
            db_session,
            raw_token=raw_token,
            chat_identity=target_identity,
            now=consumed_at + timedelta(seconds=1),
        )

    db_session.refresh(token)
    db_session.refresh(link)

    assert first_result.outcome is expected_outcome
    assert token.consumed_at == consumed_at
    assert link_snapshot(link) == link_after_first
    assert link.telegram_chat_id == target_chat_id
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(TelegramLink)
            .where(TelegramLink.user_id == user.id)
        )
        == 1
    )
    assert [event[2] for event in events_after_first] == expected_event_actions
    assert event_snapshot(db_session, user) == events_after_first
    assert raw_token.as_internal_value() not in caplog.text
    assert raw_token.as_internal_value() not in f"{error!r} {error}"


@pytest.mark.integration
def test_unlink_invalidated_token_cannot_be_replayed(
    caplog,
    db_session: Session,
) -> None:
    linked_at = datetime(2026, 7, 25, 11, 20, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=1)
    unlinked_at = issued_at + timedelta(minutes=1)
    raw_token = RawTelegramLinkToken("sequential_unlink_invalidated_token")
    chat_identity = VerifiedPrivateTelegramChatIdentity(17_004)
    user = add_user(db_session, "+998900017003")
    link = add_active_link(
        db_session,
        user,
        telegram_chat_id=chat_identity.as_bigint(),
        linked_at=linked_at,
    )
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=issued_at,
    )

    with caplog.at_level(logging.DEBUG):
        unlink_result = unlink_telegram(db_session, user, unlinked_at)
        link_after_unlink = link_snapshot(link)
        events_after_unlink = event_snapshot(db_session, user)
        error = assert_invalid_replay_is_safe(
            db_session,
            raw_token=raw_token,
            chat_identity=chat_identity,
            now=unlinked_at + timedelta(seconds=1),
        )

    db_session.refresh(token)
    db_session.refresh(link)

    assert unlink_result.invalidated_token_count == 1
    assert token.consumed_at is None
    assert token.invalidated_at == unlinked_at
    assert link_snapshot(link) == link_after_unlink
    assert link.telegram_chat_id is None
    assert link.linked_at == linked_at
    assert link.unlinked_at == unlinked_at
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(TelegramLink)
            .where(TelegramLink.user_id == user.id)
        )
        == 1
    )
    assert [event[2] for event in events_after_unlink] == ["unlinked"]
    assert event_snapshot(db_session, user) == events_after_unlink
    assert raw_token.as_internal_value() not in caplog.text
    assert raw_token.as_internal_value() not in f"{error!r} {error}"
