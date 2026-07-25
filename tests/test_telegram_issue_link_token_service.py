import logging
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature
from threading import Barrier, BrokenBarrierError

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import app.telegram.repository as telegram_repository
import app.telegram.service as telegram_service
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.rate_limit import hash_rate_limit_key
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.rate_limit import (
    TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
    TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
    TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX,
    TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
    TelegramLinkIssuanceRateLimitResult,
)
from app.telegram.repository import get_telegram_link_token_by_hash_for_update
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkTokenIssueError,
    TelegramLinkTokenIssueInternalError,
    issue_link_token,
)
from app.telegram.token import (
    TELEGRAM_LINK_TOKEN_ENTROPY_BYTES,
    RawTelegramLinkToken,
    hash_telegram_link_token,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-issue-service"
FIRST_RAW_TOKEN = "first_issue_token-123"
SECOND_RAW_TOKEN = "second_issue_token-456"
TTL_RAW_TOKEN = "ttl_issue_token-789"


@dataclass(frozen=True, repr=False)
class ParallelIssueOutcome:
    label: str
    kind: str
    token_hash: str | None = None
    error_code: ErrorCode | None = None
    exception_class: str | None = None
    generated_count: int = 0
    session_usable: bool = False

    def __repr__(self) -> str:
        return (
            "ParallelIssueOutcome("
            f"label={self.label!r}, kind={self.kind!r}, "
            f"error_code={self.error_code}, generated_count={self.generated_count}, "
            f"session_usable={self.session_usable}, "
            f"exception_class={self.exception_class!r}"
            ")"
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


def add_active_link(session: Session, user: User, now: datetime) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=900_001,
        linked_at=now,
        updated_at=now,
    )
    session.add(link)
    session.flush()
    return link


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


def stored_token_text(session: Session) -> str:
    rows = session.execute(
        text(
            "SELECT token_hash, created_at::text, expires_at::text, "
            "consumed_at::text, invalidated_at::text FROM telegram_link_tokens"
        )
    ).all()
    return "|".join(str(value) for row in rows for value in row)


def stored_issue_domain_text(session: Session) -> str:
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


def get_rate_limit_record(
    session: Session,
    settings: Settings,
    *,
    scope: str,
    raw_key: str,
) -> AuthRateLimit:
    record = session.scalar(
        select(AuthRateLimit).where(
            AuthRateLimit.scope == scope,
            AuthRateLimit.key_hash == hash_rate_limit_key(settings, raw_key),
        )
    )
    assert record is not None
    return record


def assert_sensitive_value_absent(value: str, text_value: str) -> None:
    if value in text_value:
        raise AssertionError("sensitive value leaked")


def make_generator(raw_values: list[str]):
    calls: list[int] = []

    def token_generator(byte_count: int) -> str:
        calls.append(byte_count)
        return raw_values[len(calls) - 1]

    return token_generator, calls


def test_issue_link_token_public_api_has_no_caller_supplied_phone_or_user_id() -> None:
    parameters = signature(issue_link_token).parameters
    assert "phone" not in parameters
    assert "user_id" not in parameters
    assert "raw_token" not in parameters

    issue_source = getsource(issue_link_token)
    assert "build_telegram_start_link" not in issue_source
    assert "append_telegram_link_event" not in issue_source
    assert "TelegramLinkEvent" not in issue_source
    assert "Customer" not in issue_source


@pytest.mark.integration
def test_issue_link_token_rejects_phone_and_user_id_keyword_bypass(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 16, 8, tzinfo=UTC)
    user = add_user(db_session, "+998900007201")
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN])

    with pytest.raises(TypeError):
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.91"),
            now,
            token_generator=generator,
            phone="+998900007999",
        )
    with pytest.raises(TypeError):
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.91"),
            now,
            token_generator=generator,
            user_id=user.id,
        )

    assert generator_calls == []
    assert count_table(db_session, AuthRateLimit) == 0
    assert count_table(db_session, TelegramLinkToken) == 0


@pytest.mark.integration
def test_issue_link_token_rate_limit_uses_db_canonical_phone_not_stale_object_phone(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 16, 9, tzinfo=UTC)
    user = add_user(db_session, "+998900007202")
    settings = make_settings(
        m2_test_database,
        user_attempts=10,
        phone_attempts=1,
        ip_attempts=20,
    )
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN, SECOND_RAW_TOKEN])

    first = issue_link_token(
        db_session,
        settings,
        User(id=user.id, phone="+998900007991"),
        ResolvedClientIp("203.0.113.92"),
        now,
        token_generator=generator,
    )
    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            User(id=user.id, phone="+998900007992"),
            ResolvedClientIp("203.0.113.93"),
            now + timedelta(seconds=1),
            token_generator=generator,
        )
    db_session.refresh(first.token)
    phone_records = db_session.scalars(
        select(AuthRateLimit).where(
            AuthRateLimit.scope == TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE
        )
    ).all()
    stored_rate_limit_text = "|".join(
        str(value)
        for row in db_session.execute(
            text("SELECT scope, key_hash, attempt_count::text FROM auth_rate_limits")
        ).all()
        for value in row
    )

    assert exc_info.value.error_code is ErrorCode.RATE_LIMITED
    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert first.token.invalidated_at is None
    assert len(phone_records) == 1
    assert phone_records[0].key_hash == hash_rate_limit_key(
        settings,
        f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}{user.phone}",
    )
    assert hash_rate_limit_key(
        settings,
        f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}+998900007991",
    ) != phone_records[0].key_hash
    assert hash_rate_limit_key(
        settings,
        f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}+998900007992",
    ) != phone_records[0].key_hash
    assert "+998900007991" not in stored_rate_limit_text
    assert "+998900007992" not in stored_rate_limit_text
    assert count_table(db_session, TelegramLinkToken) == 1


