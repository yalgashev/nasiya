import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramLinkTokenConsumeError,
    get_valid_link_token_for_consume,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token


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


def add_token(
    session: Session,
    user: User,
    *,
    raw_token: str,
    created_at: datetime,
    expires_at: datetime,
    consumed_at: datetime | None = None,
    invalidated_at: datetime | None = None,
) -> TelegramLinkToken:
    token = TelegramLinkToken(
        user_id=user.id,
        token_hash=hash_telegram_link_token(RawTelegramLinkToken(raw_token)),
        created_at=created_at,
        expires_at=expires_at,
        consumed_at=consumed_at,
        invalidated_at=invalidated_at,
    )
    session.add(token)
    session.flush()
    return token


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def seed_committed_token(
    engine: Engine,
    *,
    phone: str,
    raw_token: str,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[UUID, UUID]:
    session_factory = create_database_session_factory(engine)
    session = session_factory()
    try:
        user = add_user(session, phone)
        token = add_token(
            session,
            user,
            raw_token=raw_token,
            created_at=created_at,
            expires_at=expires_at,
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


def assert_uniform_invalid_error(
    exc: TelegramLinkTokenConsumeError,
    *,
    raw_token: str,
    token_hash: str,
    log_text: str = "",
) -> None:
    error_text = f"{exc!r} {exc} {exc.public_error} {log_text}"

    assert exc.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert exc.public_error["code"] == "LINK_TOKEN_INVALID"
    assert raw_token not in error_text
    assert token_hash not in error_text
    assert "consumed" not in error_text.casefold()
    assert "invalidated" not in error_text.casefold()
    assert "expired" not in error_text.casefold()
    assert "unknown" not in error_text.casefold()
    assert "telegram_link_tokens" not in error_text
    assert "token_hash" not in error_text


@pytest.mark.integration
def test_valid_consume_helper_hashes_raw_token_and_locks_token_row(
    m2_test_database: Engine,
    caplog,
) -> None:
    raw_token = "valid_consume_token"
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    now = datetime(2026, 7, 24, 18, 20, tzinfo=UTC)
    _user_id, token_id = seed_committed_token(
        m2_test_database,
        phone="+998900010001",
        raw_token=raw_token,
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=9),
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    second_session = session_factory()
    try:
        with caplog.at_level(logging.INFO):
            token = get_valid_link_token_for_consume(
                first_session,
                RawTelegramLinkToken(raw_token),
                now,
            )

        assert token.id == token_id
        assert token.token_hash == token_hash
        assert token.consumed_at is None
        assert token.invalidated_at is None
        assert_token_row_is_locked_by_other_transaction(second_session, token_id)
        assert raw_token not in caplog.text
        assert token_hash not in caplog.text
    finally:
        first_session.rollback()
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_valid_consume_helper_does_not_mutate_token_link_or_events(
    db_session: Session,
) -> None:
    raw_token = "state_preserving_consume_token"
    now = datetime(2026, 7, 24, 18, 25, tzinfo=UTC)
    user = add_user(db_session, "+998900010002")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=9),
    )
    original_state = (
        token.token_hash,
        token.created_at,
        token.expires_at,
        token.consumed_at,
        token.invalidated_at,
    )

    found = get_valid_link_token_for_consume(db_session, raw_token, now)
    db_session.refresh(token)

    assert found is token
    assert (
        token.token_hash,
        token.created_at,
        token.expires_at,
        token.consumed_at,
        token.invalidated_at,
    ) == original_state
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("label", "raw_token", "terminal_kwargs"),
    [
        ("unknown", "unknown_consume_token", None),
        ("consumed", "consumed_consume_token", {"consumed_at": "now"}),
        ("invalidated", "invalidated_consume_token", {"invalidated_at": "now"}),
        ("expires-now", "expires_now_consume_token", {"expires_at": "now"}),
        ("expired", "expired_consume_token", {"expires_at": "past"}),
    ],
    ids=["unknown", "consumed", "invalidated", "expires-now", "expired"],
)
def test_invalid_consume_tokens_return_uniform_link_token_invalid(
    db_session: Session,
    caplog,
    label: str,
    raw_token: str,
    terminal_kwargs: dict[str, str] | None,
) -> None:
    now = datetime(2026, 7, 24, 18, 30, tzinfo=UTC)
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    if terminal_kwargs is not None:
        user = add_user(db_session, f"+9989000100{len(label):02d}")
        consumed_at = now if terminal_kwargs.get("consumed_at") == "now" else None
        invalidated_at = (
            now if terminal_kwargs.get("invalidated_at") == "now" else None
        )
        expires_marker = terminal_kwargs.get("expires_at")
        expires_at = {
            "now": now,
            "past": now - timedelta(seconds=1),
        }.get(expires_marker, now + timedelta(minutes=9))
        token = add_token(
            db_session,
            user,
            raw_token=raw_token,
            created_at=now - timedelta(minutes=1),
            expires_at=expires_at,
            consumed_at=consumed_at,
            invalidated_at=invalidated_at,
        )
        original_terminal_state = (token.consumed_at, token.invalidated_at)

    with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
        with caplog.at_level(logging.INFO):
            get_valid_link_token_for_consume(
                db_session,
                RawTelegramLinkToken(raw_token),
                now,
            )

    assert_uniform_invalid_error(
        exc_info.value,
        raw_token=raw_token,
        token_hash=token_hash,
        log_text=caplog.text,
    )
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0
    if terminal_kwargs is not None:
        db_session.refresh(token)
        assert (token.consumed_at, token.invalidated_at) == original_terminal_state


@pytest.mark.integration
@pytest.mark.parametrize(
    "malformed_raw_token",
    [
        "",
        "raw token with spaces",
        "raw/token/with/slashes",
        object(),
    ],
    ids=["empty", "spaces", "slashes", "object"],
)
def test_malformed_raw_consume_input_maps_to_uniform_invalid_semantics(
    db_session: Session,
    caplog,
    malformed_raw_token: object,
) -> None:
    now = datetime(2026, 7, 24, 18, 35, tzinfo=UTC)
    fallback_token_hash = "0" * 64

    with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
        with caplog.at_level(logging.INFO):
            get_valid_link_token_for_consume(
                db_session,
                malformed_raw_token,  # type: ignore[arg-type]
                now,
            )

    raw_text = str(malformed_raw_token)
    assert exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert exc_info.value.public_error["code"] == "LINK_TOKEN_INVALID"
    if raw_text:
        assert raw_text not in str(exc_info.value)
        assert raw_text not in str(exc_info.value.public_error)
        assert raw_text not in caplog.text
    assert fallback_token_hash not in caplog.text
    assert "URL-safe" not in str(exc_info.value)
    assert "cannot be empty" not in str(exc_info.value)
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_consume_validity_helper_does_not_commit_rollback_or_close(
    m2_test_database: Engine,
) -> None:
    raw_token = "transaction_owned_consume_token"
    now = datetime(2026, 7, 24, 18, 40, tzinfo=UTC)
    _user_id, token_id = seed_committed_token(
        m2_test_database,
        phone="+998900010003",
        raw_token=raw_token,
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=9),
    )
    session_factory = create_database_session_factory(m2_test_database)
    first_session = session_factory()
    session_spy = SessionSpy(first_session)
    try:
        token = get_valid_link_token_for_consume(
            session_spy,
            RawTelegramLinkToken(raw_token),
            now,
        )

        assert token.id == token_id
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert session_spy.close_called is False
    finally:
        first_session.rollback()
        first_session.close()
