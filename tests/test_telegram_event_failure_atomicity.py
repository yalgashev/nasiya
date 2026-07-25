from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.telegram.service as telegram_service
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkLifecycleInternalError,
    TelegramLinkOutcome,
    consume_start_token,
)
from app.telegram.service import (
    unlink as unlink_telegram,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def add_link(
    session: Session,
    user: User,
    *,
    telegram_chat_id: int | None,
    linked_at: datetime,
    unlinked_at: datetime | None = None,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        updated_at=unlinked_at or linked_at,
    )
    session.add(link)
    session.flush()
    return link


def add_token(
    session: Session,
    user: User,
    *,
    raw_token: str,
    created_at: datetime,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token)),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS),
    )
    session.add(token)
    session.flush()
    return token


def link_state(
    session: Session,
    link_id: UUID,
) -> tuple[int | None, datetime, datetime | None, datetime] | None:
    link = session.get(TelegramLink, link_id)
    if link is None:
        return None
    return (
        link.telegram_chat_id,
        link.linked_at,
        link.unlinked_at,
        link.updated_at,
    )


def token_state(
    session: Session,
    token_id: UUID,
) -> tuple[datetime | None, datetime | None]:
    token = session.get(TelegramLinkToken, token_id)
    assert token is not None
    return (token.consumed_at, token.invalidated_at)


def event_snapshots(
    session: Session,
    user_id: UUID,
) -> list[tuple[str, datetime]]:
    events = session.scalars(
        select(TelegramLinkEvent)
        .where(TelegramLinkEvent.user_id == user_id)
        .order_by(TelegramLinkEvent.occurred_at, TelegramLinkEvent.id)
    ).all()
    return [(event.action, event.occurred_at) for event in events]


def count_links(session: Session, user_id: UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TelegramLink)
            .where(TelegramLink.user_id == user_id)
        )
        or 0
    )


def fail_event_writer(raw_detail: str) -> Callable:
    def fail(*_args, **_kwargs):
        raise SQLAlchemyError(raw_detail)

    return fail


def assert_safe_internal_failure(
    exc: TelegramLinkLifecycleInternalError,
    *forbidden_values: str,
) -> None:
    error_text = f"{exc!r} {exc}"

    assert str(exc) == "Telegram link lifecycle transition failed"
    assert exc.__cause__ is None
    for value in forbidden_values:
        assert value not in error_text


def assert_session_recovers_after_rollback(
    session: Session,
    *,
    phone: str,
) -> None:
    session.rollback()
    assert session.scalar(select(1)) == 1
    continuation_user = add_user(session, phone)
    assert continuation_user.id is not None
    session.rollback()
    assert session.scalar(select(1)) == 1