@pytest.mark.integration
def test_issue_link_token_first_issue_hash_only_600_second_happy_path(
    caplog,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 16, 10, tzinfo=UTC)
    user = add_user(db_session, "+998900007001")
    stale_current_user = User(id=user.id, phone="+998900007999")
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN])
    client_ip = ResolvedClientIp("::ffff:203.0.113.71")
    expected_hash = hash_telegram_link_token(RawTelegramLinkToken(FIRST_RAW_TOKEN))
    logger = logging.getLogger("tests.telegram_issue_link_token_service")

    result = issue_link_token(
        db_session,
        settings,
        stale_current_user,
        client_ip,
        now,
        token_generator=generator,
    )
    with caplog.at_level(logging.INFO):
        logger.info("issued token result %s %r", result, result)
    token_rows = db_session.scalars(select(TelegramLinkToken)).all()
    phone_rate_limit = db_session.scalar(
        select(AuthRateLimit).where(
            AuthRateLimit.scope == TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE
        )
    )
    token_storage_text = stored_token_text(db_session)

    assert result.raw_token.as_internal_value() == FIRST_RAW_TOKEN
    assert result.token is token_rows[0]
    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert len(token_rows) == 1
    assert token_rows[0].user_id == user.id
    assert token_rows[0].token_hash == expected_hash
    assert token_rows[0].created_at == now
    assert token_rows[0].expires_at == now + timedelta(
        seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS
    )
    assert token_rows[0].consumed_at is None
    assert token_rows[0].invalidated_at is None
    assert FIRST_RAW_TOKEN not in token_storage_text
    assert FIRST_RAW_TOKEN not in repr(result)
    assert FIRST_RAW_TOKEN not in caplog.text
    assert phone_rate_limit is not None
    assert phone_rate_limit.key_hash == hash_rate_limit_key(
        settings,
        f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}{user.phone}",
    )
    assert stale_current_user.phone not in token_storage_text
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, Customer) == 0


@pytest.mark.integration
def test_issue_link_token_exact_ttl_hash_only_and_bot_username_not_required(
    monkeypatch,
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)
    now = datetime(2026, 7, 24, 16, 12, tzinfo=UTC)
    user = add_user(db_session, "+998900007501")
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([TTL_RAW_TOKEN])
    expected_hash = hash_telegram_link_token(RawTelegramLinkToken(TTL_RAW_TOKEN))

    result = issue_link_token(
        db_session,
        settings,
        user,
        ResolvedClientIp("203.0.113.151"),
        now,
        token_generator=generator,
    )
    token_row = db_session.scalar(select(TelegramLinkToken))
    stored_text = stored_issue_domain_text(db_session)
    deep_link = f"https://t.me/examplebot?start={TTL_RAW_TOKEN}"

    assert settings.telegram_bot_username is None
    assert result.raw_token.as_internal_value() == TTL_RAW_TOKEN
    assert token_row is result.token
    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert token_row.token_hash == expected_hash
    assert token_row.created_at == now
    assert token_row.expires_at == now + timedelta(
        seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS
    )
    assert token_row.consumed_at is None
    assert token_row.invalidated_at is None
    assert TTL_RAW_TOKEN not in stored_text
    assert deep_link not in stored_text
    assert "https://t.me" not in stored_text
    assert "?start=" not in stored_text
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, AuthRateLimit) == 3


@pytest.mark.integration
def test_issue_link_token_already_linked_returns_code_without_token_change(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 16, 15, tzinfo=UTC)
    user = add_user(db_session, "+998900007002")
    add_active_link(db_session, user, now)
    existing = add_token(
        db_session,
        user,
        token_hash="a" * 64,
        created_at=now - timedelta(minutes=1),
    )
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN])

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.72"),
            now,
            token_generator=generator,
        )
    db_session.refresh(existing)

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_ALREADY_LINKED
    assert exc_info.value.public_error == {
        "code": "TELEGRAM_ALREADY_LINKED",
        "message": "Telegram akkauntingiz allaqachon bog'langan.",
    }
    assert generator_calls == []
    assert existing.invalidated_at is None
    assert count_table(db_session, TelegramLinkToken) == 1


@pytest.mark.integration
def test_issue_link_token_active_link_rejection_preserves_link_token_and_events(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 16, 16, tzinfo=UTC)
    user = add_user(db_session, "+998900007203")
    active_link = add_active_link(db_session, user, now)
    existing_token = add_token(
        db_session,
        user,
        token_hash="b" * 64,
        created_at=now - timedelta(minutes=1),
    )
    existing_event = add_event(
        db_session,
        user,
        action="linked",
        occurred_at=now - timedelta(minutes=2),
    )
    original_link_state = (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    )
    original_token_state = (
        existing_token.token_hash,
        existing_token.created_at,
        existing_token.expires_at,
        existing_token.consumed_at,
        existing_token.invalidated_at,
    )
    original_event_state = (
        existing_event.action,
        existing_event.occurred_at,
    )
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN])

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.94"),
            now,
            token_generator=generator,
        )
    db_session.refresh(active_link)
    db_session.refresh(existing_token)
    db_session.refresh(existing_event)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"

    assert exc_info.value.error_code is ErrorCode.TELEGRAM_ALREADY_LINKED
    assert generator_calls == []
    assert (
        active_link.telegram_chat_id,
        active_link.linked_at,
        active_link.unlinked_at,
        active_link.updated_at,
    ) == original_link_state
    assert (
        existing_token.token_hash,
        existing_token.created_at,
        existing_token.expires_at,
        existing_token.consumed_at,
        existing_token.invalidated_at,
    ) == original_token_state
    assert (
        existing_event.action,
        existing_event.occurred_at,
    ) == original_event_state
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, TelegramLinkEvent) == 1
    assert hash_telegram_link_token(RawTelegramLinkToken(FIRST_RAW_TOKEN)) not in {
        existing_token.token_hash
    }
    assert FIRST_RAW_TOKEN not in stored_token_text(db_session)
    assert FIRST_RAW_TOKEN not in error_text
    assert str(active_link.telegram_chat_id) not in error_text
    assert user.phone not in error_text
    assert "telegram_links" not in error_text
    assert "telegram_link_tokens" not in error_text
    assert "telegram_link_events" not in error_text


