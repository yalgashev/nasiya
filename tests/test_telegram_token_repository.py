from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

import app.telegram.repository as telegram_repository
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLink, TelegramLinkToken
from app.telegram.repository import (
    TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS,
    TelegramLinkTokenInsertConflict,
    get_outstanding_telegram_link_token_for_update,
    get_telegram_link_token_by_hash_for_update,
    get_telegram_link_token_status,
    get_telegram_link_tokens_eligible_for_purge,
    get_valid_telegram_link_token_for_consume_by_hash_for_update,
    has_active_telegram_link,
    insert_telegram_link_token,
    invalidate_and_insert_telegram_link_token,
    invalidate_outstanding_telegram_link_tokens,
)


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


def token_hash(seed: int) -> str:
    return f"{seed:064x}"


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
    token_hash_value: str,
    created_at: datetime,
    expires_at: datetime | None = None,
    consumed_at: datetime | None = None,
    invalidated_at: datetime | None = None,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=token_hash_value,
        created_at=created_at,
        expires_at=expires_at or created_at + timedelta(minutes=10),
        consumed_at=consumed_at,
        invalidated_at=invalidated_at,
    )
    session.add(token)
    session.flush()
    return token


def count_tokens(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TelegramLinkToken)) or 0


def seed_committed_token(
    engine: Engine,
    *,
    phone: str,
    token_hash_value: str,
) -> tuple[UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = add_user(session, phone)
        token = add_token(
            session,
            user,
            token_hash_value=token_hash_value,
            created_at=datetime(2026, 7, 24, 15, 0, tzinfo=UTC),
        )
        user_id = user.id
        token_id = token.id
        session.commit()
        return user_id, token_id
    finally:
        session.close()


def assert_token_row_is_locked_by_other_transaction(
    session: Session,
    token_id: UUID,
) -> None:
    with pytest.raises(OperationalError):
        session.execute(
            select(TelegramLinkToken.id)
            .where(TelegramLinkToken.id == token_id)
            .with_for_update(nowait=True)
        ).all()
    session.rollback()


def test_repository_public_api_is_current_user_and_hash_only() -> None:
    for callable_object in (
        has_active_telegram_link,
        get_outstanding_telegram_link_token_for_update,
        get_telegram_link_token_by_hash_for_update,
        get_valid_telegram_link_token_for_consume_by_hash_for_update,
        invalidate_outstanding_telegram_link_tokens,
        insert_telegram_link_token,
        invalidate_and_insert_telegram_link_token,
        get_telegram_link_token_status,
        get_telegram_link_tokens_eligible_for_purge,
    ):
        parameters = signature(callable_object).parameters
        assert "raw_token" not in parameters
        assert "user_id" not in parameters
        assert "chat_id" not in parameters
        assert "telegram_chat_id" not in parameters

    source = getsource(telegram_repository)
    assert "RawTelegramLinkToken" not in source
    assert "TelegramLinkEvent" not in source
    assert "TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS" in source
    assert "os.getenv" not in source
    assert "telegram_link_token_retention" not in source


@pytest.mark.integration
def test_has_active_telegram_link_checks_current_user_only(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 5, tzinfo=UTC)
    no_link_user = add_user(db_session, "+998900006001")
    active_user = add_user(db_session, "+998900006002")
    tombstone_user = add_user(db_session, "+998900006003")
    other_active_user = add_user(db_session, "+998900006004")
    add_link(
        db_session,
        active_user,
        telegram_chat_id=7001,
        linked_at=now,
    )
    add_link(
        db_session,
        tombstone_user,
        telegram_chat_id=None,
        linked_at=now,
        unlinked_at=now + timedelta(minutes=1),
    )
    add_link(
        db_session,
        other_active_user,
        telegram_chat_id=7002,
        linked_at=now,
    )

    assert has_active_telegram_link(db_session, no_link_user) is False
    assert has_active_telegram_link(db_session, active_user) is True
    assert has_active_telegram_link(db_session, tombstone_user) is False


@pytest.mark.integration
def test_insert_and_outstanding_lookup_use_hash_only_current_user_scope(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 10, tzinfo=UTC)
    user = add_user(db_session, "+998900006005")
    other_user = add_user(db_session, "+998900006006")
    consumed = add_token(
        db_session,
        user,
        token_hash_value=token_hash(1),
        created_at=now,
        consumed_at=now + timedelta(minutes=1),
    )
    other_token = add_token(
        db_session,
        other_user,
        token_hash_value=token_hash(2),
        created_at=now,
    )

    outstanding = insert_telegram_link_token(
        db_session,
        user,
        token_hash(3),
        now + timedelta(minutes=2),
        now + timedelta(minutes=12),
    )
    found = get_outstanding_telegram_link_token_for_update(db_session, user)

    assert found is outstanding
    assert found.token_hash == token_hash(3)
    assert found.user_id == user.id
    assert consumed.consumed_at is not None
    assert other_token.user_id == other_user.id
    assert count_tokens(db_session) == 3


@pytest.mark.integration
def test_lookup_by_hash_locks_only_matching_token_hash(
    m2_test_database: Engine,
) -> None:
    token_hash_value = token_hash(4)
    user_id, token_id = seed_committed_token(
        m2_test_database,
        phone="+998900006007",
        token_hash_value=token_hash_value,
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        user = first_session.get(User, user_id)
        assert user is not None

        locked = get_telegram_link_token_by_hash_for_update(
            first_session,
            token_hash_value,
        )

        assert locked is not None
        assert locked.id == token_id
        assert_token_row_is_locked_by_other_transaction(second_session, token_id)
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_outstanding_lookup_locks_current_user_token_row(
    m2_test_database: Engine,
) -> None:
    user_id, token_id = seed_committed_token(
        m2_test_database,
        phone="+998900006008",
        token_hash_value=token_hash(5),
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        current_user = first_session.get(User, user_id)
        assert current_user is not None

        locked = get_outstanding_telegram_link_token_for_update(
            first_session,
            current_user,
        )

        assert locked is not None
        assert locked.id == token_id
        assert_token_row_is_locked_by_other_transaction(second_session, token_id)
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_invalidate_outstanding_tokens_updates_current_user_only(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 15, tzinfo=UTC)
    user = add_user(db_session, "+998900006009")
    other_user = add_user(db_session, "+998900006010")
    outstanding = add_token(
        db_session,
        user,
        token_hash_value=token_hash(6),
        created_at=now,
    )
    consumed = add_token(
        db_session,
        user,
        token_hash_value=token_hash(7),
        created_at=now,
        consumed_at=now + timedelta(minutes=1),
    )
    already_invalidated = add_token(
        db_session,
        user,
        token_hash_value=token_hash(8),
        created_at=now,
        invalidated_at=now + timedelta(minutes=2),
    )
    other_outstanding = add_token(
        db_session,
        other_user,
        token_hash_value=token_hash(9),
        created_at=now,
    )
    invalidated_at = now + timedelta(minutes=3)

    updated_count = invalidate_outstanding_telegram_link_tokens(
        db_session,
        user,
        invalidated_at,
    )
    db_session.refresh(outstanding)
    db_session.refresh(consumed)
    db_session.refresh(already_invalidated)
    db_session.refresh(other_outstanding)

    assert updated_count == 1
    assert outstanding.invalidated_at == invalidated_at
    assert consumed.invalidated_at is None
    assert already_invalidated.invalidated_at == now + timedelta(minutes=2)
    assert other_outstanding.invalidated_at is None


@pytest.mark.integration
def test_user_token_status_is_production_repository_query(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 20, tzinfo=UTC)
    user = add_user(db_session, "+998900006011")
    empty_user = add_user(db_session, "+998900006012")
    add_token(
        db_session,
        user,
        token_hash_value=token_hash(10),
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )
    add_token(
        db_session,
        user,
        token_hash_value=token_hash(11),
        created_at=now,
        consumed_at=now + timedelta(minutes=1),
    )
    add_token(
        db_session,
        user,
        token_hash_value=token_hash(12),
        created_at=now,
        invalidated_at=now + timedelta(minutes=2),
    )

    status = get_telegram_link_token_status(db_session, user, now)
    empty_status = get_telegram_link_token_status(db_session, empty_user, now)

    assert status.total_count == 3
    assert status.outstanding_count == 1
    assert status.expired_outstanding_count == 1
    assert status.consumed_count == 1
    assert status.invalidated_count == 1
    assert empty_status.total_count == 0
    assert empty_status.outstanding_count == 0
    assert empty_status.expired_outstanding_count == 0
    assert empty_status.consumed_count == 0
    assert empty_status.invalidated_count == 0


def test_token_terminal_retention_constant_is_exactly_thirty_days() -> None:
    assert TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS == 30


@pytest.mark.integration
def test_purge_eligibility_selects_exact_thirty_day_terminal_tokens_only(
    caplog,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 25, 14, 30, tzinfo=UTC)
    cutoff = now - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    user = add_user(db_session, "+998900006101")
    other_expired_user = add_user(db_session, "+998900006103")
    valid_outstanding_user = add_user(db_session, "+998900006104")
    linked_at = now - timedelta(days=60)
    link = add_link(
        db_session,
        user,
        telegram_chat_id=71_101,
        linked_at=linked_at,
    )
    eligible_consumed_old = add_token(
        db_session,
        user,
        token_hash_value=token_hash(101),
        created_at=cutoff - timedelta(days=2),
        consumed_at=cutoff - timedelta(microseconds=1),
    )
    eligible_consumed_boundary = add_token(
        db_session,
        user,
        token_hash_value=token_hash(102),
        created_at=cutoff - timedelta(days=1),
        consumed_at=cutoff,
    )
    ineligible_consumed_new = add_token(
        db_session,
        user,
        token_hash_value=token_hash(103),
        created_at=cutoff,
        consumed_at=cutoff + timedelta(microseconds=1),
    )
    eligible_invalidated_old = add_token(
        db_session,
        user,
        token_hash_value=token_hash(104),
        created_at=cutoff + timedelta(seconds=1),
        invalidated_at=cutoff - timedelta(seconds=1),
    )
    eligible_invalidated_boundary = add_token(
        db_session,
        user,
        token_hash_value=token_hash(105),
        created_at=cutoff + timedelta(seconds=2),
        invalidated_at=cutoff,
    )
    ineligible_invalidated_new = add_token(
        db_session,
        user,
        token_hash_value=token_hash(106),
        created_at=cutoff + timedelta(seconds=3),
        invalidated_at=cutoff + timedelta(seconds=1),
    )
    eligible_expired_unused = add_token(
        db_session,
        user,
        token_hash_value=token_hash(107),
        created_at=cutoff - timedelta(minutes=10),
        expires_at=cutoff,
    )
    ineligible_expired_unused_new = add_token(
        db_session,
        other_expired_user,
        token_hash_value=token_hash(108),
        created_at=cutoff - timedelta(minutes=9),
        expires_at=cutoff + timedelta(microseconds=1),
    )
    valid_outstanding = add_token(
        db_session,
        valid_outstanding_user,
        token_hash_value=token_hash(109),
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=9),
    )

    with caplog.at_level("DEBUG"):
        eligible = get_telegram_link_tokens_eligible_for_purge(
            db_session,
            now,
            limit=20,
        )
    eligible_ids = [token.id for token in eligible]

    assert eligible_ids == [
        eligible_consumed_old.id,
        eligible_consumed_boundary.id,
        eligible_expired_unused.id,
        eligible_invalidated_old.id,
        eligible_invalidated_boundary.id,
    ]
    assert ineligible_consumed_new.id not in eligible_ids
    assert ineligible_invalidated_new.id not in eligible_ids
    assert ineligible_expired_unused_new.id not in eligible_ids
    assert valid_outstanding.id not in eligible_ids
    assert count_tokens(db_session) == 9
    assert db_session.get(TelegramLink, link.id) is link
    assert link.telegram_chat_id == 71_101
    assert caplog.text == ""
    for token in eligible:
        assert token.token_hash not in caplog.text
        assert str(token.user_id) not in caplog.text
    assert str(link.telegram_chat_id) not in caplog.text


@pytest.mark.integration
def test_purge_eligibility_ordering_and_limit_are_deterministic(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 25, 14, 40, tzinfo=UTC)
    cutoff = now - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    user = add_user(db_session, "+998900006102")
    first = add_token(
        db_session,
        user,
        token_hash_value=token_hash(201),
        created_at=cutoff - timedelta(days=3),
        consumed_at=cutoff,
    )
    second = add_token(
        db_session,
        user,
        token_hash_value=token_hash(202),
        created_at=cutoff - timedelta(days=2),
        invalidated_at=cutoff,
    )
    third = add_token(
        db_session,
        user,
        token_hash_value=token_hash(203),
        created_at=cutoff - timedelta(days=1),
        expires_at=cutoff,
    )

    eligible = get_telegram_link_tokens_eligible_for_purge(
        db_session,
        now,
        limit=2,
    )

    assert [token.id for token in eligible] == [first.id, second.id]
    assert third.id not in [token.id for token in eligible]


@pytest.mark.integration
def test_purge_eligibility_rejects_naive_now_and_invalid_limit(
    db_session: Session,
) -> None:
    with pytest.raises(ValueError) as naive_error:
        get_telegram_link_tokens_eligible_for_purge(
            db_session,
            datetime(2026, 7, 25, 14, 50),
            limit=10,
        )
    with pytest.raises(ValueError) as limit_error:
        get_telegram_link_tokens_eligible_for_purge(
            db_session,
            datetime(2026, 7, 25, 14, 50, tzinfo=UTC),
            limit=0,
        )

    assert "timezone-aware" in str(naive_error.value)
    assert "limit" in str(limit_error.value)


@pytest.mark.integration
def test_hash_lookup_and_insert_reject_non_hash_without_echoing_value(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998900006013")
    raw_token = "raw-url-safe-link-token"
    now = datetime(2026, 7, 24, 15, 25, tzinfo=UTC)

    with pytest.raises(ValueError) as lookup_error:
        get_telegram_link_token_by_hash_for_update(db_session, raw_token)
    with pytest.raises(ValueError) as insert_error:
        insert_telegram_link_token(
            db_session,
            user,
            raw_token,
            now,
            now + timedelta(minutes=10),
        )

    assert raw_token not in str(lookup_error.value)
    assert raw_token not in str(insert_error.value)
    assert count_tokens(db_session) == 0


@pytest.mark.integration
def test_partial_unique_conflict_is_isolated_by_savepoint(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900006014")
    other_user = add_user(db_session, "+998900006015")
    first = insert_telegram_link_token(
        db_session,
        user,
        token_hash(13),
        now,
        now + timedelta(minutes=10),
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            insert_telegram_link_token(
                db_session,
                user,
                token_hash(14),
                now + timedelta(seconds=1),
                now + timedelta(minutes=10),
            )

    other = insert_telegram_link_token(
        db_session,
        other_user,
        token_hash(15),
        now,
        now + timedelta(minutes=10),
    )

    assert db_session.get(TelegramLinkToken, first.id) is first
    assert db_session.get(TelegramLinkToken, other.id) is other
    assert count_tokens(db_session) == 2


@pytest.mark.integration
def test_repository_does_not_commit_or_full_rollback(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    session_spy = SessionSpy(first_session)
    now = datetime(2026, 7, 24, 15, 35, tzinfo=UTC)
    try:
        user = add_user(first_session, "+998900006016")
        add_link(
            first_session,
            user,
            telegram_chat_id=7016,
            linked_at=now,
        )

        assert has_active_telegram_link(session_spy, user) is True
        token = insert_telegram_link_token(
            session_spy,
            user,
            token_hash(16),
            now,
            now + timedelta(minutes=10),
        )
        found_outstanding = get_outstanding_telegram_link_token_for_update(
            session_spy,
            user,
        )
        assert found_outstanding is token
        assert (
            get_telegram_link_token_by_hash_for_update(session_spy, token_hash(16))
            is token
        )
        assert get_telegram_link_token_status(
            session_spy,
            user,
            now,
        ).outstanding_count == 1
        assert (
            invalidate_outstanding_telegram_link_tokens(
                session_spy,
                user,
                now + timedelta(minutes=1),
            )
            == 1
        )
        stored_token_count = second_session.scalar(
            select(func.count()).select_from(TelegramLinkToken)
        )

        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert stored_token_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_reissue_invalidates_existing_outstanding_and_inserts_new_token(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 40, tzinfo=UTC)
    user = add_user(db_session, "+998900006017")
    existing = add_token(
        db_session,
        user,
        token_hash_value=token_hash(17),
        created_at=now - timedelta(minutes=1),
    )

    new_token = invalidate_and_insert_telegram_link_token(
        db_session,
        user,
        token_hash(18),
        now,
        now + timedelta(minutes=10),
    )
    status = get_telegram_link_token_status(db_session, user, now)

    assert existing.invalidated_at == now
    assert new_token.user_id == user.id
    assert new_token.token_hash == token_hash(18)
    assert new_token.created_at == now
    assert new_token.expires_at == now + timedelta(minutes=10)
    assert new_token.consumed_at is None
    assert new_token.invalidated_at is None
    assert status.total_count == 2
    assert status.invalidated_count == 1
    assert status.outstanding_count == 1


@pytest.mark.integration
def test_reissue_invalidates_expired_unterminal_outstanding_before_insert(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 45, tzinfo=UTC)
    user = add_user(db_session, "+998900006018")
    expired = add_token(
        db_session,
        user,
        token_hash_value=token_hash(19),
        created_at=now - timedelta(minutes=20),
        expires_at=now - timedelta(minutes=10),
    )

    new_token = invalidate_and_insert_telegram_link_token(
        db_session,
        user,
        token_hash(20),
        now,
        now + timedelta(minutes=10),
    )
    status = get_telegram_link_token_status(db_session, user, now)

    assert expired.invalidated_at == now
    assert new_token.token_hash == token_hash(20)
    assert status.outstanding_count == 1
    assert status.expired_outstanding_count == 0
    assert status.invalidated_count == 1


@pytest.mark.integration
def test_reissue_locks_existing_outstanding_row(
    m2_test_database: Engine,
) -> None:
    user_id, token_id = seed_committed_token(
        m2_test_database,
        phone="+998900006019",
        token_hash_value=token_hash(21),
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    now = datetime(2026, 7, 24, 15, 50, tzinfo=UTC)
    try:
        current_user = first_session.get(User, user_id)
        assert current_user is not None

        new_token = invalidate_and_insert_telegram_link_token(
            first_session,
            current_user,
            token_hash(22),
            now,
            now + timedelta(minutes=10),
        )

        assert new_token.token_hash == token_hash(22)
        assert_token_row_is_locked_by_other_transaction(second_session, token_id)
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_duplicate_hash_conflict_is_typed_and_outer_transaction_remains_usable(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 15, 55, tzinfo=UTC)
    user = add_user(db_session, "+998900006020")
    other_user = add_user(db_session, "+998900006021")
    existing = add_token(
        db_session,
        user,
        token_hash_value=token_hash(23),
        created_at=now,
    )
    add_token(
        db_session,
        other_user,
        token_hash_value=token_hash(24),
        created_at=now,
        consumed_at=now + timedelta(minutes=1),
    )

    with pytest.raises(TelegramLinkTokenInsertConflict) as exc_info:
        invalidate_and_insert_telegram_link_token(
            db_session,
            user,
            token_hash(24),
            now + timedelta(minutes=2),
            now + timedelta(minutes=12),
        )
    usable_token = insert_telegram_link_token(
        db_session,
        other_user,
        token_hash(25),
        now + timedelta(minutes=3),
        now + timedelta(minutes=13),
    )
    db_session.refresh(existing)

    assert "IntegrityError" not in str(exc_info.value)
    assert token_hash(24) not in str(exc_info.value)
    assert existing.invalidated_at is None
    assert usable_token.token_hash == token_hash(25)
    assert count_tokens(db_session) == 3


@pytest.mark.integration
def test_partial_unique_conflict_is_typed_and_outer_transaction_remains_usable(
    monkeypatch,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 16, 0, tzinfo=UTC)
    user = add_user(db_session, "+998900006022")
    other_user = add_user(db_session, "+998900006023")
    existing = add_token(
        db_session,
        user,
        token_hash_value=token_hash(26),
        created_at=now,
    )
    monkeypatch.setattr(
        telegram_repository,
        "get_outstanding_telegram_link_token_for_update",
        lambda session, current_user: None,
    )

    with pytest.raises(TelegramLinkTokenInsertConflict) as exc_info:
        telegram_repository.invalidate_and_insert_telegram_link_token(
            db_session,
            user,
            token_hash(27),
            now + timedelta(minutes=1),
            now + timedelta(minutes=11),
        )
    usable_token = insert_telegram_link_token(
        db_session,
        other_user,
        token_hash(28),
        now,
        now + timedelta(minutes=10),
    )
    db_session.refresh(existing)

    assert "IntegrityError" not in str(exc_info.value)
    assert token_hash(27) not in str(exc_info.value)
    assert existing.invalidated_at is None
    assert usable_token.token_hash == token_hash(28)
    assert count_tokens(db_session) == 2


@pytest.mark.integration
def test_reissue_repository_does_not_commit_or_full_rollback(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    session_spy = SessionSpy(first_session)
    now = datetime(2026, 7, 24, 16, 5, tzinfo=UTC)
    try:
        user = add_user(first_session, "+998900006024")
        add_token(
            first_session,
            user,
            token_hash_value=token_hash(29),
            created_at=now - timedelta(minutes=1),
        )

        new_token = invalidate_and_insert_telegram_link_token(
            session_spy,
            user,
            token_hash(30),
            now,
            now + timedelta(minutes=10),
        )
        stored_token_count = second_session.scalar(
            select(func.count()).select_from(TelegramLinkToken)
        )

        assert new_token.token_hash == token_hash(30)
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert stored_token_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()
