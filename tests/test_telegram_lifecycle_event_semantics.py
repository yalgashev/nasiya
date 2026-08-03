import inspect as python_inspect
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import DateTime, func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.telegram.events as telegram_events
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramChatAlreadyLinkedError,
    TelegramLinkTokenConsumeError,
    TelegramLinkTokenIssueError,
    TelegramStartTokenConsumeOutcome,
    bind_start_token_for_contact,
    consume_start_token,
)
from app.telegram.service import (
    unlink as unlink_telegram,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token
from tests.telegram_issue_helpers import (
    issue_link_token_in_one_test_transaction as issue_link_token,
)
from tests.telegram_issue_helpers import (
    issue_relink_token_in_one_test_transaction as issue_relink_token,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-events"
CONTACT_BINDING_KEY = SecretStr(TEST_RATE_LIMIT_HMAC_KEY)


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
        telegram_link_rate_limit_user_attempts=50,
        telegram_link_rate_limit_phone_attempts=50,
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


def count_events(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(TelegramLinkEvent)) or 0


def all_events(session: Session) -> list[TelegramLinkEvent]:
    return list(
        session.scalars(
            select(TelegramLinkEvent).order_by(
                TelegramLinkEvent.occurred_at,
                TelegramLinkEvent.id,
            )
        )
    )


def assert_events(
    session: Session,
    expected: list[tuple[UUID, str, datetime]],
) -> list[TelegramLinkEvent]:
    events = all_events(session)

    assert [(event.user_id, event.action, event.occurred_at) for event in events] == (
        expected
    )
    return events


def get_user_link(session: Session, user: User) -> TelegramLink:
    link = session.scalar(select(TelegramLink).where(TelegramLink.user_id == user.id))
    assert link is not None
    return link


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
    raw = RawTelegramLinkToken(raw_token)
    token_hash = hash_telegram_link_token(raw)
    phone = session.scalar(
        select(User.phone)
        .join(TelegramLinkToken, TelegramLinkToken.user_id == User.id)
        .where(TelegramLinkToken.token_hash == token_hash)
    )
    assert phone is not None
    chat_identity = VerifiedPrivateTelegramChatIdentity(telegram_chat_id)
    sender_identity = TelegramUserIdentity(telegram_chat_id)
    bind_start_token_for_contact(
        session,
        raw,
        chat_identity,
        sender_identity,
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now,
    )
    return consume_start_token(
        session,
        chat_identity,
        sender_identity,
        sender_identity,
        SensitiveTelegramContactPhone(phone),
        rate_limit_hmac_key=CONTACT_BINDING_KEY,
        now=now,
    )


@pytest.mark.integration
def test_successful_lifecycle_transitions_write_exactly_one_event_at_injected_now(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session, "+998900016001")
    chat_a = 16_001
    chat_b = 16_002
    chat_c = 16_003
    first_issue_at = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)
    first_linked_at = first_issue_at + timedelta(seconds=10)
    unlinked_at = first_linked_at + timedelta(minutes=3)
    second_issue_at = unlinked_at + timedelta(seconds=10)
    second_linked_at = second_issue_at + timedelta(seconds=10)
    relink_issue_at = second_linked_at + timedelta(seconds=10)
    relinked_at = relink_issue_at + timedelta(seconds=10)

    first_raw = issue_first_raw(
        db_session,
        settings,
        user,
        raw_token="event_semantics_first_token",
        client_ip="203.0.113.221",
        now=first_issue_at,
    )
    assert count_events(db_session) == 0

    first_result = consume_raw(
        db_session,
        raw_token=first_raw,
        telegram_chat_id=chat_a,
        now=first_linked_at,
    )
    events = assert_events(db_session, [(user.id, "linked", first_linked_at)])
    first_link = get_user_link(db_session, user)

    assert first_result.outcome is TelegramStartTokenConsumeOutcome.LINKED
    assert first_result.event is events[0]
    assert first_link.telegram_chat_id == chat_a
    assert first_link.linked_at == first_linked_at
    assert first_link.updated_at == first_linked_at
    assert first_link.unlinked_at is None

    unlink_result = unlink_telegram(db_session, user, unlinked_at)
    events = assert_events(
        db_session,
        [
            (user.id, "linked", first_linked_at),
            (user.id, "unlinked", unlinked_at),
        ],
    )
    unlinked_link = get_user_link(db_session, user)

    assert unlink_result.event is events[1]
    assert unlinked_link.telegram_chat_id is None
    assert unlinked_link.linked_at == first_linked_at
    assert unlinked_link.unlinked_at == unlinked_at
    assert unlinked_link.updated_at == unlinked_at

    second_raw = issue_first_raw(
        db_session,
        settings,
        user,
        raw_token="event_semantics_second_token",
        client_ip="203.0.113.222",
        now=second_issue_at,
    )
    assert_events(
        db_session,
        [
            (user.id, "linked", first_linked_at),
            (user.id, "unlinked", unlinked_at),
        ],
    )

    second_result = consume_raw(
        db_session,
        raw_token=second_raw,
        telegram_chat_id=chat_b,
        now=second_linked_at,
    )
    events = assert_events(
        db_session,
        [
            (user.id, "linked", first_linked_at),
            (user.id, "unlinked", unlinked_at),
            (user.id, "linked", second_linked_at),
        ],
    )
    second_link = get_user_link(db_session, user)

    assert second_result.outcome is TelegramStartTokenConsumeOutcome.LINKED
    assert second_result.event is events[2]
    assert second_link.telegram_chat_id == chat_b
    assert second_link.linked_at == second_linked_at
    assert second_link.updated_at == second_linked_at
    assert second_link.unlinked_at is None

    relink_raw = issue_relink_raw(
        db_session,
        settings,
        user,
        raw_token="event_semantics_relink_token",
        client_ip="203.0.113.223",
        now=relink_issue_at,
    )
    assert_events(
        db_session,
        [
            (user.id, "linked", first_linked_at),
            (user.id, "unlinked", unlinked_at),
            (user.id, "linked", second_linked_at),
        ],
    )

    relink_result = consume_raw(
        db_session,
        raw_token=relink_raw,
        telegram_chat_id=chat_c,
        now=relinked_at,
    )
    events = assert_events(
        db_session,
        [
            (user.id, "linked", first_linked_at),
            (user.id, "unlinked", unlinked_at),
            (user.id, "linked", second_linked_at),
            (user.id, "relinked", relinked_at),
        ],
    )
    relinked_link = get_user_link(db_session, user)

    assert relink_result.outcome is TelegramStartTokenConsumeOutcome.RELINKED
    assert relink_result.event is events[3]
    assert relinked_link.telegram_chat_id == chat_c
    assert relinked_link.linked_at == relinked_at
    assert relinked_link.updated_at == relinked_at
    assert relinked_link.unlinked_at is None


@pytest.mark.integration
def test_noop_issue_and_failure_paths_do_not_write_events_or_duplicates(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    settings = make_settings(m2_test_database)
    user = add_user(db_session, "+998900016002")
    issue_only_user = add_user(db_session, "+998900016003")
    no_link_user = add_user(db_session, "+998900016004")
    chat_a = 16_004
    collision_chat = 16_005
    linked_at = datetime(2026, 7, 25, 10, 0, tzinfo=UTC)

    issue_first_raw(
        db_session,
        settings,
        issue_only_user,
        raw_token="event_issue_only_first",
        client_ip="203.0.113.224",
        now=linked_at,
    )
    issue_first_raw(
        db_session,
        settings,
        issue_only_user,
        raw_token="event_issue_only_reissue",
        client_ip="203.0.113.225",
        now=linked_at + timedelta(seconds=1),
    )
    assert count_events(db_session) == 0

    first_raw = issue_first_raw(
        db_session,
        settings,
        user,
        raw_token="event_noop_first_token",
        client_ip="203.0.113.226",
        now=linked_at + timedelta(seconds=2),
    )
    consume_raw(
        db_session,
        raw_token=first_raw,
        telegram_chat_id=chat_a,
        now=linked_at + timedelta(seconds=3),
    )
    assert count_events(db_session) == 1

    with pytest.raises(TelegramLinkTokenIssueError) as active_issue_exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.227"),
            linked_at + timedelta(seconds=4),
            token_generator=make_generator("event_active_issue_rejected"),
        )
    assert active_issue_exc_info.value.error_code is ErrorCode.TELEGRAM_ALREADY_LINKED
    assert count_events(db_session) == 1

    with pytest.raises(TelegramLinkTokenIssueError) as no_link_exc_info:
        issue_relink_token(
            db_session,
            settings,
            no_link_user,
            ResolvedClientIp("203.0.113.228"),
            linked_at + timedelta(seconds=5),
            token_generator=make_generator("event_no_link_relink_rejected"),
        )
    assert no_link_exc_info.value.error_code is ErrorCode.TELEGRAM_NOT_LINKED
    assert count_events(db_session) == 1

    same_chat_first_raw = issue_relink_raw(
        db_session,
        settings,
        user,
        raw_token="event_same_chat_first_issue",
        client_ip="203.0.113.229",
        now=linked_at + timedelta(seconds=6),
    )
    assert same_chat_first_raw == "event_same_chat_first_issue"
    same_chat_raw = issue_relink_raw(
        db_session,
        settings,
        user,
        raw_token="event_same_chat_reissue",
        client_ip="203.0.113.230",
        now=linked_at + timedelta(seconds=7),
    )
    assert count_events(db_session) == 1

    same_chat_result = consume_raw(
        db_session,
        raw_token=same_chat_raw,
        telegram_chat_id=chat_a,
        now=linked_at + timedelta(seconds=8),
    )
    assert same_chat_result.outcome is TelegramStartTokenConsumeOutcome.RELINKED
    assert same_chat_result.event is not None
    assert same_chat_result.event.action == "relinked"
    assert same_chat_result.link.phone_verified_at == linked_at + timedelta(seconds=8)
    assert count_events(db_session) == 2

    with pytest.raises(TelegramLinkTokenConsumeError) as replay_exc_info:
        consume_raw(
            db_session,
            raw_token=same_chat_raw,
            telegram_chat_id=chat_a,
            now=linked_at + timedelta(seconds=9),
        )
    assert replay_exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert count_events(db_session) == 2

    expired_issue_at = linked_at + timedelta(seconds=10)
    expired_raw = issue_relink_raw(
        db_session,
        settings,
        user,
        raw_token="event_expired_relink_token",
        client_ip="203.0.113.231",
        now=expired_issue_at,
    )
    with pytest.raises(TelegramLinkTokenConsumeError) as expired_exc_info:
        consume_raw(
            db_session,
            raw_token=expired_raw,
            telegram_chat_id=16_006,
            now=expired_issue_at
            + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 1),
        )
    assert expired_exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert count_events(db_session) == 2

    collision_user = add_user(db_session, "+998900016005")
    collision_user_raw = issue_first_raw(
        db_session,
        settings,
        collision_user,
        raw_token="event_collision_owner_token",
        client_ip="203.0.113.232",
        now=expired_issue_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 2),
    )
    consume_raw(
        db_session,
        raw_token=collision_user_raw,
        telegram_chat_id=collision_chat,
        now=expired_issue_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 3),
    )
    count_before_collision = count_events(db_session)
    candidate_result = issue_relink_token(
        db_session,
        settings,
        user,
        ResolvedClientIp("203.0.113.233"),
        expired_issue_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 4),
        token_generator=make_generator("event_collision_candidate_token"),
    )

    with pytest.raises(TelegramChatAlreadyLinkedError) as collision_exc_info:
        consume_raw(
            db_session,
            raw_token=candidate_result.raw_token.as_internal_value(),
            telegram_chat_id=collision_chat,
            now=expired_issue_at
            + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 5),
        )
    db_session.refresh(candidate_result.token)
    assert collision_exc_info.value.error_code is ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
    assert candidate_result.token.consumed_at is None
    assert candidate_result.token.invalidated_at is None
    assert count_events(db_session) == count_before_collision

    unlink_telegram(
        db_session,
        user,
        expired_issue_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 6),
    )
    count_after_unlink = count_events(db_session)
    with pytest.raises(TelegramLinkTokenIssueError) as repeated_unlink_exc_info:
        unlink_telegram(
            db_session,
            user,
            expired_issue_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS + 7),
        )
    assert repeated_unlink_exc_info.value.error_code is ErrorCode.TELEGRAM_NOT_LINKED
    assert count_events(db_session) == count_after_unlink


@pytest.mark.integration
def test_event_schema_has_no_sensitive_payload_and_repository_is_append_only(
    m2_test_database: Engine,
) -> None:
    inspector = sa_inspect(m2_test_database)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("telegram_link_events")
    }
    forbidden_columns = {
        "telegram_chat_id",
        "old_telegram_chat_id",
        "new_telegram_chat_id",
        "old_chat_id",
        "new_chat_id",
        "chat_id",
        "token",
        "token_hash",
        "raw_token",
        "phone",
        "ip",
        "ip_address",
        "client_ip",
        "username",
        "message",
        "update_json",
        "payload",
        "metadata",
        "updated_at",
        "deleted_at",
    }
    public_functions = {
        name
        for name, member in python_inspect.getmembers(
            telegram_events,
            python_inspect.isfunction,
        )
        if not name.startswith("_")
    }
    occurred_at_column = TelegramLinkEvent.__table__.columns["occurred_at"]

    assert set(columns) == {"id", "user_id", "action", "occurred_at"}
    assert forbidden_columns.isdisjoint(columns)
    assert isinstance(occurred_at_column.type, DateTime)
    assert occurred_at_column.type.timezone is True
    assert public_functions == {"append_telegram_link_event"}