@pytest.mark.integration
def test_issue_link_token_rate_limited_before_invalidating_old_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 16, 20, tzinfo=UTC)
    user = add_user(db_session, "+998900007003")
    settings = make_settings(m2_test_database, user_attempts=1)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN, SECOND_RAW_TOKEN])
    client_ip = ResolvedClientIp("203.0.113.73")
    first_result = issue_link_token(
        db_session,
        settings,
        user,
        client_ip,
        now,
        token_generator=generator,
    )

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            client_ip,
            now + timedelta(seconds=1),
            token_generator=generator,
        )
    db_session.refresh(first_result.token)

    assert exc_info.value.error_code is ErrorCode.RATE_LIMITED
    assert SECOND_RAW_TOKEN not in str(exc_info.value)
    assert generator_calls == [
        TELEGRAM_LINK_TOKEN_ENTROPY_BYTES,
    ]
    assert first_result.token.invalidated_at is None
    assert count_table(db_session, TelegramLinkToken) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    (
        "bucket_label",
        "settings_kwargs",
        "user_phone",
        "client_ip",
        "usable_phone",
    ),
    [
        (
            "user",
            {"user_attempts": 1, "phone_attempts": 10, "ip_attempts": 20},
            "+998900007301",
            "203.0.113.101",
            "+998900007401",
        ),
        (
            "phone",
            {"user_attempts": 10, "phone_attempts": 1, "ip_attempts": 20},
            "+998900007302",
            "203.0.113.102",
            "+998900007402",
        ),
        (
            "ip",
            {"user_attempts": 10, "phone_attempts": 10, "ip_attempts": 1},
            "+998900007303",
            "203.0.113.103",
            "+998900007403",
        ),
    ],
    ids=["user-bucket", "phone-bucket", "ip-bucket"],
)
def test_issue_link_token_rate_limit_rejection_preserves_existing_token_state(
    caplog,
    m2_test_database: Engine,
    db_session: Session,
    bucket_label: str,
    settings_kwargs: dict[str, int],
    user_phone: str,
    client_ip: str,
    usable_phone: str,
) -> None:
    first_now = datetime(2026, 7, 24, 16, 22, tzinfo=UTC)
    second_now = first_now + timedelta(seconds=1)
    user = add_user(db_session, user_phone)
    settings = make_settings(m2_test_database, **settings_kwargs)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN, SECOND_RAW_TOKEN])
    resolved_client_ip = ResolvedClientIp(client_ip)
    first_hash = hash_telegram_link_token(RawTelegramLinkToken(FIRST_RAW_TOKEN))
    second_hash = hash_telegram_link_token(RawTelegramLinkToken(SECOND_RAW_TOKEN))
    logger = logging.getLogger("tests.telegram_rate_limit_rejection")

    first_result = issue_link_token(
        db_session,
        settings,
        user,
        resolved_client_ip,
        first_now,
        token_generator=generator,
    )
    original_token_state = (
        first_result.token.token_hash,
        first_result.token.created_at,
        first_result.token.expires_at,
        first_result.token.consumed_at,
        first_result.token.invalidated_at,
    )
    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            resolved_client_ip,
            second_now,
            token_generator=generator,
        )
    db_session.refresh(first_result.token)
    with caplog.at_level(logging.INFO):
        logger.info(
            "blocked issue result %s %r %s",
            exc_info.value,
            exc_info.value,
            exc_info.value.public_error,
        )
    token_rows = db_session.scalars(select(TelegramLinkToken)).all()
    old_token = get_telegram_link_token_by_hash_for_update(db_session, first_hash)
    new_token = get_telegram_link_token_by_hash_for_update(db_session, second_hash)
    stored_text = stored_token_text(db_session)
    usable_user = add_user(db_session, usable_phone)
    error_and_log_text = (
        f"{exc_info.value!r} {exc_info.value} "
        f"{exc_info.value.public_error} {caplog.text}"
    )

    assert bucket_label not in error_and_log_text.casefold()
    assert exc_info.value.error_code is ErrorCode.RATE_LIMITED
    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert (
        first_result.token.token_hash,
        first_result.token.created_at,
        first_result.token.expires_at,
        first_result.token.consumed_at,
        first_result.token.invalidated_at,
    ) == original_token_state
    assert old_token is first_result.token
    assert new_token is None
    assert len(token_rows) == 1
    assert FIRST_RAW_TOKEN not in stored_text
    assert SECOND_RAW_TOKEN not in stored_text
    assert user.phone not in error_and_log_text
    assert resolved_client_ip.as_hmac_input() not in error_and_log_text
    assert FIRST_RAW_TOKEN not in error_and_log_text
    assert SECOND_RAW_TOKEN not in error_and_log_text
    assert TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE not in error_and_log_text
    assert TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE not in error_and_log_text
    assert TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE not in error_and_log_text
    assert "user" not in error_and_log_text.casefold()
    assert "phone" not in error_and_log_text.casefold()
    assert "ip" not in error_and_log_text.casefold()
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert db_session.get(User, usable_user.id) is usable_user


