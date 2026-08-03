import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.repository import (
    get_other_active_telegram_link_by_chat_identity_for_update,
    get_telegram_link_by_user,
    get_telegram_link_by_user_for_update,
    get_telegram_link_status,
    has_active_telegram_link,
    has_otp_eligible_telegram_link,
    is_otp_eligible_telegram_link,
    link_phone_verified_private_chat_from_prelocked_state,
    link_unverified_private_chat,
    relink_phone_verified_private_chat_from_prelocked_state,
    relink_unverified_private_chat,
    unlink_verified_private_chat,
)


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
    phone_verified_at: datetime | None = None,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=telegram_chat_id,
        linked_at=linked_at,
        unlinked_at=unlinked_at,
        phone_verified_at=phone_verified_at,
        updated_at=unlinked_at or linked_at,
    )
    session.add(link)
    session.flush()
    return link


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def seed_committed_active_link(
    engine: Engine,
    *,
    phone: str,
    telegram_chat_id: int,
) -> tuple[UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = add_user(session, phone)
        link = add_link(
            session,
            user,
            telegram_chat_id=telegram_chat_id,
            linked_at=datetime(2026, 7, 24, 17, 0, tzinfo=UTC),
        )
        user_id = user.id
        link_id = link.id
        session.commit()
        return user_id, link_id
    finally:
        session.close()


def assert_link_row_is_locked_by_other_transaction(
    session: Session,
    link_id: UUID,
) -> None:
    with pytest.raises(OperationalError):
        session.execute(
            select(TelegramLink.id)
            .where(TelegramLink.id == link_id)
            .with_for_update(nowait=True)
        ).all()
    session.rollback()


def test_link_repository_public_api_uses_current_user_and_verified_identity() -> None:
    link_callables = (
        has_active_telegram_link,
        get_telegram_link_by_user,
        get_telegram_link_by_user_for_update,
        get_other_active_telegram_link_by_chat_identity_for_update,
        link_unverified_private_chat,
        relink_unverified_private_chat,
        unlink_verified_private_chat,
        get_telegram_link_status,
    )

    for callable_object in link_callables:
        parameters = signature(callable_object).parameters
        assert "user_id" not in parameters
        assert "customer_id" not in parameters
        assert "chat_id" not in parameters
        assert "telegram_chat_id" not in parameters
        assert "raw_chat_id" not in parameters
        for parameter in parameters.values():
            assert parameter.annotation is not int
            if "chat" in parameter.name:
                assert parameter.name == "chat_identity"
                assert parameter.annotation is VerifiedPrivateTelegramChatIdentity

    source = "\n".join(getsource(callable_object) for callable_object in link_callables)
    assert "logging" not in source
    assert "logger" not in source
    assert "print(" not in source
    assert "commit(" not in source
    assert "rollback(" not in source
    assert "TelegramLinkEvent" not in source
    assert "TelegramLinkToken" not in source


@pytest.mark.integration
def test_link_lookup_and_status_are_own_user_scoped(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 17, 5, tzinfo=UTC)
    user = add_user(db_session, "+998900008001")
    other_user = add_user(db_session, "+998900008002")
    empty_user = add_user(db_session, "+998900008003")
    own_link = add_link(
        db_session,
        user,
        telegram_chat_id=8001,
        linked_at=now,
    )
    other_link = add_link(
        db_session,
        other_user,
        telegram_chat_id=8002,
        linked_at=now,
    )

    found = get_telegram_link_by_user(db_session, user)
    status = get_telegram_link_status(db_session, user)
    missing = get_telegram_link_status(db_session, empty_user)

    assert found is own_link
    assert status is own_link
    assert missing is None
    assert other_link.user_id == other_user.id
    assert other_link is not own_link


@pytest.mark.integration
def test_otp_link_policy_requires_exact_owner_and_verified_generation(
    db_session: Session,
) -> None:
    now = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
    owner = add_user(db_session, "+998900008021")
    other_user = add_user(db_session, "+998900008022")
    link = add_link(
        db_session,
        owner,
        telegram_chat_id=8022,
        linked_at=now,
        phone_verified_at=now,
    )

    assert is_otp_eligible_telegram_link(link, expected_user_id=owner.id)
    assert not is_otp_eligible_telegram_link(
        link,
        expected_user_id=other_user.id,
    )
    assert has_otp_eligible_telegram_link(db_session, owner)
    assert not has_otp_eligible_telegram_link(db_session, other_user)

    link.phone_verified_at = None
    db_session.flush()

    assert not is_otp_eligible_telegram_link(link, expected_user_id=owner.id)
    assert not has_otp_eligible_telegram_link(db_session, owner)


@pytest.mark.integration
def test_get_link_by_user_for_update_locks_own_link_row(
    m2_test_database: Engine,
) -> None:
    user_id, link_id = seed_committed_active_link(
        m2_test_database,
        phone="+998900008004",
        telegram_chat_id=8004,
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = first_session.get(User, user_id)
        assert user is not None

        link = get_telegram_link_by_user_for_update(first_session, user)

        assert link is not None
        assert link.id == link_id
        assert_link_row_is_locked_by_other_transaction(second_session, link_id)
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_other_active_chat_lookup_uses_verified_identity_and_locks_other_link(
    m2_test_database: Engine,
) -> None:
    current_user_id, current_link_id = seed_committed_active_link(
        m2_test_database,
        phone="+998900008005",
        telegram_chat_id=8005,
    )
    _other_user_id, other_link_id = seed_committed_active_link(
        m2_test_database,
        phone="+998900008006",
        telegram_chat_id=8006,
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        current_user = first_session.get(User, current_user_id)
        assert current_user is not None

        other_link = get_other_active_telegram_link_by_chat_identity_for_update(
            first_session,
            current_user,
            VerifiedPrivateTelegramChatIdentity(8006),
        )
        own_link = get_other_active_telegram_link_by_chat_identity_for_update(
            first_session,
            current_user,
            VerifiedPrivateTelegramChatIdentity(8005),
        )

        assert other_link is not None
        assert other_link.id == other_link_id
        assert own_link is None
        assert current_link_id != other_link_id
        assert_link_row_is_locked_by_other_transaction(second_session, other_link_id)
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_first_link_creates_active_row_without_token_or_event_side_effects(
    db_session: Session,
    caplog,
) -> None:
    now = datetime(2026, 7, 24, 17, 10, tzinfo=UTC)
    user = add_user(db_session, "+998900008007")

    with caplog.at_level(logging.INFO):
        link = link_unverified_private_chat(
            db_session,
            user,
            VerifiedPrivateTelegramChatIdentity(8007),
            now,
        )

    assert link is not None
    assert link.user_id == user.id
    assert link.telegram_chat_id == 8007
    assert link.linked_at == now
    assert link.phone_verified_at is None
    assert link.unlinked_at is None
    assert link.updated_at == now
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert "8007" not in caplog.text


@pytest.mark.integration
def test_first_link_reactivates_existing_tombstone_row(db_session: Session) -> None:
    first_linked_at = datetime(2026, 7, 24, 17, 15, tzinfo=UTC)
    unlinked_at = first_linked_at + timedelta(minutes=5)
    relinked_at = first_linked_at + timedelta(minutes=10)
    user = add_user(db_session, "+998900008008")
    tombstone = add_link(
        db_session,
        user,
        telegram_chat_id=None,
        linked_at=first_linked_at,
        unlinked_at=unlinked_at,
    )

    link = link_unverified_private_chat(
        db_session,
        user,
        VerifiedPrivateTelegramChatIdentity(8008),
        relinked_at,
    )

    assert link is tombstone
    assert link.telegram_chat_id == 8008
    assert link.linked_at == relinked_at
    assert link.phone_verified_at is None
    assert link.unlinked_at is None
    assert link.updated_at == relinked_at
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_first_link_does_not_replace_existing_active_chat(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 17, 20, tzinfo=UTC)
    user = add_user(db_session, "+998900008009")
    existing = add_link(
        db_session,
        user,
        telegram_chat_id=8009,
        linked_at=now,
    )

    link = link_unverified_private_chat(
        db_session,
        user,
        VerifiedPrivateTelegramChatIdentity(8010),
        now + timedelta(minutes=1),
    )
    db_session.refresh(existing)

    assert link is None
    assert existing.telegram_chat_id == 8009
    assert existing.linked_at == now
    assert existing.unlinked_at is None
    assert existing.updated_at == now
    assert count_table(db_session, TelegramLink) == 1


@pytest.mark.integration
def test_relink_updates_current_active_chat_only(db_session: Session) -> None:
    first_linked_at = datetime(2026, 7, 24, 17, 25, tzinfo=UTC)
    relinked_at = first_linked_at + timedelta(minutes=3)
    user = add_user(db_session, "+998900008010")
    active_link = add_link(
        db_session,
        user,
        telegram_chat_id=8011,
        linked_at=first_linked_at,
        phone_verified_at=first_linked_at,
    )

    link = relink_unverified_private_chat(
        db_session,
        user,
        VerifiedPrivateTelegramChatIdentity(8012),
        relinked_at,
    )

    assert link is active_link
    assert link.telegram_chat_id == 8012
    assert link.linked_at == relinked_at
    assert link.phone_verified_at is None
    assert link.unlinked_at is None
    assert link.updated_at == relinked_at
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_relink_returns_none_without_active_link(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 17, 30, tzinfo=UTC)
    no_link_user = add_user(db_session, "+998900008011")
    tombstone_user = add_user(db_session, "+998900008012")
    tombstone = add_link(
        db_session,
        tombstone_user,
        telegram_chat_id=None,
        linked_at=now,
        unlinked_at=now + timedelta(minutes=1),
    )

    no_link_result = relink_unverified_private_chat(
        db_session,
        no_link_user,
        VerifiedPrivateTelegramChatIdentity(8013),
        now + timedelta(minutes=2),
    )
    tombstone_result = relink_unverified_private_chat(
        db_session,
        tombstone_user,
        VerifiedPrivateTelegramChatIdentity(8014),
        now + timedelta(minutes=2),
    )
    db_session.refresh(tombstone)

    assert no_link_result is None
    assert tombstone_result is None
    assert tombstone.telegram_chat_id is None
    assert tombstone.unlinked_at == now + timedelta(minutes=1)
    assert count_table(db_session, TelegramLink) == 1


@pytest.mark.integration
def test_unlink_active_row_sets_tombstone_state(db_session: Session) -> None:
    linked_at = datetime(2026, 7, 24, 17, 35, tzinfo=UTC)
    unlinked_at = linked_at + timedelta(minutes=4)
    user = add_user(db_session, "+998900008013")
    active_link = add_link(
        db_session,
        user,
        telegram_chat_id=8015,
        linked_at=linked_at,
        phone_verified_at=linked_at,
    )

    link = unlink_verified_private_chat(db_session, user, unlinked_at)

    assert link is active_link
    assert link.telegram_chat_id is None
    assert link.linked_at == linked_at
    assert link.unlinked_at == unlinked_at
    assert link.phone_verified_at is None
    assert link.updated_at == unlinked_at
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_phone_verified_generation_helpers_set_exact_shared_timestamp(
    db_session: Session,
) -> None:
    linked_at = datetime(2026, 8, 2, 19, 0, tzinfo=UTC)
    relinked_at = linked_at + timedelta(minutes=1)
    user = add_user(db_session, "+998900008020")

    link = link_phone_verified_private_chat_from_prelocked_state(
        db_session,
        user,
        VerifiedPrivateTelegramChatIdentity(8020),
        linked_at,
        existing_link=None,
    )
    assert link is not None
    assert link.linked_at == linked_at
    assert link.phone_verified_at == linked_at

    relinked = relink_phone_verified_private_chat_from_prelocked_state(
        db_session,
        user,
        VerifiedPrivateTelegramChatIdentity(8021),
        relinked_at,
        existing_link=link,
    )
    assert relinked is link
    assert relinked.linked_at == relinked_at
    assert relinked.phone_verified_at == relinked_at
    assert relinked.updated_at == relinked_at


@pytest.mark.integration
def test_unlink_returns_none_without_active_link(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 17, 40, tzinfo=UTC)
    no_link_user = add_user(db_session, "+998900008014")
    tombstone_user = add_user(db_session, "+998900008015")
    tombstone_unlinked_at = now + timedelta(minutes=1)
    tombstone = add_link(
        db_session,
        tombstone_user,
        telegram_chat_id=None,
        linked_at=now,
        unlinked_at=tombstone_unlinked_at,
    )

    no_link_result = unlink_verified_private_chat(db_session, no_link_user, now)
    tombstone_result = unlink_verified_private_chat(
        db_session,
        tombstone_user,
        now + timedelta(minutes=2),
    )
    db_session.refresh(tombstone)

    assert no_link_result is None
    assert tombstone_result is None
    assert tombstone.telegram_chat_id is None
    assert tombstone.unlinked_at == tombstone_unlinked_at
    assert count_table(db_session, TelegramLink) == 1


@pytest.mark.integration
def test_active_chat_uniqueness_remains_database_owned(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 17, 45, tzinfo=UTC)
    first_user = add_user(db_session, "+998900008016")
    second_user = add_user(db_session, "+998900008017")
    link_unverified_private_chat(
        db_session,
        first_user,
        VerifiedPrivateTelegramChatIdentity(8016),
        now,
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            link_unverified_private_chat(
                db_session,
                second_user,
                VerifiedPrivateTelegramChatIdentity(8016),
                now + timedelta(seconds=1),
            )
    continuation_user = add_user(db_session, "+998900008018")

    assert continuation_user.id is not None
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_link_repository_does_not_commit_rollback_or_close(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    session_spy = SessionSpy(first_session)
    linked_at = datetime(2026, 7, 24, 17, 50, tzinfo=UTC)
    try:
        user = add_user(first_session, "+998900008019")

        created = link_unverified_private_chat(
            session_spy,
            user,
            VerifiedPrivateTelegramChatIdentity(8017),
            linked_at,
        )
        relinked = relink_unverified_private_chat(
            session_spy,
            user,
            VerifiedPrivateTelegramChatIdentity(8018),
            linked_at + timedelta(minutes=1),
        )
        unlinked = unlink_verified_private_chat(
            session_spy,
            user,
            linked_at + timedelta(minutes=2),
        )
        status = get_telegram_link_status(session_spy, user)
        locked = get_telegram_link_by_user_for_update(session_spy, user)
        stored_link_count = count_table(second_session, TelegramLink)

        assert created is not None
        assert relinked is created
        assert unlinked is created
        assert status is created
        assert locked is created
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert session_spy.close_called is False
        assert stored_link_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()
