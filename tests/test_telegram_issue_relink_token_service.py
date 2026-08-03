import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkTokenIssueError,
    issue_relink_token_after_rate_limit,
)
from app.telegram.token import (
    TELEGRAM_LINK_TOKEN_ENTROPY_BYTES,
    RawTelegramLinkToken,
    hash_telegram_link_token,
)
from tests.telegram_issue_helpers import (
    issue_relink_token_in_one_test_transaction as issue_relink_token,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-relink-service"
FIRST_RAW_TOKEN = "first_relink_token-123"
SECOND_RAW_TOKEN = "second_relink_token-456"


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

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def begin_nested(self, *args, **kwargs):
        return self.session.begin_nested(*args, **kwargs)

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


def make_settings(
    engine: Engine,
    *,
    user_attempts: int = 3,
    phone_attempts: int = 3,
    ip_attempts: int = 20,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        telegram_link_rate_limit_user_attempts=user_attempts,
        telegram_link_rate_limit_phone_attempts=phone_attempts,
        telegram_link_rate_limit_ip_attempts=ip_attempts,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


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
    token_hash: str,
    created_at: datetime,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=token_hash,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS),
    )
    session.add(token)
    session.flush()
    return token


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def make_generator(raw_values: list[str]):
    calls: list[int] = []

    def token_generator(byte_count: int) -> str:
        calls.append(byte_count)
        return raw_values[len(calls) - 1]

    return token_generator, calls


def stored_relink_domain_text(session: Session) -> str:
    queries = (
        (
            "telegram_link_tokens",
            "SELECT token_hash, created_at::text, expires_at::text, "
            "consumed_at::text, invalidated_at::text FROM telegram_link_tokens",
        ),
        (
            "telegram_links",
            "SELECT telegram_chat_id::text, linked_at::text, "
            "unlinked_at::text, updated_at::text FROM telegram_links",
        ),
        (
            "telegram_link_events",
            "SELECT action, occurred_at::text FROM telegram_link_events",
        ),
        (
            "auth_rate_limits",
            "SELECT scope, key_hash, window_started_at::text, "
            "attempt_count::text, updated_at::text FROM auth_rate_limits",
        ),
    )
    values: list[str] = []
    for table_name, query in queries:
        for row in session.execute(text(query)).all():
            values.append(table_name)
            values.extend(str(value) for value in row)
    return "|".join(values)


def test_issue_relink_token_public_api_has_no_password_or_external_identity() -> None:
    parameters = signature(issue_relink_token_after_rate_limit).parameters

    assert list(parameters) == [
        "session",
        "current_user",
        "now",
        "token_generator",
    ]
    assert "phone" not in parameters
    assert "user_id" not in parameters
    assert "chat_id" not in parameters
    assert "telegram_chat_id" not in parameters
    assert "password" not in parameters
    assert "current_password" not in parameters
    assert "raw_password" not in parameters

    source = getsource(issue_relink_token_after_rate_limit)
    assert "password" not in source
    assert "build_telegram_start_link" not in source
    assert "append_telegram_link_event" not in source
    assert "TelegramLinkEvent" not in source


@pytest.mark.integration
def test_issue_relink_token_active_link_hash_only_600_second_happy_path(
    caplog,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    linked_at = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
    now = linked_at + timedelta(minutes=5)
    user = add_user(db_session, "+998900013001")
    active_link = add_link(
        db_session,
        user,
        telegram_chat_id=13_001,
        linked_at=linked_at,
    )
    original_link_state = (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    )
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN])
    expected_hash = hash_telegram_link_token(RawTelegramLinkToken(FIRST_RAW_TOKEN))
    session_spy = SessionSpy(db_session)
    logger = logging.getLogger("tests.telegram_issue_relink_token_service")

    result = issue_relink_token(
        session_spy,
        settings,
        user,
        ResolvedClientIp("203.0.113.161"),
        now,
        token_generator=generator,
    )
    with caplog.at_level(logging.INFO):
        logger.info("relink issue result %s %r", result, result)
    db_session.refresh(active_link)
    stored_text = stored_relink_domain_text(db_session)

    assert result.raw_token.as_internal_value() == FIRST_RAW_TOKEN
    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert result.token.user_id == user.id
    assert result.token.token_hash == expected_hash
    assert result.token.created_at == now
    assert result.token.expires_at == now + timedelta(
        seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS
    )
    assert result.token.consumed_at is None
    assert result.token.invalidated_at is None
    assert (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    ) == original_link_state
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, AuthRateLimit) == 3
    assert session_spy.commit_called is False
    assert session_spy.rollback_called is False
    assert session_spy.close_called is False
    assert FIRST_RAW_TOKEN not in stored_text
    assert FIRST_RAW_TOKEN not in repr(result)
    assert FIRST_RAW_TOKEN not in caplog.text