@pytest.mark.integration
def test_issue_link_token_timezone_naive_now_fails_fast_without_state_changes(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    naive_now = datetime(2026, 7, 24, 16, 23)
    user = add_user(db_session, "+998900007502")
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([TTL_RAW_TOKEN])

    with pytest.raises(ValueError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.152"),
            naive_now,
            token_generator=generator,
        )

    assert "timezone-aware" in str(exc_info.value)
    assert TTL_RAW_TOKEN not in str(exc_info.value)
    assert generator_calls == []
    assert count_table(db_session, AuthRateLimit) == 0
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_issue_link_token_sequential_reissue_invalidates_old_token_only(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    first_now = datetime(2026, 7, 24, 16, 24, tzinfo=UTC)
    second_now = first_now + timedelta(seconds=30)
    user = add_user(db_session, "+998900007104")
    settings = make_settings(m2_test_database)
    generator, generator_calls = make_generator([FIRST_RAW_TOKEN, SECOND_RAW_TOKEN])
    client_ip = ResolvedClientIp("203.0.113.84")
    first_hash = hash_telegram_link_token(RawTelegramLinkToken(FIRST_RAW_TOKEN))
    second_hash = hash_telegram_link_token(RawTelegramLinkToken(SECOND_RAW_TOKEN))

    first_result = issue_link_token(
        db_session,
        settings,
        user,
        client_ip,
        first_now,
        token_generator=generator,
    )
    first_created_at = first_result.token.created_at
    first_expires_at = first_result.token.expires_at
    second_result = issue_link_token(
        db_session,
        settings,
        user,
        client_ip,
        second_now,
        token_generator=generator,
    )
    token_rows = db_session.scalars(
        select(TelegramLinkToken).order_by(TelegramLinkToken.created_at)
    ).all()
    old_token = get_telegram_link_token_by_hash_for_update(db_session, first_hash)
    new_token = get_telegram_link_token_by_hash_for_update(db_session, second_hash)
    outstanding_rows = [
        token
        for token in token_rows
        if token.consumed_at is None and token.invalidated_at is None
    ]
    token_storage_text = stored_token_text(db_session)
    issue_domain_text = stored_issue_domain_text(db_session)
    invalid_token_body = telegram_service.get_public_error_body(
        ErrorCode.LINK_TOKEN_INVALID,
        internal_detail=FIRST_RAW_TOKEN,
    )

    assert first_result.raw_token.as_internal_value() == FIRST_RAW_TOKEN
    assert second_result.raw_token.as_internal_value() == SECOND_RAW_TOKEN
    assert generator_calls == [
        TELEGRAM_LINK_TOKEN_ENTROPY_BYTES,
        TELEGRAM_LINK_TOKEN_ENTROPY_BYTES,
    ]
    assert len(token_rows) == 2
    assert old_token is first_result.token
    assert new_token is second_result.token
    assert old_token.invalidated_at == second_now
    assert old_token.created_at == first_created_at
    assert old_token.expires_at == first_expires_at
    assert new_token.token_hash == second_hash
    assert new_token.created_at == second_now
    assert new_token.expires_at == second_now + timedelta(
        seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS
    )
    assert outstanding_rows == [new_token]
    assert FIRST_RAW_TOKEN not in token_storage_text
    assert SECOND_RAW_TOKEN not in token_storage_text
    assert FIRST_RAW_TOKEN not in issue_domain_text
    assert SECOND_RAW_TOKEN not in issue_domain_text
    assert "https://t.me" not in issue_domain_text
    assert "?start=" not in issue_domain_text
    assert invalid_token_body["code"] == "LINK_TOKEN_INVALID"
    assert FIRST_RAW_TOKEN not in str(invalid_token_body)
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, Customer) == 0


@pytest.mark.integration
def test_issue_link_token_parallel_issue_keeps_one_outstanding_without_500(
    caplog,
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    try:
        user = add_user(setup_session, "+998900007601")
        user_id = user.id
        setup_session.commit()
    finally:
        setup_session.close()

    settings = make_settings(
        m2_test_database,
        user_attempts=10,
        phone_attempts=10,
        ip_attempts=20,
    )
    now = datetime(2026, 7, 24, 16, 26, tzinfo=UTC)
    client_ip = ResolvedClientIp("203.0.113.161")
    raw_by_label = {
        "first": "parallel_issue_token_one",
        "second": "parallel_issue_token_two",
    }
    attempted_hashes = {
        label: hash_telegram_link_token(RawTelegramLinkToken(raw_token))
        for label, raw_token in raw_by_label.items()
    }
    start_barrier = Barrier(2)
    empty_outstanding_barrier = Barrier(2)
    original_get_outstanding = (
        telegram_repository.get_outstanding_telegram_link_token_for_update
    )
    logger = logging.getLogger("tests.telegram_parallel_issue")

    def allow_rate_limit(*_args, **_kwargs) -> TelegramLinkIssuanceRateLimitResult:
        return TelegramLinkIssuanceRateLimitResult(allowed=True)

    def synchronized_get_outstanding(
        session: Session,
        current_user: User,
    ) -> TelegramLinkToken | None:
        token = original_get_outstanding(session, current_user)
        if token is None:
            empty_outstanding_barrier.wait(timeout=5)
        return token

    def worker(label: str) -> ParallelIssueOutcome:
        session = session_factory()
        generator_calls: list[int] = []
        try:
            start_barrier.wait(timeout=5)
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            if current_user is None:
                return ParallelIssueOutcome(
                    label=label,
                    kind="unexpected",
                    exception_class="MissingUser",
                )

            def token_generator(byte_count: int) -> str:
                generator_calls.append(byte_count)
                return raw_by_label[label]

            try:
                issued = issue_link_token(
                    session,
                    settings,
                    current_user,
                    client_ip,
                    now,
                    token_generator=token_generator,
                )
            except TelegramLinkTokenIssueError as exc:
                session_usable = (
                    session.scalar(select(func.count()).select_from(User)) or 0
                ) >= 1
                session.commit()
                return ParallelIssueOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=exc.error_code,
                    generated_count=len(generator_calls),
                    session_usable=session_usable,
                )

            token_hash = hash_telegram_link_token(issued.raw_token)
            session_usable = (
                session.scalar(select(func.count()).select_from(User)) or 0
            ) >= 1
            session.commit()
            return ParallelIssueOutcome(
                label=label,
                kind="issued",
                token_hash=token_hash,
                generated_count=len(generator_calls),
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelIssueOutcome(
                label=label,
                kind="unexpected",
                exception_class="BrokenBarrierError",
                generated_count=len(generator_calls),
            )
        except Exception as exc:
            session.rollback()
            return ParallelIssueOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
                generated_count=len(generator_calls),
            )
        finally:
            session.close()

    monkeypatch.setattr(
        telegram_service,
        "record_telegram_link_issuance_attempt",
        allow_rate_limit,
    )
    monkeypatch.setattr(
        telegram_repository,
        "get_outstanding_telegram_link_token_for_update",
        synchronized_get_outstanding,
    )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [executor.submit(worker, label) for label in ("first", "second")]
        done, not_done = wait(futures, timeout=15)
        if not_done:
            for future in not_done:
                future.cancel()
            pytest.fail("parallel issue timed out")
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    with caplog.at_level(logging.INFO):
        logger.info("parallel issue outcomes %s", outcomes)

    final_session = session_factory()
    try:
        token_rows = final_session.scalars(
            select(TelegramLinkToken).where(TelegramLinkToken.user_id == user_id)
        ).all()
        outstanding_rows = [
            token
            for token in token_rows
            if token.consumed_at is None and token.invalidated_at is None
        ]
        stored_text = stored_issue_domain_text(final_session)
        link_count = count_table(final_session, TelegramLink)
        event_count = count_table(final_session, TelegramLinkEvent)
    finally:
        final_session.close()

    unexpected_outcomes = [
        outcome for outcome in outcomes if outcome.kind == "unexpected"
    ]
    issued_outcomes = [outcome for outcome in outcomes if outcome.kind == "issued"]
    domain_outcomes = [
        outcome for outcome in outcomes if outcome.kind == "domain_error"
    ]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)
    non_outstanding_hashes = set(attempted_hashes.values())

    assert unexpected_outcomes == []
    assert len(issued_outcomes) == 1
    assert len(domain_outcomes) == 1
    assert domain_outcomes[0].error_code is ErrorCode.RATE_LIMITED
    assert all(outcome.session_usable for outcome in outcomes)
    assert all(
        outcome.generated_count == 1
        for outcome in outcomes
        if outcome.kind in {"issued", "domain_error"}
    )
    assert len(token_rows) == 1
    assert len(outstanding_rows) == 1
    assert outstanding_rows[0].token_hash == issued_outcomes[0].token_hash
    non_outstanding_hashes.discard(outstanding_rows[0].token_hash)
    assert all(token.token_hash not in non_outstanding_hashes for token in token_rows)
    assert link_count == 0
    assert event_count == 0
    assert "IntegrityError" not in outcome_text
    assert "IntegrityError" not in caplog.text
    for raw_token in raw_by_label.values():
        assert_sensitive_value_absent(raw_token, stored_text)
        assert_sensitive_value_absent(raw_token, outcome_text)
        assert_sensitive_value_absent(raw_token, caplog.text)


