import logging
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramChatAlreadyLinkedError,
    TelegramLinkTokenConsumeError,
    TelegramStartTokenConsumeOutcome,
    consume_start_token,
    issue_link_token,
    issue_relink_token,
)
from app.telegram.service import (
    unlink as unlink_telegram,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-lifecycle-reuse"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        telegram_link_rate_limit_user_attempts=20,
        telegram_link_rate_limit_phone_attempts=20,
        telegram_link_rate_limit_ip_attempts=50,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_generator(raw_token: str) -> Callable[[int], str]:
    def token_generator(_byte_count: int) -> str:
        return raw_token

    return token_generator


def add_user(session: Session, phone: str) -> User:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    return user


def issue_first_raw(
    session: Session,
    settings: Settings,
    user: User,
    *,
    raw_token: str,
    client_ip: str,
    now: datetime,
) -> str:
    result = issue_link_token(
        session,
        settings,
        user,
        ResolvedClientIp(client_ip),
        now,
        token_generator=make_generator(raw_token),
    )
    assert result.raw_token.as_internal_value() == raw_token
    return raw_token


def issue_relink_raw(
    session: Session,
    settings: Settings,
    user: User,
    *,
    raw_token: str,
    client_ip: str,
    now: datetime,
) -> str:
    result = issue_relink_token(
        session,
        settings,
        user,
        ResolvedClientIp(client_ip),
        now,
        token_generator=make_generator(raw_token),
    )
    assert result.raw_token.as_internal_value() == raw_token
    return raw_token


def consume_raw(
    session: Session,
    *,
    raw_token: str,
    telegram_chat_id: int,
    now: datetime,
):
    return consume_start_token(
        session,
        raw_token,
        VerifiedPrivateTelegramChatIdentity(telegram_chat_id),
        now,
    )


def get_user_link(session: Session, user: User) -> TelegramLink:
    link = session.scalar(select(TelegramLink).where(TelegramLink.user_id == user.id))
    assert link is not None
    return link


def user_link_count(session: Session, user: User) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(TelegramLink)
            .where(TelegramLink.user_id == user.id)
        )
        or 0
    )


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def event_actions(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(TelegramLinkEvent.action).order_by(TelegramLinkEvent.occurred_at)
        )
    )


def event_text(session: Session) -> str:
    rows = session.execute(
        text("SELECT action, occurred_at::text FROM telegram_link_events")
    ).all()
    return "|".join(str(value) for row in rows for value in row)


def link_and_event_text(session: Session) -> str:
    rows = session.execute(
        text(
            "SELECT telegram_chat_id::text, linked_at::text, "
            "unlinked_at::text, updated_at::text FROM telegram_links"
        )
    ).all()
    link_text = "|".join(str(value) for row in rows for value in row)
    return f"{link_text}|{event_text(session)}"


@pytest.mark.integration
def test_first_link_unlink_then_first_link_reuses_same_tombstone_row(
    caplog,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session, "+998900015001")
    chat_a = 15_001
    chat_b = 15_002
    first_issue_at = datetime(2026, 7, 24, 22, 0, tzinfo=UTC)
    first_linked_at = first_issue_at + timedelta(seconds=10)
    unlinked_at = first_linked_at + timedelta(minutes=3)
    second_issue_at = unlinked_at + timedelta(seconds=10)
    second_linked_at = second_issue_at + timedelta(seconds=10)

    with caplog.at_level(logging.INFO):
        first_raw = issue_first_raw(
            db_session,
            settings,
            user,
            raw_token="reuse_first_token",
            client_ip="203.0.113.201",
            now=first_issue_at,
        )
        first_result = consume_raw(
            db_session,
            raw_token=first_raw,
            telegram_chat_id=chat_a,
            now=first_linked_at,
        )
        first_link_id = first_result.link.id
        unlink_telegram(db_session, user, unlinked_at)
        second_raw = issue_first_raw(
            db_session,
            settings,
            user,
            raw_token="reuse_second_token",
            client_ip="203.0.113.202",
            now=second_issue_at,
        )
        second_result = consume_raw(
            db_session,
            raw_token=second_raw,
            telegram_chat_id=chat_b,
            now=second_linked_at,
        )

    link = get_user_link(db_session, user)

    assert second_result.outcome is TelegramStartTokenConsumeOutcome.LINKED
    assert second_result.link.id == first_link_id
    assert link.id == first_link_id
    assert user_link_count(db_session, user) == 1
    assert link.telegram_chat_id == chat_b
    assert link.linked_at == second_linked_at
    assert link.updated_at == second_linked_at
    assert link.unlinked_at is None
    assert event_actions(db_session) == ["linked", "unlinked", "linked"]
    assert count_events(db_session) == 3
    assert str(chat_a) not in link_and_event_text(db_session)
    assert str(chat_a) not in caplog.text
    assert first_raw not in caplog.text
    assert second_raw not in caplog.text