@pytest.mark.parametrize("has_tombstone", [False, True])
@pytest.mark.integration
def test_issue_relink_token_rejects_without_active_link_and_preserves_token(
    has_tombstone: bool,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 24, 20, 10, tzinfo=UTC)
    now = created_at + timedelta(minutes=1)
    user = add_user(
        db_session,
        "+998900013002" if has_tombstone else "+998900013003",
    )
    if has_tombstone:
        add_link(
            db_session,
            user,
            telegram_chat_id=None,
            linked_at=created_at - timedelta(minutes=5),
            unlinked_at=created_at - timedelta(minutes=1),
        )
    existing_token = add_token(
        db_session,
        user,
        token_hash="c" * 64 if has_tombstone else "d" * 64,
        created_at=created_at,
    )
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN])

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_relink_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.162"),
            now,
            token_generator=generator,
        )
    db_session.refresh(existing_token)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_NOT_LINKED
    assert exc_info.value.public_error == {
        "code": "TELEGRAM_NOT_LINKED",
        "message": "Telegram akkauntingiz bog'lanmagan.",
    }
    assert generator_calls == []
    assert existing_token.invalidated_at is None
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert FIRST_RAW_TOKEN not in stored_relink_domain_text(db_session)
    assert FIRST_RAW_TOKEN not in error_text
    assert user.phone not in error_text


@pytest.mark.integration
def test_issue_relink_token_reissue_invalidates_old_token_and_preserves_link(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    linked_at = datetime(2026, 7, 24, 20, 20, tzinfo=UTC)
    now = linked_at + timedelta(minutes=6)
    user = add_user(db_session, "+998900013004")
    active_link = add_link(
        db_session,
        user,
        telegram_chat_id=13_004,
        linked_at=linked_at,
    )
    existing_token = add_token(
        db_session,
        user,
        token_hash="e" * 64,
        created_at=linked_at + timedelta(minutes=1),
    )
    original_link_state = (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    )
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([SECOND_RAW_TOKEN])
    expected_new_hash = hash_telegram_link_token(RawTelegramLinkToken(SECOND_RAW_TOKEN))

    result = issue_relink_token(
        db_session,
        settings,
        user,
        ResolvedClientIp("203.0.113.163"),
        now,
        token_generator=generator,
    )
    db_session.refresh(active_link)
    db_session.refresh(existing_token)
    tokens = db_session.scalars(
        select(TelegramLinkToken).order_by(TelegramLinkToken.created_at)
    ).all()

    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert existing_token.invalidated_at == now
    assert result.token.token_hash == expected_new_hash
    assert result.token.created_at == now
    assert result.token.expires_at == now + timedelta(
        seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS
    )
    assert result.token.consumed_at is None
    assert result.token.invalidated_at is None
    assert (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    ) == original_link_state
    assert len(tokens) == 2
    assert (
        sum(
            token.consumed_at is None and token.invalidated_at is None
            for token in tokens
        )
        == 1
    )
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_issue_relink_token_rate_limit_rejection_preserves_link_and_token_state(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    linked_at = datetime(2026, 7, 24, 20, 30, tzinfo=UTC)
    now = linked_at + timedelta(minutes=4)
    user = add_user(db_session, "+998900013005")
    active_link = add_link(
        db_session,
        user,
        telegram_chat_id=13_005,
        linked_at=linked_at,
    )
    settings = make_settings(m2_test_database, user_attempts=1)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN, SECOND_RAW_TOKEN])
    client_ip = ResolvedClientIp("203.0.113.164")
    first_result = issue_relink_token(
        db_session,
        settings,
        user,
        client_ip,
        now,
        token_generator=generator,
    )
    original_link_state = (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    )
    original_token_state = (
        first_result.token.token_hash,
        first_result.token.created_at,
        first_result.token.expires_at,
        first_result.token.consumed_at,
        first_result.token.invalidated_at,
    )

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_relink_token(
            db_session,
            settings,
            user,
            client_ip,
            now + timedelta(seconds=1),
            token_generator=generator,
        )
    db_session.refresh(active_link)
    db_session.refresh(first_result.token)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"

    assert exc_info.value.error_code is ErrorCode.RATE_LIMITED
    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    ) == original_link_state
    assert (
        first_result.token.token_hash,
        first_result.token.created_at,
        first_result.token.expires_at,
        first_result.token.consumed_at,
        first_result.token.invalidated_at,
    ) == original_token_state
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert SECOND_RAW_TOKEN not in stored_relink_domain_text(db_session)
    assert SECOND_RAW_TOKEN not in error_text
    assert user.phone not in error_text
    assert "13005" not in error_text