@pytest.mark.integration
def test_issue_link_token_parallel_four_same_user_keeps_rate_limit_ceiling(
    caplog,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    phone = "+998900007701"
    setup_session = session_factory()
    try:
        user = add_user(setup_session, phone)
        user_id = user.id
        setup_session.commit()
    finally:
        setup_session.close()

    settings = make_settings(m2_test_database)
    now = datetime(2026, 7, 24, 16, 31, tzinfo=UTC)
    client_ip = ResolvedClientIp("203.0.113.171")
    raw_by_label = {
        f"attempt_{index}": f"parallel_rate_limited_issue_token_{index}"
        for index in range(4)
    }
    start_barrier = Barrier(len(raw_by_label))
    logger = logging.getLogger("tests.telegram_parallel_issue_rate_ceiling")

    user_raw_key = f"{TELEGRAM_LINK_RATE_LIMIT_USER_KEY_PREFIX}{user_id}"
    phone_raw_key = f"{TELEGRAM_LINK_RATE_LIMIT_PHONE_KEY_PREFIX}{phone}"
    ip_raw_key = (
        f"{TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX}{client_ip.as_hmac_input()}"
    )
    sensitive_values = (
        *raw_by_label.values(),
        phone,
        str(user_id),
        client_ip.as_hmac_input(),
        user_raw_key,
        phone_raw_key,
        ip_raw_key,
    )

    def worker(label: str) -> ParallelIssueOutcome:
        session = session_factory()
        generator_calls: list[int] = []
        try:
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id)
            if current_user is None:
                return ParallelIssueOutcome(
                    label=label,
                    kind="unexpected",
                    exception_class="MissingUser",
                )
            start_barrier.wait(timeout=5)

            def token_generator(byte_count: int) -> str:
                generator_calls.append(byte_count)
                return raw_by_label[label]

            try:
                issued = issue_link_token(
                    session,
                    settings,
                    current_user,
                    client_ip,
                    now,
                    token_generator=token_generator,
                )
            except TelegramLinkTokenIssueError as exc:
                error_text = f"{exc!r} {exc} {exc.public_error}"
                for value in sensitive_values:
                    assert_sensitive_value_absent(value, error_text)
                session_usable = (
                    session.scalar(select(func.count()).select_from(User)) or 0
                ) >= 1
                session.commit()
                return ParallelIssueOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=exc.error_code,
                    generated_count=len(generator_calls),
                    session_usable=session_usable,
                )

            token_hash = hash_telegram_link_token(issued.raw_token)
            session_usable = (
                session.scalar(select(func.count()).select_from(User)) or 0
            ) >= 1
            session.commit()
            return ParallelIssueOutcome(
                label=label,
                kind="issued",
                token_hash=token_hash,
                generated_count=len(generator_calls),
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelIssueOutcome(
                label=label,
                kind="unexpected",
                exception_class="BrokenBarrierError",
                generated_count=len(generator_calls),
            )
        except Exception as exc:
            session.rollback()
            return ParallelIssueOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
                generated_count=len(generator_calls),
            )
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=len(raw_by_label))
    try:
        with caplog.at_level(logging.INFO):
            futures = [
                executor.submit(worker, label) for label in raw_by_label
            ]
            done, not_done = wait(futures, timeout=15)
            if not_done:
                start_barrier.abort()
                for future in not_done:
                    future.cancel()
                pytest.fail("parallel issue rate ceiling timed out", pytrace=False)
            outcomes = [future.result(timeout=0) for future in futures]
            logger.info("parallel issue rate ceiling outcomes %s", outcomes)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    caplog.clear()
    unexpected_outcomes = [
        outcome for outcome in outcomes if outcome.kind == "unexpected"
    ]
    issued_outcomes = [outcome for outcome in outcomes if outcome.kind == "issued"]
    domain_outcomes = [
        outcome for outcome in outcomes if outcome.kind == "domain_error"
    ]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)

    final_session = session_factory()
    try:
        token_rows = final_session.scalars(
            select(TelegramLinkToken).where(TelegramLinkToken.user_id == user_id)
        ).all()
        outstanding_rows = [
            token
            for token in token_rows
            if token.consumed_at is None and token.invalidated_at is None
        ]
        user_record = get_rate_limit_record(
            final_session,
            settings,
            scope=TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
            raw_key=user_raw_key,
        )
        phone_record = get_rate_limit_record(
            final_session,
            settings,
            scope=TELEGRAM_LINK_RATE_LIMIT_PHONE_SCOPE,
            raw_key=phone_raw_key,
        )
        ip_record = get_rate_limit_record(
            final_session,
            settings,
            scope=TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
            raw_key=ip_raw_key,
        )
        stored_text = stored_issue_domain_text(final_session)
        link_count = count_table(final_session, TelegramLink)
        event_count = count_table(final_session, TelegramLinkEvent)
        customer_count = count_table(final_session, Customer)
        final_session_usable = (
            final_session.scalar(select(func.count()).select_from(User)) or 0
        ) >= 1
        final_outstanding_id = outstanding_rows[0].id if outstanding_rows else None
        final_outstanding_hash = (
            outstanding_rows[0].token_hash if outstanding_rows else None
        )
    finally:
        final_session.close()

    assert unexpected_outcomes == []
    assert len(issued_outcomes) >= 1
    assert len(issued_outcomes) <= 3
    assert len(issued_outcomes) + len(domain_outcomes) == 4
    assert all(
        outcome.error_code is ErrorCode.RATE_LIMITED
        for outcome in domain_outcomes
    )
    assert all(outcome.session_usable for outcome in outcomes)
    assert sum(outcome.generated_count for outcome in outcomes) <= 3
    assert len(token_rows) <= 3
    assert len(outstanding_rows) == 1
    assert outstanding_rows[0].consumed_at is None
    assert outstanding_rows[0].invalidated_at is None
    assert user_record.attempt_count == 4
    assert phone_record.attempt_count == 4
    assert ip_record.attempt_count == 4
    assert link_count == 0
    assert event_count == 0
    assert customer_count == 0
    assert final_session_usable is True
    assert "IntegrityError" not in outcome_text
    assert "IntegrityError" not in captured_log
    for value in sensitive_values:
        assert_sensitive_value_absent(value, stored_text)
        assert_sensitive_value_absent(value, outcome_text)
        assert_sensitive_value_absent(value, captured_log)

    rejection_raw = "parallel_rate_limit_rejection_token"
    rejection_session = session_factory()
    try:
        current_user = rejection_session.get(User, user_id)
        assert current_user is not None
        with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
            issue_link_token(
                rejection_session,
                settings,
                current_user,
                client_ip,
                now + timedelta(seconds=2),
                token_generator=make_generator([rejection_raw])[0],
            )
        rejection_error_text = (
            f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
        )
        rejection_session_usable = (
            rejection_session.scalar(select(func.count()).select_from(User)) or 0
        ) >= 1
        rejection_session.commit()
    finally:
        rejection_session.close()

    verify_session = session_factory()
    try:
        current_outstanding = verify_session.get(
            TelegramLinkToken,
            final_outstanding_id,
        )
        user_record_after_rejection = get_rate_limit_record(
            verify_session,
            settings,
            scope=TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
            raw_key=user_raw_key,
        )
    finally:
        verify_session.close()

    assert exc_info.value.error_code is ErrorCode.RATE_LIMITED
    assert rejection_session_usable is True
    assert current_outstanding is not None
    assert current_outstanding.token_hash == final_outstanding_hash
    assert current_outstanding.consumed_at is None
    assert current_outstanding.invalidated_at is None
    assert user_record_after_rejection.attempt_count == 4
    for value in (*sensitive_values, rejection_raw):
        assert_sensitive_value_absent(value, rejection_error_text)


