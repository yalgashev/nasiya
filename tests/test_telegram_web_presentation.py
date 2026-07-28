from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLink, TelegramLinkToken
from app.telegram.web_presentation import (
    TelegramLinkAttemptPresentation,
    TelegramWebLanguage,
    get_link_attempt_presentation,
    get_telegram_web_copy,
    resolve_telegram_web_language,
)

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Iterator[Session]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def add_user(db_session: Session, phone: str) -> User:
    user = User(phone=phone)
    db_session.add(user)
    db_session.flush()
    return user


def add_token(
    db_session: Session,
    user: User,
    *,
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
    consumed_at: datetime | None = None,
    invalidated_at: datetime | None = None,
    token_hash: str = "a" * 64,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=token_hash,
        created_at=created_at,
        expires_at=expires_at or created_at + timedelta(minutes=10),
        consumed_at=consumed_at,
        invalidated_at=invalidated_at,
    )
    db_session.add(token)
    db_session.flush()
    return token


def test_attempt_status_is_waiting_for_owned_live_token(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998901111111")
    token = add_token(db_session, user)

    result = get_link_attempt_presentation(db_session, user, token.id, NOW)

    assert result is TelegramLinkAttemptPresentation.WAITING
    assert result.is_terminal is False


def test_attempt_status_maps_owned_terminal_token_states(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998902222222")
    superseded = add_token(
        db_session,
        user,
        invalidated_at=NOW,
    )
    expired = add_token(
        db_session,
        user,
        created_at=NOW - timedelta(minutes=11),
        expires_at=NOW - timedelta(minutes=1),
        token_hash="b" * 64,
    )
    consumed = add_token(
        db_session,
        user,
        consumed_at=NOW,
        token_hash="c" * 64,
    )

    assert (
        get_link_attempt_presentation(db_session, user, superseded.id, NOW)
        is TelegramLinkAttemptPresentation.SUPERSEDED
    )
    assert (
        get_link_attempt_presentation(db_session, user, expired.id, NOW)
        is TelegramLinkAttemptPresentation.EXPIRED
    )
    assert (
        get_link_attempt_presentation(db_session, user, consumed.id, NOW)
        is TelegramLinkAttemptPresentation.UNAVAILABLE
    )


def test_attempt_status_hides_foreign_unknown_and_purged_rows(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998903333333")
    other_user = add_user(db_session, "+998904444444")
    foreign = add_token(db_session, other_user)

    foreign_result = get_link_attempt_presentation(
        db_session,
        user,
        foreign.id,
        NOW,
    )
    unknown_result = get_link_attempt_presentation(
        db_session,
        user,
        uuid4(),
        NOW,
    )

    assert foreign_result is TelegramLinkAttemptPresentation.UNAVAILABLE
    assert unknown_result is TelegramLinkAttemptPresentation.UNAVAILABLE


def test_canonical_linked_status_wins_for_every_owned_attempt(
    db_session: Session,
) -> None:
    user = add_user(db_session, "+998905555555")
    superseded = add_token(
        db_session,
        user,
        invalidated_at=NOW,
    )
    db_session.add(
        TelegramLink(
            user_id=user.id,
            telegram_chat_id=900_555,
            linked_at=NOW,
            updated_at=NOW,
        )
    )
    db_session.flush()

    result = get_link_attempt_presentation(db_session, user, superseded.id, NOW)

    assert result is TelegramLinkAttemptPresentation.LINKED


@pytest.mark.parametrize(
    ("accept_language", "expected"),
    [
        (None, TelegramWebLanguage.UZ_LATN),
        ("uz-Latn-UZ,ru;q=0.8", TelegramWebLanguage.UZ_LATN),
        ("ru-RU,uz;q=0.8", TelegramWebLanguage.RU),
        ("en-US,ru;q=0.7", TelegramWebLanguage.RU),
        ("ru;q=0,uz;q=0.9", TelegramWebLanguage.UZ_LATN),
        ("en-US", TelegramWebLanguage.UZ_LATN),
    ],
)
def test_web_language_resolution_is_bounded_to_uzbek_and_russian(
    accept_language: str | None,
    expected: TelegramWebLanguage,
) -> None:
    assert resolve_telegram_web_language(accept_language) is expected


def test_web_copy_has_the_same_keys_for_both_languages() -> None:
    uz_copy = get_telegram_web_copy(TelegramWebLanguage.UZ_LATN)
    ru_copy = get_telegram_web_copy(TelegramWebLanguage.RU)

    assert uz_copy.keys() == ru_copy.keys()
    assert uz_copy["linked"] == "Bog'langan"
    assert ru_copy["linked"] == "Подключен"
