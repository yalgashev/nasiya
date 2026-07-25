import inspect
import logging
from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.events as telegram_events
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.events import append_telegram_link_event
from app.telegram.models import TelegramLinkEvent


class SessionSpy:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commit_called = False
        self.rollback_called = False

    def add(self, *args, **kwargs):
        return self.session.add(*args, **kwargs)

    def flush(self, *args, **kwargs):
        return self.session.flush(*args, **kwargs)

    def scalar(self, *args, **kwargs):
        return self.session.scalar(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return self.session.execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True

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


def count_events(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TelegramLinkEvent)) or 0


def test_event_writer_public_api_is_append_only_and_sensitive_payload_free() -> None:
    parameters = inspect.signature(append_telegram_link_event).parameters

    assert list(parameters) == ["session", "user_id", "action", "occurred_at"]
    for forbidden_parameter in (
        "chat_id",
        "telegram_chat_id",
        "token",
        "token_hash",
        "raw_token",
        "phone",
        "ip",
        "client_ip",
        "metadata",
        "message",
        "update_json",
    ):
        assert forbidden_parameter not in parameters

    for module_name in dir(telegram_events):
        if module_name.startswith("_"):
            continue
        callable_object = getattr(telegram_events, module_name)
        if not callable(callable_object):
            continue
        assert not module_name.startswith(("update", "delete", "replace"))

    source = inspect.getsource(telegram_events)
    assert "logging" not in source
    assert "logger" not in source
    assert "print(" not in source
    assert "commit(" not in source
    assert "rollback(" not in source


@pytest.mark.integration
@pytest.mark.parametrize("action", ["linked", "unlinked", "relinked"])
def test_append_telegram_link_event_writes_valid_narrow_event(
    db_session: Session,
    action: str,
    caplog,
) -> None:
    now = datetime(2026, 7, 24, 18, 0, tzinfo=UTC)
    user = add_user(db_session, f"+99890000900{len(action)}")

    with caplog.at_level(logging.INFO):
        event = append_telegram_link_event(
            db_session,
            user.id,
            action,  # type: ignore[arg-type]
            now,
        )

    assert isinstance(event.id, UUID)
    assert event.user_id == user.id
    assert event.action == action
    assert event.occurred_at == now
    assert count_events(db_session) == 1
    assert str(user.id) not in caplog.text
    assert user.phone not in caplog.text


@pytest.mark.integration
def test_append_telegram_link_event_rejects_invalid_action_without_db_write(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 18, 5, tzinfo=UTC)
    user = add_user(db_session, "+998900009004")
    invalid_action = "linked_with_raw_token_secret"

    with pytest.raises(ValueError) as exc_info:
        append_telegram_link_event(
            db_session,
            user.id,
            invalid_action,  # type: ignore[arg-type]
            now,
        )

    assert invalid_action not in str(exc_info.value)
    assert str(user.id) not in str(exc_info.value)
    assert count_events(db_session) == 0


@pytest.mark.integration
def test_append_telegram_link_event_does_not_commit_or_full_rollback(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    session_spy = SessionSpy(first_session)
    now = datetime(2026, 7, 24, 18, 10, tzinfo=UTC)
    try:
        user = add_user(first_session, "+998900009005")

        event = append_telegram_link_event(session_spy, user.id, "linked", now)
        stored_event_count = count_events(second_session)

        assert isinstance(event.id, UUID)
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert stored_event_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_append_telegram_link_event_persists_in_caller_transaction(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    now = datetime(2026, 7, 24, 18, 15, tzinfo=UTC)
    try:
        user = add_user(first_session, "+998900009006")
        event = append_telegram_link_event(first_session, user.id, "relinked", now)
        event_id = event.id
        first_session.commit()
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        stored_event = second_session.get(TelegramLinkEvent, event_id)

        assert stored_event is not None
        assert stored_event.action == "relinked"
        assert stored_event.occurred_at == now
        assert count_events(second_session) == 1
    finally:
        second_session.rollback()
        second_session.close()
