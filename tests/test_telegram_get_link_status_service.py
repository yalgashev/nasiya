import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import TelegramLinkStatus, get_link_status


class SessionSpy:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commit_called = False
        self.rollback_called = False
        self.close_called = False

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def scalar(self, *args, **kwargs):
        return self.session.scalar(*args, **kwargs)

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


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def assert_status_has_no_chat_identifier(
    status: TelegramLinkStatus,
    raw_chat_id: int,
) -> None:
    raw_chat_text = str(raw_chat_id)

    assert status in {TelegramLinkStatus.LINKED, TelegramLinkStatus.UNLINKED}
    assert not isinstance(status, TelegramLink)
    assert raw_chat_text not in str(status)
    assert raw_chat_text not in repr(status)
    assert raw_chat_text not in status.value
    assert raw_chat_text not in json.dumps({"status": status})


def test_get_link_status_public_api_is_current_user_only() -> None:
    parameters = signature(get_link_status).parameters

    assert list(parameters) == ["session", "current_user"]
    assert "user_id" not in parameters
    assert "customer_id" not in parameters
    assert "chat_id" not in parameters
    assert "telegram_chat_id" not in parameters
    assert "raw_chat_id" not in parameters

    source = getsource(get_link_status)
    assert "commit(" not in source
    assert "rollback(" not in source
    assert "append_telegram_link_event" not in source
    assert "TelegramLinkEvent" not in source
    assert "TelegramLinkToken" not in source
    assert "telegram_chat_id" not in source


@pytest.mark.integration
def test_get_link_status_returns_unlinked_for_never_linked_user(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900012001")

    status = get_link_status(db_session, user)

    assert status is TelegramLinkStatus.UNLINKED
    assert_status_has_no_chat_identifier(status, 12_001)
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, TelegramLinkToken) == 0


@pytest.mark.integration
def test_get_link_status_returns_linked_for_active_link(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 19, 5, tzinfo=UTC)
    user = add_user(db_session, "+998900012002")
    add_link(
        db_session,
        user,
        telegram_chat_id=12_002,
        linked_at=now,
    )

    status = get_link_status(db_session, user)

    assert status is TelegramLinkStatus.LINKED
    assert_status_has_no_chat_identifier(status, 12_002)
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, TelegramLinkToken) == 0


@pytest.mark.integration
def test_get_link_status_returns_unlinked_for_tombstone(
    db_session: Session,
) -> None:
    linked_at = datetime(2026, 7, 24, 19, 10, tzinfo=UTC)
    unlinked_at = linked_at + timedelta(minutes=2)
    user = add_user(db_session, "+998900012003")
    add_link(
        db_session,
        user,
        telegram_chat_id=None,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
    )

    status = get_link_status(db_session, user)

    assert status is TelegramLinkStatus.UNLINKED
    assert_status_has_no_chat_identifier(status, 12_003)
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, TelegramLinkToken) == 0


@pytest.mark.integration
def test_get_link_status_is_scoped_to_current_user(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 19, 15, tzinfo=UTC)
    linked_user = add_user(db_session, "+998900012004")
    never_linked_user = add_user(db_session, "+998900012005")
    add_link(
        db_session,
        linked_user,
        telegram_chat_id=12_004,
        linked_at=now,
    )

    linked_status = get_link_status(db_session, linked_user)
    never_linked_status = get_link_status(db_session, never_linked_user)

    assert linked_status is TelegramLinkStatus.LINKED
    assert never_linked_status is TelegramLinkStatus.UNLINKED
    assert_status_has_no_chat_identifier(linked_status, 12_004)
    assert_status_has_no_chat_identifier(never_linked_status, 12_004)
    assert count_table(db_session, TelegramLink) == 1


@pytest.mark.integration
def test_get_link_status_is_read_only_and_caller_transaction_owned(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 19, 20, tzinfo=UTC)
    user = add_user(db_session, "+998900012006")
    add_link(
        db_session,
        user,
        telegram_chat_id=12_006,
        linked_at=now,
    )
    session_spy = SessionSpy(db_session)

    status = get_link_status(session_spy, user)

    assert status is TelegramLinkStatus.LINKED
    assert session_spy.commit_called is False
    assert session_spy.rollback_called is False
    assert session_spy.close_called is False
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, TelegramLinkToken) == 0
