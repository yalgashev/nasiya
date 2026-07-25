import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkTokenIssueError,
    UnlinkedTelegramLink,
)
from app.telegram.service import (
    unlink as unlink_telegram,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token


class SessionSpy:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commit_called = False
        self.rollback_called = False
        self.close_called = False

    def add(self, *args, **kwargs):
        return self.session.add(*args, **kwargs)

    def flush(self, *args, **kwargs):
        return self.session.flush(*args, **kwargs)

    def scalar(self, *args, **kwargs):
        return self.session.scalar(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return self.session.execute(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True

    def __getattr__(self, name: str):
        return getattr(self.session, name)


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
    consumed_at: datetime | None = None,
    invalidated_at: datetime | None = None,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token)),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS),
        consumed_at=consumed_at,
        invalidated_at=invalidated_at,
    )
    session.add(token)
    session.flush()
    return token


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def stored_unlink_domain_text(session: Session) -> str:
    queries = (
        (
            "telegram_links",
            "SELECT telegram_chat_id::text, linked_at::text, "
            "unlinked_at::text, updated_at::text FROM telegram_links",
        ),
        (
            "telegram_link_tokens",
            "SELECT token_hash, created_at::text, expires_at::text, "
            "consumed_at::text, invalidated_at::text FROM telegram_link_tokens",
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


def test_unlink_public_api_has_no_password_chat_or_external_user_id() -> None:
    parameters = signature(unlink_telegram).parameters

    assert list(parameters) == ["session", "current_user", "now"]
    assert "user_id" not in parameters
    assert "chat_id" not in parameters
    assert "telegram_chat_id" not in parameters
    assert "raw_chat_id" not in parameters
    assert "password" not in parameters
    assert "current_password" not in parameters
    assert "raw_password" not in parameters

    source = getsource(unlink_telegram)
    assert "commit(" not in source
    assert "rollback(" not in source
    assert "password" not in source
    assert "chat_identity" not in source


@pytest.mark.integration
def test_unlink_active_link_tombstones_invalidates_token_and_writes_event(
    caplog,
    m2_test_database: Engine,
) -> None:
    raw_token = "unlink_outstanding_token"
    consumed_raw_token = "unlink_consumed_token"
    invalidated_raw_token = "unlink_invalidated_token"
    old_chat_id = 12_345_600
    linked_at = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)
    now = linked_at + timedelta(minutes=7)
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = add_user(first_session, "+998900014001")
        link = add_link(
            first_session,
            user,
            telegram_chat_id=old_chat_id,
            linked_at=linked_at,
        )
        outstanding_token = add_token(
            first_session,
            user,
            raw_token=raw_token,
            created_at=linked_at + timedelta(minutes=1),
        )
        consumed_token = add_token(
            first_session,
            user,
            raw_token=consumed_raw_token,
            created_at=linked_at + timedelta(minutes=2),
            consumed_at=linked_at + timedelta(minutes=3),
        )
        invalidated_token = add_token(
            first_session,
            user,
            raw_token=invalidated_raw_token,
            created_at=linked_at + timedelta(minutes=4),
            invalidated_at=linked_at + timedelta(minutes=5),
        )
        first_session.commit()
        user_id = user.id
        link_id = link.id
        outstanding_token_id = outstanding_token.id
        consumed_token_id = consumed_token.id
        invalidated_token_id = invalidated_token.id

        user_in_transaction = first_session.get(User, user_id)
        assert user_in_transaction is not None
        session_spy = SessionSpy(first_session)

        with caplog.at_level(logging.INFO):
            result = unlink_telegram(session_spy, user_in_transaction, now)
        link_in_transaction = first_session.get(TelegramLink, link_id)
        token_in_transaction = first_session.get(
            TelegramLinkToken,
            outstanding_token_id,
        )
        event_in_transaction = first_session.scalar(select(TelegramLinkEvent))

        assert isinstance(result, UnlinkedTelegramLink)
        assert result.link is link_in_transaction
        assert result.event is event_in_transaction
        assert result.invalidated_token_count == 1
        assert result.link.telegram_chat_id is None
        assert result.link.linked_at == linked_at
        assert result.link.unlinked_at == now
        assert result.link.updated_at == now
        assert result.event.user_id == user_id
        assert result.event.action == "unlinked"
        assert result.event.occurred_at == now
        assert token_in_transaction is not None
        assert token_in_transaction.invalidated_at == now
        assert first_session.get(TelegramLinkToken, consumed_token_id).consumed_at == (
            linked_at + timedelta(minutes=3)
        )
        assert first_session.get(
            TelegramLinkToken,
            invalidated_token_id,
        ).invalidated_at == linked_at + timedelta(minutes=5)
        assert count_table(first_session, TelegramLinkEvent) == 1
        assert count_table(first_session, Customer) == 0
        assert second_session.get(TelegramLink, link_id).telegram_chat_id == old_chat_id
        assert (
            second_session.get(TelegramLinkToken, outstanding_token_id).invalidated_at
            is None
        )
        assert count_table(second_session, TelegramLinkEvent) == 0
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert session_spy.close_called is False
        assert str(old_chat_id) not in stored_unlink_domain_text(first_session)
        assert str(old_chat_id) not in repr(result)
        assert str(old_chat_id) not in caplog.text
        assert raw_token not in stored_unlink_domain_text(first_session)
        assert raw_token not in repr(result)
        assert raw_token not in caplog.text

        first_session.commit()
    finally:
        first_session.close()
        second_session.close()

    verify_session = session_factory()
    try:
        stored_link = verify_session.get(TelegramLink, link_id)
        stored_token = verify_session.get(TelegramLinkToken, outstanding_token_id)
        stored_events = verify_session.scalars(
            select(TelegramLinkEvent).where(TelegramLinkEvent.user_id == user_id)
        ).all()

        assert stored_link is not None
        assert stored_link.telegram_chat_id is None
        assert stored_link.linked_at == linked_at
        assert stored_link.unlinked_at == now
        assert stored_link.updated_at == now
        assert stored_token is not None
        assert stored_token.invalidated_at == now
        assert len(stored_events) == 1
        assert stored_events[0].action == "unlinked"
        assert stored_events[0].occurred_at == now
        assert str(old_chat_id) not in stored_unlink_domain_text(verify_session)
    finally:
        verify_session.rollback()
        verify_session.close()


@pytest.mark.parametrize("has_tombstone", [False, True])
@pytest.mark.integration
def test_unlink_without_active_link_preserves_state_token_and_events(
    has_tombstone: bool,
    db_session: Session,
) -> None:
    raw_token = "unlink_rejected_outstanding_token"
    linked_at = datetime(2026, 7, 24, 21, 20, tzinfo=UTC)
    tombstone_unlinked_at = linked_at + timedelta(minutes=2)
    now = linked_at + timedelta(minutes=5)
    user = add_user(
        db_session,
        "+998900014002" if has_tombstone else "+998900014003",
    )
    link = None
    if has_tombstone:
        link = add_link(
            db_session,
            user,
            telegram_chat_id=None,
            linked_at=linked_at,
            unlinked_at=tombstone_unlinked_at,
        )
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=linked_at + timedelta(minutes=1),
    )
    existing_event = None
    if has_tombstone:
        existing_event = TelegramLinkEvent(
            user_id=user.id,
            action="unlinked",
            occurred_at=tombstone_unlinked_at,
        )
        db_session.add(existing_event)
        db_session.flush()

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        unlink_telegram(db_session, user, now)
    db_session.refresh(token)
    if link is not None:
        db_session.refresh(link)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
    continuation_user = add_user(
        db_session,
        "+998900014004" if has_tombstone else "+998900014005",
    )

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_NOT_LINKED
    assert exc_info.value.public_error == {
        "code": "TELEGRAM_NOT_LINKED",
        "message": "Telegram akkauntingiz bog'lanmagan.",
    }
    assert token.consumed_at is None
    assert token.invalidated_at is None
    if link is None:
        assert count_table(db_session, TelegramLink) == 0
    else:
        assert link.telegram_chat_id is None
        assert link.linked_at == linked_at
        assert link.unlinked_at == tombstone_unlinked_at
        assert link.updated_at == tombstone_unlinked_at
        assert existing_event is not None
        db_session.refresh(existing_event)
        assert existing_event.action == "unlinked"
        assert existing_event.occurred_at == tombstone_unlinked_at
    assert count_table(db_session, TelegramLinkEvent) == (1 if has_tombstone else 0)
    assert continuation_user.id is not None
    assert raw_token not in error_text
    assert user.phone not in error_text
