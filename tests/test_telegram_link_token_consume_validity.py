import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import (
    TelegramLinkTokenConsumeError,
    bind_start_token_for_contact,
    consume_start_token,
    get_valid_link_token_for_consume,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token

CONTACT_BINDING_KEY = SecretStr(
    "test-consume-validity-contact-binding-key-at-least-32-characters"
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


def consume_raw(
    session: Session,
    *,
    raw_token: str,
    telegram_chat_id: int,
    now: datetime,
):
    raw = RawTelegramLinkToken(raw_token)
    phone = session.scalar(
        select(User.phone)
        .join(TelegramLinkToken, TelegramLinkToken.user_id == User.id)
        .where(TelegramLinkToken.token_hash == hash_telegram_link_token(raw))
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


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def domain_snapshot(session: Session) -> tuple[tuple[object, ...], ...]:
    token_rows = session.execute(
        select(
            TelegramLinkToken.id,
            TelegramLinkToken.user_id,
            TelegramLinkToken.token_hash,
            TelegramLinkToken.created_at,
            TelegramLinkToken.expires_at,
            TelegramLinkToken.consumed_at,
            TelegramLinkToken.invalidated_at,
        ).order_by(TelegramLinkToken.id)
    ).all()
    link_rows = session.execute(
        select(
            TelegramLink.id,
            TelegramLink.user_id,
            TelegramLink.telegram_chat_id,
            TelegramLink.linked_at,
            TelegramLink.unlinked_at,
            TelegramLink.updated_at,
        ).order_by(TelegramLink.id)
    ).all()
    event_rows = session.execute(
        select(
            TelegramLinkEvent.id,
            TelegramLinkEvent.user_id,
            TelegramLinkEvent.action,
            TelegramLinkEvent.occurred_at,
        ).order_by(TelegramLinkEvent.id)
    ).all()
    return tuple(token_rows + link_rows + event_rows)


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
    assert "telegram_links" not in error_text
    assert "token_hash" not in error_text
    assert "constraint" not in error_text.casefold()
    assert "integrityerror" not in error_text.casefold()


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
        invalidated_at = now if terminal_kwargs.get("invalidated_at") == "now" else None
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
@pytest.mark.parametrize(
    ("label", "seconds_from_expiry", "is_valid"),
    [
        ("before-expiry", -1, True),
        ("at-expiry", 0, False),
        ("after-expiry", 1, False),
    ],
    ids=["now-before-expires", "now-equals-expires", "now-after-expires"],
)
def test_token_ttl_boundary_uses_injected_timezone_aware_now(
    db_session: Session,
    caplog,
    label: str,
    seconds_from_expiry: int,
    is_valid: bool,
) -> None:
    expires_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    now = expires_at + timedelta(seconds=seconds_from_expiry)
    raw_token = f"ttl_boundary_{label}_token"
    token_hash = hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    user = add_user(db_session, f"+9989000101{abs(seconds_from_expiry):02d}")
    token = add_token(
        db_session,
        user,
        raw_token=raw_token,
        created_at=expires_at - timedelta(minutes=10),
        expires_at=expires_at,
    )
    before_snapshot = domain_snapshot(db_session)

    assert now.tzinfo is UTC
    with caplog.at_level(logging.INFO):
        if is_valid:
            found = get_valid_link_token_for_consume(
                db_session,
                RawTelegramLinkToken(raw_token),
                now,
            )

            assert found is token
            assert found.expires_at == expires_at
            assert domain_snapshot(db_session) == before_snapshot
        else:
            with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
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
            assert domain_snapshot(db_session) == before_snapshot

    assert raw_token not in caplog.text
    assert token_hash not in caplog.text
    assert count_table(db_session, TelegramLink) == 0
    assert count_table(db_session, TelegramLinkEvent) == 0


@pytest.mark.integration
def test_invalid_token_matrix_is_uniform_and_state_preserving(
    db_session: Session,
    caplog,
) -> None:
    now = datetime(2026, 7, 25, 12, 30, tzinfo=UTC)
    invalid_shapes: list[tuple[str, ErrorCode, tuple[tuple[str, str], ...], str]] = []

    def capture_invalid_case(
        *,
        raw_token: str,
        token_hash: str,
        operation,
    ) -> None:
        before_snapshot = domain_snapshot(db_session)
        caplog.clear()

        with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
            with caplog.at_level(logging.INFO):
                operation()

        assert domain_snapshot(db_session) == before_snapshot
        assert_uniform_invalid_error(
            exc_info.value,
            raw_token=raw_token,
            token_hash=token_hash,
            log_text=caplog.text,
        )
        invalid_shapes.append(
            (
                type(exc_info.value).__name__,
                exc_info.value.error_code,
                tuple(sorted(exc_info.value.public_error.items())),
                str(exc_info.value),
            )
        )

    unknown_raw = "matrix_unknown_token"
    unknown_hash = hash_telegram_link_token(RawTelegramLinkToken(unknown_raw))
    capture_invalid_case(
        raw_token=unknown_raw,
        token_hash=unknown_hash,
        operation=lambda: get_valid_link_token_for_consume(
            db_session,
            RawTelegramLinkToken(unknown_raw),
            now,
        ),
    )

    malformed_raw = "matrix malformed token"
    capture_invalid_case(
        raw_token=malformed_raw,
        token_hash="0" * 64,
        operation=lambda: get_valid_link_token_for_consume(
            db_session,
            malformed_raw,
            now,
        ),
    )

    consumed_raw = "matrix_consumed_token"
    consumed_user = add_user(db_session, "+998900010120")
    consumed_token = add_token(
        db_session,
        consumed_user,
        raw_token=consumed_raw,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
        consumed_at=now - timedelta(minutes=1),
    )
    capture_invalid_case(
        raw_token=consumed_raw,
        token_hash=consumed_token.token_hash,
        operation=lambda: get_valid_link_token_for_consume(
            db_session,
            RawTelegramLinkToken(consumed_raw),
            now,
        ),
    )

    invalidated_raw = "matrix_invalidated_token"
    invalidated_user = add_user(db_session, "+998900010121")
    invalidated_token = add_token(
        db_session,
        invalidated_user,
        raw_token=invalidated_raw,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
        invalidated_at=now - timedelta(minutes=1),
    )
    capture_invalid_case(
        raw_token=invalidated_raw,
        token_hash=invalidated_token.token_hash,
        operation=lambda: get_valid_link_token_for_consume(
            db_session,
            RawTelegramLinkToken(invalidated_raw),
            now,
        ),
    )

    purged_raw = "matrix_purged_token"
    purged_user = add_user(db_session, "+998900010122")
    purged_token = add_token(
        db_session,
        purged_user,
        raw_token=purged_raw,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
    )
    purged_hash = purged_token.token_hash
    db_session.delete(purged_token)
    db_session.flush()
    capture_invalid_case(
        raw_token=purged_raw,
        token_hash=purged_hash,
        operation=lambda: get_valid_link_token_for_consume(
            db_session,
            RawTelegramLinkToken(purged_raw),
            now,
        ),
    )

    replay_raw = "matrix_replay_token"
    replay_user = add_user(db_session, "+998900010123")
    replay_token = add_token(
        db_session,
        replay_user,
        raw_token=replay_raw,
        created_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(minutes=5),
    )
    first_consume_at = now + timedelta(seconds=1)
    first_result = consume_raw(
        db_session,
        raw_token=replay_raw,
        telegram_chat_id=10_123,
        now=first_consume_at,
    )
    assert first_result.token is replay_token
    assert replay_token.consumed_at == first_consume_at
    capture_invalid_case(
        raw_token=replay_raw,
        token_hash=replay_token.token_hash,
        operation=lambda: consume_raw(
            db_session,
            raw_token=replay_raw,
            telegram_chat_id=10_123,
            now=first_consume_at + timedelta(seconds=1),
        ),
    )

    assert len(invalid_shapes) == 6
    assert len(set(invalid_shapes)) == 1
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 1


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