@pytest.mark.integration
def test_first_link_event_failure_rolls_back_transition_and_retry_succeeds(
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    raw_token = "event_failure_first_link_token"
    raw_database_detail = (
        "raw event insert detail event_failure_first_link_token chat 18301"
    )
    issued_at = datetime(2026, 7, 25, 13, 0, tzinfo=UTC)
    failure_at = issued_at + timedelta(minutes=1)
    retry_at = failure_at + timedelta(seconds=1)
    chat_id = 18_301
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = add_user(session, "+998900018301")
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=issued_at,
        )
        user_id = user.id
        token_id = token.id
        session.commit()

        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                telegram_service,
                "append_telegram_link_event",
                fail_event_writer(raw_database_detail),
            )
            with pytest.raises(TelegramLinkLifecycleInternalError) as exc_info:
                consume_start_token(
                    session,
                    RawTelegramLinkToken(raw_token),
                    VerifiedPrivateTelegramChatIdentity(chat_id),
                    failure_at,
                )

        assert_safe_internal_failure(
            exc_info.value,
            raw_database_detail,
            raw_token,
            str(chat_id),
        )
        assert_session_recovers_after_rollback(
            session,
            phone="+998900018302",
        )
        assert count_links(session, user_id) == 0
        assert token_state(session, token_id) == (None, None)
        assert event_snapshots(session, user_id) == []

        result = consume_start_token(
            session,
            RawTelegramLinkToken(raw_token),
            VerifiedPrivateTelegramChatIdentity(chat_id),
            retry_at,
        )
        session.commit()

        assert result.outcome is TelegramLinkOutcome.LINKED
        stored_link = session.scalar(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        )
        assert stored_link is not None
        assert link_state(session, stored_link.id) == (
            chat_id,
            retry_at,
            None,
            retry_at,
        )
        assert token_state(session, token_id) == (retry_at, None)
        assert event_snapshots(session, user_id) == [("linked", retry_at)]
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_unlink_event_failure_rolls_back_transition_and_retry_succeeds(
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    raw_token = "event_failure_unlink_token"
    raw_database_detail = (
        "raw event insert detail event_failure_unlink_token chat 18303"
    )
    chat_id = 18_303
    linked_at = datetime(2026, 7, 25, 13, 10, tzinfo=UTC)
    failure_at = linked_at + timedelta(minutes=3)
    retry_at = failure_at + timedelta(seconds=1)
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = add_user(session, "+998900018303")
        link = add_link(
            session,
            user,
            telegram_chat_id=chat_id,
            linked_at=linked_at,
        )
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=linked_at + timedelta(minutes=1),
        )
        user_id = user.id
        link_id = link.id
        token_id = token.id
        session.commit()

        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                telegram_service,
                "append_telegram_link_event",
                fail_event_writer(raw_database_detail),
            )
            current_user = session.get(User, user_id)
            assert current_user is not None
            with pytest.raises(TelegramLinkLifecycleInternalError) as exc_info:
                unlink_telegram(session, current_user, failure_at)

        assert_safe_internal_failure(
            exc_info.value,
            raw_database_detail,
            raw_token,
            str(chat_id),
        )
        assert_session_recovers_after_rollback(
            session,
            phone="+998900018304",
        )
        assert link_state(session, link_id) == (chat_id, linked_at, None, linked_at)
        assert token_state(session, token_id) == (None, None)
        assert event_snapshots(session, user_id) == []

        current_user = session.get(User, user_id)
        assert current_user is not None
        result = unlink_telegram(session, current_user, retry_at)
        session.commit()

        assert result.outcome is TelegramLinkOutcome.UNLINKED
        assert result.invalidated_token_count == 1
        assert link_state(session, link_id) == (None, linked_at, retry_at, retry_at)
        assert token_state(session, token_id) == (None, retry_at)
        assert event_snapshots(session, user_id) == [("unlinked", retry_at)]
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_relink_event_failure_rolls_back_transition_and_retry_succeeds(
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    raw_token = "event_failure_relink_token"
    raw_database_detail = (
        "raw event insert detail event_failure_relink_token chat 18306"
    )
    chat_a = 18_305
    chat_b = 18_306
    linked_at = datetime(2026, 7, 25, 13, 20, tzinfo=UTC)
    issued_at = linked_at + timedelta(minutes=1)
    failure_at = linked_at + timedelta(minutes=3)
    retry_at = failure_at + timedelta(seconds=1)
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        user = add_user(session, "+998900018305")
        link = add_link(
            session,
            user,
            telegram_chat_id=chat_a,
            linked_at=linked_at,
        )
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=issued_at,
        )
        user_id = user.id
        link_id = link.id
        token_id = token.id
        session.commit()

        with monkeypatch.context() as patch_context:
            patch_context.setattr(
                telegram_service,
                "append_telegram_link_event",
                fail_event_writer(raw_database_detail),
            )
            with pytest.raises(TelegramLinkLifecycleInternalError) as exc_info:
                consume_start_token(
                    session,
                    RawTelegramLinkToken(raw_token),
                    VerifiedPrivateTelegramChatIdentity(chat_b),
                    failure_at,
                )

        assert_safe_internal_failure(
            exc_info.value,
            raw_database_detail,
            raw_token,
            str(chat_a),
            str(chat_b),
        )
        assert_session_recovers_after_rollback(
            session,
            phone="+998900018306",
        )
        assert link_state(session, link_id) == (chat_a, linked_at, None, linked_at)
        assert token_state(session, token_id) == (None, None)
        assert event_snapshots(session, user_id) == []

        result = consume_start_token(
            session,
            RawTelegramLinkToken(raw_token),
            VerifiedPrivateTelegramChatIdentity(chat_b),
            retry_at,
        )
        session.commit()

        assert result.outcome is TelegramLinkOutcome.RELINKED
        assert link_state(session, link_id) == (chat_b, retry_at, None, retry_at)
        assert token_state(session, token_id) == (retry_at, None)
        assert event_snapshots(session, user_id) == [("relinked", retry_at)]
    finally:
        session.rollback()
        session.close()
