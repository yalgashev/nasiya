from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLink


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
    phone_verified_at: datetime | None = None,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        phone_verified_at=phone_verified_at,
        updated_at=linked_at if unlinked_at is None else unlinked_at,
    )
    session.add(link)
    session.flush()
    return link


def count_links(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TelegramLink)) or 0


@pytest.mark.integration
def test_valid_active_link_insert_works(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    user = add_user(db_session, "+998900001001")

    link = add_link(
        db_session,
        user,
        telegram_chat_id=1001001,
        linked_at=now,
    )

    assert link.id is not None
    assert link.user_id == user.id
    assert link.telegram_chat_id == 1001001
    assert link.unlinked_at is None
    assert count_links(db_session) == 1


@pytest.mark.integration
def test_second_link_row_for_same_user_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 5, tzinfo=UTC)
    user = add_user(db_session, "+998900001002")
    add_link(db_session, user, telegram_chat_id=1001002, linked_at=now)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_link(
                db_session,
                user,
                telegram_chat_id=1001003,
                linked_at=now + timedelta(seconds=1),
            )

    assert count_links(db_session) == 1


@pytest.mark.integration
def test_different_user_can_use_different_active_chat(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 10, tzinfo=UTC)
    first_user = add_user(db_session, "+998900001003")
    second_user = add_user(db_session, "+998900001004")

    first_link = add_link(
        db_session,
        first_user,
        telegram_chat_id=1001004,
        linked_at=now,
    )
    second_link = add_link(
        db_session,
        second_user,
        telegram_chat_id=1001005,
        linked_at=now,
    )

    assert first_link.telegram_chat_id != second_link.telegram_chat_id
    assert count_links(db_session) == 2


@pytest.mark.integration
def test_same_active_chat_for_second_user_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 15, tzinfo=UTC)
    first_user = add_user(db_session, "+998900001005")
    second_user = add_user(db_session, "+998900001006")
    add_link(db_session, first_user, telegram_chat_id=1001006, linked_at=now)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_link(
                db_session,
                second_user,
                telegram_chat_id=1001006,
                linked_at=now + timedelta(seconds=1),
            )

    assert count_links(db_session) == 1


@pytest.mark.integration
def test_active_state_requires_chat_and_no_unlink_time(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 20, tzinfo=UTC)
    user = add_user(db_session, "+998900001007")

    link = add_link(db_session, user, telegram_chat_id=1001007, linked_at=now)

    assert link.telegram_chat_id == 1001007
    assert link.unlinked_at is None


@pytest.mark.integration
def test_tombstone_state_requires_null_chat_and_unlink_time(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 25, tzinfo=UTC)
    user = add_user(db_session, "+998900001008")
    unlinked_at = now + timedelta(minutes=1)

    link = add_link(
        db_session,
        user,
        telegram_chat_id=None,
        linked_at=now,
        unlinked_at=unlinked_at,
    )

    assert link.telegram_chat_id is None
    assert link.unlinked_at == unlinked_at


@pytest.mark.integration
def test_null_chat_without_unlink_time_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900001009")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_link(
                db_session,
                user,
                telegram_chat_id=None,
                linked_at=now,
                unlinked_at=None,
            )

    assert count_links(db_session) == 0


@pytest.mark.integration
def test_chat_with_unlink_time_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 35, tzinfo=UTC)
    user = add_user(db_session, "+998900001010")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_link(
                db_session,
                user,
                telegram_chat_id=1001010,
                linked_at=now,
                unlinked_at=now + timedelta(minutes=1),
            )

    assert count_links(db_session) == 0


@pytest.mark.integration
def test_missing_user_foreign_key_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 10, 40, tzinfo=UTC)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            link = TelegramLink(
                user_id=uuid4(),
                telegram_chat_id=1001011,
                linked_at=now,
                updated_at=now,
            )
            db_session.add(link)
            db_session.flush()

    assert count_links(db_session) == 0


@pytest.mark.integration
def test_parent_user_delete_is_restricted_when_link_exists(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 45, tzinfo=UTC)
    user = add_user(db_session, "+998900001011")
    link = add_link(db_session, user, telegram_chat_id=1001012, linked_at=now)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(user)
            db_session.flush()

    assert db_session.get(User, user.id) is not None
    assert db_session.get(TelegramLink, link.id) is not None


@pytest.mark.integration
def test_exact_link_generation_phone_verification_is_accepted(
    db_session: Session,
) -> None:
    now = datetime(2026, 8, 2, 10, 50, tzinfo=UTC)
    user = add_user(db_session, "+998900001012")

    link = add_link(
        db_session,
        user,
        telegram_chat_id=1_001_013,
        linked_at=now,
        phone_verified_at=now,
    )

    assert link.phone_verified_at == link.linked_at
    assert link.unlinked_at is None


@pytest.mark.integration
def test_phone_verification_must_equal_link_generation(
    db_session: Session,
) -> None:
    now = datetime(2026, 8, 2, 10, 55, tzinfo=UTC)
    user = add_user(db_session, "+998900001013")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_link(
                db_session,
                user,
                telegram_chat_id=1_001_014,
                linked_at=now,
                phone_verified_at=now + timedelta(microseconds=1),
            )


@pytest.mark.integration
def test_unlinked_tombstone_cannot_remain_phone_verified(
    db_session: Session,
) -> None:
    now = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
    user = add_user(db_session, "+998900001014")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_link(
                db_session,
                user,
                telegram_chat_id=None,
                linked_at=now,
                unlinked_at=now + timedelta(minutes=1),
                phone_verified_at=now,
            )