@pytest.mark.integration
def test_issue_link_token_parallel_shared_ip_respects_twenty_ceiling(
    caplog,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    labels = [f"worker_{index:02d}" for index in range(21)]
    phone_by_label = {
        label: f"+9989000078{index:02d}" for index, label in enumerate(labels)
    }
    raw_by_label = {
        label: f"parallel_shared_ip_issue_token_{index:02d}"
        for index, label in enumerate(labels)
    }
    setup_session = session_factory()
    try:
        user_id_by_label = {}
        for label in labels:
            user = add_user(setup_session, phone_by_label[label])
            user_id_by_label[label] = user.id
        setup_session.commit()
    finally:
        setup_session.close()

    settings = make_settings(
        m2_test_database,
        user_attempts=25,
        phone_attempts=25,
        ip_attempts=20,
    )
    now = datetime(2026, 7, 24, 16, 32, tzinfo=UTC)
    client_ip = ResolvedClientIp("203.0.113.172")
    ip_raw_key = (
        f"{TELEGRAM_LINK_RATE_LIMIT_IP_KEY_PREFIX}{client_ip.as_hmac_input()}"
    )
    start_barrier = Barrier(len(labels))
    logger = logging.getLogger("tests.telegram_parallel_shared_ip")
    sensitive_values = (
        *raw_by_label.values(),
        *phone_by_label.values(),
        *(str(user_id) for user_id in user_id_by_label.values()),
        client_ip.as_hmac_input(),
        ip_raw_key,
    )

    def worker(label: str) -> ParallelIssueOutcome:
        session = session_factory()
        generator_calls: list[int] = []
        try:
            start_barrier.wait(timeout=5)
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            current_user = session.get(User, user_id_by_label[label])
            if current_user is None:
                return ParallelIssueOutcome(
                    label=label,
                    kind="unexpected",
                    exception_class="MissingUser",
                )

            def token_generator(byte_count: int) -> str:
                generator_calls.append(byte_count)
                return raw_by_label[label]

            try:
                issued = issue_link_token(
                    session,
                    settings,
                    current_user,
                    client_ip,
                    now,
                    token_generator=token_generator,
                )
            except TelegramLinkTokenIssueError as exc:
                error_text = f"{exc!r} {exc} {exc.public_error}"
                for value in sensitive_values:
                    assert_sensitive_value_absent(value, error_text)
                session_usable = (
                    session.scalar(select(func.count()).select_from(User)) or 0
                ) >= 1
                session.commit()
                return ParallelIssueOutcome(
                    label=label,
                    kind="domain_error",
                    error_code=exc.error_code,
                    generated_count=len(generator_calls),
                    session_usable=session_usable,
                )

            token_hash = hash_telegram_link_token(issued.raw_token)
            session_usable = (
                session.scalar(select(func.count()).select_from(User)) or 0
            ) >= 1
            session.commit()
            return ParallelIssueOutcome(
                label=label,
                kind="issued",
                token_hash=token_hash,
                generated_count=len(generator_calls),
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelIssueOutcome(
                label=label,
                kind="unexpected",
                exception_class="BrokenBarrierError",
                generated_count=len(generator_calls),
            )
        except Exception as exc:
            session.rollback()
            return ParallelIssueOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
                generated_count=len(generator_calls),
            )
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=len(labels))
    try:
        with caplog.at_level(logging.INFO):
            futures = [executor.submit(worker, label) for label in labels]
            done, not_done = wait(futures, timeout=20)
            if not_done:
                start_barrier.abort()
                for future in not_done:
                    future.cancel()
                pytest.fail("parallel shared-IP issue timed out", pytrace=False)
            outcomes = [future.result(timeout=0) for future in futures]
            logger.info("parallel shared-IP issue outcomes %s", outcomes)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    caplog.clear()
    unexpected_outcomes = [
        outcome for outcome in outcomes if outcome.kind == "unexpected"
    ]
    issued_outcomes = [outcome for outcome in outcomes if outcome.kind == "issued"]
    domain_outcomes = [
        outcome for outcome in outcomes if outcome.kind == "domain_error"
    ]
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)

    final_session = session_factory()
    try:
        token_rows = final_session.scalars(select(TelegramLinkToken)).all()
        outstanding_rows = [
            token
            for token in token_rows
            if token.consumed_at is None and token.invalidated_at is None
        ]
        ip_record = get_rate_limit_record(
            final_session,
            settings,
            scope=TELEGRAM_LINK_RATE_LIMIT_IP_SCOPE,
            raw_key=ip_raw_key,
        )
        stored_text = stored_issue_domain_text(final_session)
        link_count = count_table(final_session, TelegramLink)
        event_count = count_table(final_session, TelegramLinkEvent)
        customer_count = count_table(final_session, Customer)
        final_session_usable = (
            final_session.scalar(select(func.count()).select_from(User)) or 0
        ) >= len(labels)
    finally:
        final_session.close()

    assert unexpected_outcomes == []
    assert len(issued_outcomes) == 20
    assert len(domain_outcomes) == 1
    assert domain_outcomes[0].error_code is ErrorCode.RATE_LIMITED
    assert all(outcome.session_usable for outcome in outcomes)
    assert all(outcome.generated_count == 1 for outcome in issued_outcomes)
    assert all(outcome.generated_count == 0 for outcome in domain_outcomes)
    assert len(token_rows) == 20
    assert len(outstanding_rows) == 20
    assert ip_record.attempt_count == 21
    assert link_count == 0
    assert event_count == 0
    assert customer_count == 0
    assert final_session_usable is True
    assert "IntegrityError" not in outcome_text
    assert "IntegrityError" not in captured_log
    for value in sensitive_values:
        assert_sensitive_value_absent(value, stored_text)
        assert_sensitive_value_absent(value, outcome_text)
        assert_sensitive_value_absent(value, captured_log)


