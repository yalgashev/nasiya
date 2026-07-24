from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLinkToken

VALID_TOKEN_HASH = "a" * 64


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
    token_hash: str,
    created_at: datetime,
    expires_at: datetime | None = None,
    consumed_at: datetime | None = None,
    invalidated_at: datetime | None = None,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=token_hash,
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


@pytest.mark.integration
def test_valid_lowercase_64_hex_hash_insert_works(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 11, 0, tzinfo=UTC)
    user = add_user(db_session, "+998900002001")

    token = add_token(db_session, user, token_hash=VALID_TOKEN_HASH, created_at=now)

    assert token.id is not None
    assert token.user_id == user.id
    assert token.token_hash == VALID_TOKEN_HASH
    assert token.consumed_at is None
    assert token.invalidated_at is None
    assert count_tokens(db_session) == 1


@pytest.mark.integration
def test_duplicate_token_hash_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 11, 5, tzinfo=UTC)
    first_user = add_user(db_session, "+998900002002")
    second_user = add_user(db_session, "+998900002003")
    add_token(db_session, first_user, token_hash="b" * 64, created_at=now)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_token(
                db_session,
                second_user,
                token_hash="b" * 64,
                created_at=now + timedelta(seconds=1),
            )

    assert count_tokens(db_session) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "token_hash",
    [
        "A" * 64,
        "a" * 63,
        "g" * 64,
    ],
)
def test_invalid_token_hash_format_is_rejected(
    db_session: Session,
    token_hash: str,
) -> None:
    now = datetime(2026, 7, 24, 11, 10, tzinfo=UTC)
    user = add_user(db_session, "+998900002004")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_token(db_session, user, token_hash=token_hash, created_at=now)

    assert count_tokens(db_session) == 0


@pytest.mark.integration
def test_too_long_token_hash_is_rejected_by_varchar_boundary(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 11, 12, tzinfo=UTC)
    user = add_user(db_session, "+998900002104")

    with pytest.raises(DataError):
        with db_session.begin_nested():
            add_token(db_session, user, token_hash="a" * 65, created_at=now)

    assert count_tokens(db_session) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "expires_delta",
    [timedelta(seconds=0), timedelta(seconds=-1)],
)
def test_expires_at_must_be_after_created_at(
    db_session: Session,
    expires_delta: timedelta,
) -> None:
    now = datetime(2026, 7, 24, 11, 15, tzinfo=UTC)
    user = add_user(db_session, "+998900002005")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_token(
                db_session,
                user,
                token_hash="c" * 64,
                created_at=now,
                expires_at=now + expires_delta,
            )

    assert count_tokens(db_session) == 0


@pytest.mark.integration
def test_consumed_and_invalidated_cannot_both_be_non_null(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 11, 20, tzinfo=UTC)
    user = add_user(db_session, "+998900002006")

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_token(
                db_session,
                user,
                token_hash="d" * 64,
                created_at=now,
                consumed_at=now + timedelta(minutes=1),
                invalidated_at=now + timedelta(minutes=2),
            )

    assert count_tokens(db_session) == 0


@pytest.mark.integration
def test_one_outstanding_token_for_user_works(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 11, 25, tzinfo=UTC)
    user = add_user(db_session, "+998900002007")

    token = add_token(db_session, user, token_hash="e" * 64, created_at=now)

    assert token.consumed_at is None
    assert token.invalidated_at is None
    assert count_tokens(db_session) == 1


@pytest.mark.integration
def test_second_outstanding_token_for_same_user_is_rejected(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 11, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900002008")
    add_token(db_session, user, token_hash="f" * 64, created_at=now)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            add_token(
                db_session,
                user,
                token_hash="1" * 64,
                created_at=now + timedelta(seconds=1),
            )

    assert count_tokens(db_session) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("terminal_field", "first_hash", "second_hash"),
    [
        ("consumed_at", "2" * 64, "3" * 64),
        ("invalidated_at", "4" * 64, "5" * 64),
    ],
)
def test_terminal_old_token_allows_new_outstanding_for_same_user(
    db_session: Session,
    terminal_field: str,
    first_hash: str,
    second_hash: str,
) -> None:
    now = datetime(2026, 7, 24, 11, 35, tzinfo=UTC)
    user = add_user(db_session, "+998900002009")
    terminal_kwargs = {terminal_field: now + timedelta(minutes=1)}
    add_token(
        db_session,
        user,
        token_hash=first_hash,
        created_at=now,
        **terminal_kwargs,
    )

    outstanding = add_token(
        db_session,
        user,
        token_hash=second_hash,
        created_at=now + timedelta(minutes=2),
    )

    assert outstanding.consumed_at is None
    assert outstanding.invalidated_at is None
    assert count_tokens(db_session) == 2


@pytest.mark.integration
def test_different_user_can_have_separate_outstanding_token(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 11, 40, tzinfo=UTC)
    first_user = add_user(db_session, "+998900002010")
    second_user = add_user(db_session, "+998900002011")

    add_token(db_session, first_user, token_hash="6" * 64, created_at=now)
    add_token(
        db_session,
        second_user,
        token_hash="7" * 64,
        created_at=now + timedelta(seconds=1),
    )

    assert count_tokens(db_session) == 2


@pytest.mark.integration
def test_missing_user_foreign_key_is_rejected(db_session: Session) -> None:
    now = datetime(2026, 7, 24, 11, 45, tzinfo=UTC)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            token = TelegramLinkToken(
                user_id=uuid4(),
                token_hash="8" * 64,
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
            db_session.add(token)
            db_session.flush()

    assert count_tokens(db_session) == 0


@pytest.mark.integration
def test_parent_user_delete_is_restricted_when_token_exists(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 11, 50, tzinfo=UTC)
    user = add_user(db_session, "+998900002012")
    token = add_token(db_session, user, token_hash="9" * 64, created_at=now)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(user)
            db_session.flush()

    assert db_session.get(User, user.id) is not None
    assert db_session.get(TelegramLinkToken, token.id) is not None
