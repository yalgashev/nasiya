from collections.abc import Generator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import DateTime, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLinkEvent


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


def add_event(
    session: Session,
    user: User,
    *,
    action: str,
    occurred_at: datetime,
) -> TelegramLinkEvent:
    event = TelegramLinkEvent(
        user_id=user.id,
        action=action,
        occurred_at=occurred_at,
    )
    session.add(event)
    session.flush()
    return event


def count_events(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TelegramLinkEvent)) or 0


@pytest.mark.integration
@pytest.mark.parametrize("action", ["linked", "unlinked", "relinked"])
def test_allowed_event_actions_insert(db_session: Session, action: str) -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    user = add_user(db_session, f"+99890000300{len(action)}")

    event = add_event(db_session, user, action=action, occurred_at=now)

    assert event.id is not None
    assert event.user_id == user.id
    assert event.action == action
    assert event.occurred_at == now
    assert count_events(db_session) == 1


@pytest.mark.integration
def test_unknown_event_action_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 12, 5, tzinfo=UTC)
    user = add_user(db_session, "+998900003004")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_event(db_session, user, action="consumed", occurred_at=now)

    assert count_events(db_session) == 0


@pytest.mark.integration
def test_missing_user_foreign_key_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 12, 10, tzinfo=UTC)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            event = TelegramLinkEvent(
                user_id=uuid4(),
                action="linked",
                occurred_at=now,
            )
            db_session.add(event)
            db_session.flush()

    assert count_events(db_session) == 0


@pytest.mark.integration
def test_parent_user_delete_is_restricted_when_event_exists(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 12, 15, tzinfo=UTC)
    user = add_user(db_session, "+998900003005")
    event = add_event(db_session, user, action="linked", occurred_at=now)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(user)
            db_session.flush()

    assert db_session.get(User, user.id) is not None
    assert db_session.get(TelegramLinkEvent, event.id) is not None


def test_event_table_keeps_m4_narrow_schema() -> None:
    columns = TelegramLinkEvent.__table__.columns

    assert set(columns.keys()) == {"id", "user_id", "action", "occurred_at"}
    assert columns["occurred_at"].nullable is False
    assert isinstance(columns["occurred_at"].type, DateTime)
    assert columns["occurred_at"].type.timezone is True

    forbidden_columns = {
        "telegram_chat_id",
        "old_chat_id",
        "new_chat_id",
        "chat_id",
        "token",
        "token_hash",
        "raw_token",
        "phone",
        "ip",
        "ip_address",
        "username",
        "update_json",
        "message",
        "metadata",
        "updated_at",
        "deleted_at",
    }
    assert forbidden_columns.isdisjoint(columns.keys())


def test_event_user_foreign_key_restricts_parent_delete() -> None:
    user_id_column = TelegramLinkEvent.__table__.columns["user_id"]
    foreign_key = next(iter(user_id_column.foreign_keys))

    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "RESTRICT"


def test_event_relationships_do_not_delete_cascade() -> None:
    for mapper in (User.__mapper__, TelegramLinkEvent.__mapper__):
        for relationship in mapper.relationships:
            if relationship.mapper.class_ not in {User, TelegramLinkEvent}:
                continue

            assert "delete" not in relationship.cascade
            assert "delete-orphan" not in relationship.cascade