@pytest.mark.integration
def test_unlinked_old_chat_can_be_linked_by_another_user_fresh_token(
    caplog,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user_a = add_user(db_session, "+998900015002")
    user_b = add_user(db_session, "+998900015003")
    released_chat = 15_003
    linked_at = datetime(2026, 7, 24, 22, 20, tzinfo=UTC)
    unlinked_at = linked_at + timedelta(minutes=2)
    user_b_linked_at = unlinked_at + timedelta(minutes=1)

    with caplog.at_level(logging.INFO):
        user_a_raw = issue_first_raw(
            db_session,
            settings,
            user_a,
            raw_token="released_chat_user_a_token",
            client_ip="203.0.113.203",
            now=linked_at - timedelta(seconds=10),
        )
        consume_raw(
            db_session,
            raw_token=user_a_raw,
            telegram_chat_id=released_chat,
            now=linked_at,
        )
        unlink_telegram(db_session, user_a, unlinked_at)
        user_b_raw = issue_first_raw(
            db_session,
            settings,
            user_b,
            raw_token="released_chat_user_b_token",
            client_ip="203.0.113.204",
            now=user_b_linked_at - timedelta(seconds=10),
        )
        user_b_result = consume_raw(
            db_session,
            raw_token=user_b_raw,
            telegram_chat_id=released_chat,
            now=user_b_linked_at,
        )

    user_a_link = get_user_link(db_session, user_a)
    user_b_link = get_user_link(db_session, user_b)

    assert user_a_link.telegram_chat_id is None
    assert user_a_link.unlinked_at == unlinked_at
    assert user_b_result.link is user_b_link
    assert user_b_link.telegram_chat_id == released_chat
    assert user_b_link.linked_at == user_b_linked_at
    assert user_b_link.updated_at == user_b_linked_at
    assert user_b_link.unlinked_at is None
    assert user_link_count(db_session, user_a) == 1
    assert user_link_count(db_session, user_b) == 1
    assert event_actions(db_session) == ["linked", "unlinked", "linked"]
    assert str(released_chat) not in event_text(db_session)
    assert str(released_chat) not in caplog.text
    assert user_a_raw not in caplog.text
    assert user_b_raw not in caplog.text


@pytest.mark.integration
def test_successful_relink_releases_old_chat_for_another_user(
    caplog,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user_a = add_user(db_session, "+998900015004")
    user_b = add_user(db_session, "+998900015005")
    chat_a = 15_004
    chat_b = 15_005
    initial_at = datetime(2026, 7, 24, 22, 40, tzinfo=UTC)
    relinked_at = initial_at + timedelta(minutes=2)
    user_b_linked_at = relinked_at + timedelta(minutes=1)

    with caplog.at_level(logging.INFO):
        first_raw = issue_first_raw(
            db_session,
            settings,
            user_a,
            raw_token="relink_release_first_token",
            client_ip="203.0.113.205",
            now=initial_at - timedelta(seconds=10),
        )
        consume_raw(
            db_session,
            raw_token=first_raw,
            telegram_chat_id=chat_a,
            now=initial_at,
        )
        relink_raw = issue_relink_raw(
            db_session,
            settings,
            user_a,
            raw_token="relink_release_relink_token",
            client_ip="203.0.113.206",
            now=relinked_at - timedelta(seconds=10),
        )
        relink_result = consume_raw(
            db_session,
            raw_token=relink_raw,
            telegram_chat_id=chat_b,
            now=relinked_at,
        )
        user_b_raw = issue_first_raw(
            db_session,
            settings,
            user_b,
            raw_token="relink_release_user_b_token",
            client_ip="203.0.113.207",
            now=user_b_linked_at - timedelta(seconds=10),
        )
        user_b_result = consume_raw(
            db_session,
            raw_token=user_b_raw,
            telegram_chat_id=chat_a,
            now=user_b_linked_at,
        )

    user_a_link = get_user_link(db_session, user_a)
    user_b_link = get_user_link(db_session, user_b)

    assert relink_result.outcome is TelegramStartTokenConsumeOutcome.RELINKED
    assert user_b_result.outcome is TelegramStartTokenConsumeOutcome.LINKED
    assert user_a_link.telegram_chat_id == chat_b
    assert user_a_link.linked_at == relinked_at
    assert user_a_link.updated_at == relinked_at
    assert user_a_link.unlinked_at is None
    assert user_b_link.telegram_chat_id == chat_a
    assert user_b_link.linked_at == user_b_linked_at
    assert user_b_link.updated_at == user_b_linked_at
    assert user_b_link.unlinked_at is None
    assert event_actions(db_session) == ["linked", "relinked", "linked"]
    assert str(chat_a) not in event_text(db_session)
    assert str(chat_b) not in event_text(db_session)
    assert str(chat_a) not in caplog.text
    assert str(chat_b) not in caplog.text


@pytest.mark.integration
def test_token_expiry_reissue_and_failure_do_not_release_active_chat(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user_a = add_user(db_session, "+998900015006")
    user_b = add_user(db_session, "+998900015007")
    stable_chat = 15_006
    collision_chat = 15_007
    initial_at = datetime(2026, 7, 24, 23, 0, tzinfo=UTC)
    expired_issue_at = initial_at + timedelta(minutes=1)
    expired_consume_at = expired_issue_at + timedelta(
        seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 1
    )
    reissue_at = expired_consume_at + timedelta(seconds=1)
    second_reissue_at = reissue_at + timedelta(seconds=1)
    collision_at = second_reissue_at + timedelta(seconds=1)

    first_raw = issue_first_raw(
        db_session,
        settings,
        user_a,
        raw_token="failure_release_first_token",
        client_ip="203.0.113.208",
        now=initial_at - timedelta(seconds=10),
    )
    consume_raw(
        db_session,
        raw_token=first_raw,
        telegram_chat_id=stable_chat,
        now=initial_at,
    )
    expired_raw = issue_relink_raw(
        db_session,
        settings,
        user_a,
        raw_token="failure_release_expired_token",
        client_ip="203.0.113.209",
        now=expired_issue_at,
    )

    with pytest.raises(TelegramLinkTokenConsumeError) as expired_exc_info:
        consume_raw(
            db_session,
            raw_token=expired_raw,
            telegram_chat_id=15_008,
            now=expired_consume_at,
        )
    expired_token = db_session.scalar(
        select(TelegramLinkToken).where(
            TelegramLinkToken.user_id == user_a.id,
            TelegramLinkToken.created_at == expired_issue_at,
        )
    )

    assert expired_exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert get_user_link(db_session, user_a).telegram_chat_id == stable_chat
    assert expired_token is not None
    assert expired_token.consumed_at is None
    assert expired_token.invalidated_at is None

    second_raw = issue_relink_raw(
        db_session,
        settings,
        user_a,
        raw_token="failure_release_second_token",
        client_ip="203.0.113.210",
        now=reissue_at,
    )
    db_session.refresh(expired_token)

    assert get_user_link(db_session, user_a).telegram_chat_id == stable_chat
    assert expired_token.invalidated_at == reissue_at

    candidate_raw = issue_relink_raw(
        db_session,
        settings,
        user_a,
        raw_token="failure_release_candidate_token",
        client_ip="203.0.113.211",
        now=second_reissue_at,
    )
    second_token = db_session.scalar(
        select(TelegramLinkToken).where(
            TelegramLinkToken.user_id == user_a.id,
            TelegramLinkToken.created_at == reissue_at,
        )
    )
    candidate_token = db_session.scalar(
        select(TelegramLinkToken).where(
            TelegramLinkToken.user_id == user_a.id,
            TelegramLinkToken.created_at == second_reissue_at,
        )
    )
    db_session.refresh(second_token)

    assert second_raw == "failure_release_second_token"
    assert get_user_link(db_session, user_a).telegram_chat_id == stable_chat
    assert second_token is not None
    assert second_token.invalidated_at == second_reissue_at
    assert candidate_token is not None
    assert candidate_token.consumed_at is None
    assert candidate_token.invalidated_at is None

    user_b_raw = issue_first_raw(
        db_session,
        settings,
        user_b,
        raw_token="failure_release_user_b_token",
        client_ip="203.0.113.212",
        now=collision_at - timedelta(seconds=1),
    )
    consume_raw(
        db_session,
        raw_token=user_b_raw,
        telegram_chat_id=collision_chat,
        now=collision_at,
    )

    with pytest.raises(TelegramChatAlreadyLinkedError) as collision_exc_info:
        consume_raw(
            db_session,
            raw_token=candidate_raw,
            telegram_chat_id=collision_chat,
            now=collision_at + timedelta(seconds=1),
        )
    db_session.refresh(candidate_token)

    assert collision_exc_info.value.error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    assert get_user_link(db_session, user_a).telegram_chat_id == stable_chat
    assert candidate_token.consumed_at is None
    assert candidate_token.invalidated_at is None
    assert event_actions(db_session) == ["linked", "linked"]


@pytest.mark.integration
def test_no_active_chat_historical_blacklist_table_or_hash_column(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)
    table_names = set(inspector.get_table_names())
    telegram_tables = {table for table in table_names if table.startswith("telegram_")}

    assert telegram_tables == {
        "telegram_links",
        "telegram_link_tokens",
        "telegram_link_events",
    }
    assert not any("blacklist" in table or "history" in table for table in table_names)
    for table_name in telegram_tables:
        column_names = {column["name"] for column in inspector.get_columns(table_name)}
        assert "telegram_chat_id_hash" not in column_names
        assert "old_telegram_chat_id" not in column_names
        assert "previous_telegram_chat_id" not in column_names


def count_events(session: Session) -> int:
    return count_table(session, TelegramLinkEvent)