@pytest.mark.integration
def test_issue_link_token_respects_caller_rollback_for_reissue_state(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    first_hash = "c" * 64
    now = datetime(2026, 7, 24, 16, 27, tzinfo=UTC)
    try:
        user = add_user(setup_session, "+998900007602")
        existing = add_token(
            setup_session,
            user,
            token_hash=first_hash,
            created_at=now - timedelta(minutes=1),
        )
        user_id = user.id
        existing_token_id = existing.id
        setup_session.commit()
    finally:
        setup_session.close()

    first_session = session_factory()
    raw_token = "rollback_reissue_token"
    second_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    try:
        current_user = first_session.get(User, user_id)
        assert current_user is not None
        settings = make_settings(m2_test_database)
        generator, generator_calls = make_generator([raw_token])

        issue_link_token(
            first_session,
            settings,
            current_user,
            ResolvedClientIp("203.0.113.162"),
            now,
            token_generator=generator,
        )
        existing_in_transaction = first_session.get(
            TelegramLinkToken,
            existing_token_id,
        )

        assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
        assert existing_in_transaction is not None
        assert existing_in_transaction.invalidated_at == now
        assert count_table(first_session, TelegramLinkToken) == 2
        assert count_table(first_session, AuthRateLimit) == 3
        first_session.rollback()
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        token_rows = second_session.scalars(
            select(TelegramLinkToken).where(TelegramLinkToken.user_id == user_id)
        ).all()
        old_token = second_session.get(TelegramLinkToken, existing_token_id)
        stored_text = stored_issue_domain_text(second_session)

        assert len(token_rows) == 1
        assert old_token is not None
        assert old_token.token_hash == first_hash
        assert old_token.invalidated_at is None
        assert all(token.token_hash != second_hash for token in token_rows)
        assert count_table(second_session, AuthRateLimit) == 0
        assert count_table(second_session, TelegramLink) == 0
        assert count_table(second_session, TelegramLinkEvent) == 0
        assert count_table(second_session, Customer) == 0
        assert_sensitive_value_absent(raw_token, stored_text)
    finally:
        second_session.rollback()
        second_session.close()


@pytest.mark.integration
def test_issue_link_token_respects_caller_commit_for_token_and_limiter(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    now = datetime(2026, 7, 24, 16, 28, tzinfo=UTC)
    raw_token = "commit_issue_token"
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    try:
        user = add_user(first_session, "+998900007603")
        user_id = user.id
        settings = make_settings(m2_test_database)

        result = issue_link_token(
            first_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.163"),
            now,
            token_generator=make_generator([raw_token])[0],
        )
        token_id = result.token.id
        first_session.commit()
    finally:
        first_session.close()

    second_session = session_factory()
    try:
        token = second_session.get(TelegramLinkToken, token_id)
        stored_text = stored_issue_domain_text(second_session)

        assert token is not None
        assert token.user_id == user_id
        assert token.token_hash == token_hash
        assert token.created_at == now
        assert token.expires_at == now + timedelta(
            seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS
        )
        assert token.consumed_at is None
        assert token.invalidated_at is None
        assert count_table(second_session, AuthRateLimit) == 3
        assert count_table(second_session, TelegramLink) == 0
        assert count_table(second_session, TelegramLinkEvent) == 0
        assert count_table(second_session, Customer) == 0
        assert_sensitive_value_absent(raw_token, stored_text)
    finally:
        second_session.rollback()
        second_session.close()


@pytest.mark.integration
def test_issue_link_token_expected_conflict_keeps_session_usable(
    monkeypatch,
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 24, 16, 29, tzinfo=UTC)
    user = add_user(db_session, "+998900007604")
    existing = add_token(
        db_session,
        user,
        token_hash="d" * 64,
        created_at=now - timedelta(minutes=1),
    )
    raw_token = "expected_conflict_issue_token"
    generator, generator_calls = make_generator([raw_token])
    settings = make_settings(m2_test_database)
    monkeypatch.setattr(
        telegram_repository,
        "get_outstanding_telegram_link_token_for_update",
        lambda session, current_user: None,
    )

    with pytest.raises(TelegramLinkTokenIssueError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.164"),
            now,
            token_generator=generator,
        )
    continuation_user = add_user(db_session, "+998900007605")
    db_session.refresh(existing)
    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
    stored_text = stored_issue_domain_text(db_session)

    assert exc_info.value.error_code is ErrorCode.RATE_LIMITED
    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert continuation_user.id is not None
    assert db_session.scalar(select(func.count()).select_from(User)) == 2
    assert existing.invalidated_at is None
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, Customer) == 0
    assert "IntegrityError" not in error_text
    assert "telegram_link_tokens" not in error_text
    assert "uq_telegram_link_tokens_one_outstanding_per_user" not in error_text
    assert_sensitive_value_absent(raw_token, error_text)
    assert_sensitive_value_absent(raw_token, stored_text)


@pytest.mark.integration
def test_issue_link_token_wraps_unexpected_database_error_without_raw_detail(
    monkeypatch,
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 24, 16, 30, tzinfo=UTC)
    user = add_user(db_session, "+998900007606")
    raw_token = "unexpected_db_error_issue_token"
    raw_database_detail = (
        "raw SQL violates constraint uq_telegram_link_tokens_token_hash"
    )
    generator, generator_calls = make_generator([raw_token])
    settings = make_settings(m2_test_database)

    def fail_insert(*_args, **_kwargs) -> TelegramLinkToken:
        raise SQLAlchemyError(raw_database_detail)

    monkeypatch.setattr(
        telegram_service,
        "invalidate_and_insert_telegram_link_token",
        fail_insert,
    )

    with pytest.raises(TelegramLinkTokenIssueInternalError) as exc_info:
        issue_link_token(
            db_session,
            settings,
            user,
            ResolvedClientIp("203.0.113.165"),
            now,
            token_generator=generator,
        )
    error_text = f"{exc_info.value!r} {exc_info.value}"
    stored_text = stored_issue_domain_text(db_session)

    assert generator_calls == [TELEGRAM_LINK_TOKEN_ENTROPY_BYTES]
    assert str(exc_info.value) == "Telegram link token issue failed"
    assert exc_info.value.__cause__ is None
    assert raw_database_detail not in error_text
    assert "telegram_link_tokens" not in error_text
    assert "constraint" not in error_text.casefold()
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert count_table(db_session, Customer) == 0
    assert_sensitive_value_absent(raw_token, error_text)
    assert_sensitive_value_absent(raw_token, stored_text)


@pytest.mark.integration
def test_issue_link_token_service_does_not_commit_or_full_rollback(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    session_spy = SessionSpy(first_session)
    now = datetime(2026, 7, 24, 16, 25, tzinfo=UTC)
    try:
        user = add_user(first_session, "+998900007004")
        settings = make_settings(m2_test_database)

        result = issue_link_token(
            session_spy,
            settings,
            user,
            ResolvedClientIp("203.0.113.74"),
            now,
            token_generator=make_generator([FIRST_RAW_TOKEN])[0],
        )
        stored_token_count = second_session.scalar(
            select(func.count()).select_from(TelegramLinkToken)
        )
        stored_rate_limit_count = second_session.scalar(
            select(func.count()).select_from(AuthRateLimit)
        )

        assert result.raw_token.as_internal_value() == FIRST_RAW_TOKEN
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert session_spy.close_called is False
        assert stored_token_count == 0
        assert stored_rate_limit_count == 0
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()
