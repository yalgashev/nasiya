import logging
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from inspect import getsource, signature
from threading import Barrier, BrokenBarrierError
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.repository import (
    TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS,
    delete_telegram_link_tokens_eligible_for_purge,
)
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_PURGE_DEFAULT_BATCH_SIZE,
    TELEGRAM_LINK_TOKEN_PURGE_MAX_BATCH_SIZE,
    TelegramLinkTokenConsumeError,
    get_valid_link_token_for_consume,
    purge_terminal_link_tokens,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token

_BARRIER_TIMEOUT_SECONDS = 5
_FUTURE_TIMEOUT_SECONDS = 15


class SessionSpy:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commit_called = False
        self.rollback_called = False
        self.close_called = False

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


@dataclass(frozen=True, repr=False)
class ParallelPurgeOutcome:
    label: str
    kind: str
    deleted_count: int = 0
    error_code: ErrorCode | None = None
    session_usable: bool = False
    exception_class: str | None = None

    def __repr__(self) -> str:
        return (
            "ParallelPurgeOutcome("
            f"label={self.label!r}, kind={self.kind!r}, "
            f"deleted_count={self.deleted_count}, error_code={self.error_code}, "
            f"session_usable={self.session_usable}, "
            f"exception_class={self.exception_class!r}"
            ")"
        )


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


def raw_token_hash(raw_token: str) -> str:
    return hash_telegram_link_token(RawTelegramLinkToken(raw_token))


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


def assert_purged_token_is_uniform_invalid(
    session: Session,
    raw_token: str,
    now: datetime,
    *,
    log_text: str,
) -> None:
    token_hash_value = raw_token_hash(raw_token)

    with pytest.raises(TelegramLinkTokenConsumeError) as exc_info:
        get_valid_link_token_for_consume(
            session,
            RawTelegramLinkToken(raw_token),
            now,
        )

    error_text = f"{exc_info.value!r} {exc_info.value} {exc_info.value.public_error}"
    assert exc_info.value.error_code is ErrorCode.LINK_TOKEN_INVALID
    assert exc_info.value.public_error["code"] == "LINK_TOKEN_INVALID"
    assert raw_token not in error_text
    assert token_hash_value not in error_text
    assert "telegram_link_tokens" not in error_text
    assert raw_token not in log_text
    assert token_hash_value not in log_text


def count_table(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def token_ids(session: Session) -> set[UUID]:
    return set(session.scalars(select(TelegramLinkToken.id)).all())


def link_event_snapshot(session: Session) -> tuple[tuple[object, ...], ...]:
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
    return tuple(link_rows + event_rows)


def assert_sensitive_values_absent(
    sensitive_values: tuple[str, ...],
    *texts: str,
) -> None:
    if any(value in text for value in sensitive_values for text in texts):
        pytest.fail("sensitive Telegram purge value leaked", pytrace=False)


def test_purge_terminal_link_tokens_public_contract_is_internal_and_bounded() -> None:
    parameters = signature(purge_terminal_link_tokens).parameters

    assert list(parameters) == ["session", "now", "batch_size"]
    assert parameters["batch_size"].default == 500
    assert TELEGRAM_LINK_TOKEN_PURGE_DEFAULT_BATCH_SIZE == 500
    assert TELEGRAM_LINK_TOKEN_PURGE_MAX_BATCH_SIZE == 5000

    service_source = getsource(purge_terminal_link_tokens)
    repository_source = getsource(delete_telegram_link_tokens_eligible_for_purge)
    assert "commit(" not in service_source
    assert "rollback(" not in service_source
    assert "append_telegram_link_event" not in service_source
    assert "TelegramLinkEvent" not in service_source
    assert "schedule" not in service_source.lower()
    assert "cron" not in service_source.lower()
    assert "route" not in service_source.lower()
    assert "skip_locked=True" in repository_source
    assert ".limit(limit)" in repository_source
    assert "delete(TelegramLinkToken)" in repository_source
    assert "telegram_links" not in repository_source
    assert "TelegramLinkEvent" not in repository_source


@pytest.mark.integration
def test_purge_terminal_link_tokens_deletes_only_eligible_batch_and_is_idempotent(
    caplog,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 25, 16, 0, tzinfo=UTC)
    cutoff = now - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    linked_user = add_user(db_session, "+998900011001")
    invalidated_user = add_user(db_session, "+998900011002")
    expired_user = add_user(db_session, "+998900011003")
    valid_user = add_user(db_session, "+998900011004")
    fresh_terminal_user = add_user(db_session, "+998900011005")
    link = add_link(
        db_session,
        linked_user,
        telegram_chat_id=91_001,
        linked_at=now - timedelta(days=60),
    )
    event = add_event(
        db_session,
        linked_user,
        action="linked",
        occurred_at=now - timedelta(days=60),
    )
    eligible_consumed = add_token(
        db_session,
        linked_user,
        token_hash_value=token_hash(301),
        created_at=cutoff - timedelta(days=3),
        consumed_at=cutoff,
    )
    eligible_invalidated = add_token(
        db_session,
        invalidated_user,
        token_hash_value=token_hash(302),
        created_at=cutoff - timedelta(days=2),
        invalidated_at=cutoff,
    )
    eligible_expired_unused = add_token(
        db_session,
        expired_user,
        token_hash_value=token_hash(303),
        created_at=cutoff - timedelta(days=1),
        expires_at=cutoff,
    )
    valid_outstanding = add_token(
        db_session,
        valid_user,
        token_hash_value=token_hash(304),
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=9),
    )
    fresh_terminal = add_token(
        db_session,
        fresh_terminal_user,
        token_hash_value=token_hash(305),
        created_at=cutoff,
        consumed_at=cutoff + timedelta(microseconds=1),
    )
    before_snapshot = link_event_snapshot(db_session)

    with caplog.at_level("DEBUG"):
        first_deleted_count = purge_terminal_link_tokens(
            db_session,
            now,
            batch_size=2,
        )
        second_deleted_count = purge_terminal_link_tokens(
            db_session,
            now,
            batch_size=2,
        )
        third_deleted_count = purge_terminal_link_tokens(
            db_session,
            now,
            batch_size=2,
        )

    remaining_token_ids = token_ids(db_session)
    assert first_deleted_count == 2
    assert second_deleted_count == 1
    assert third_deleted_count == 0
    assert eligible_consumed.id not in remaining_token_ids
    assert eligible_invalidated.id not in remaining_token_ids
    assert eligible_expired_unused.id not in remaining_token_ids
    assert valid_outstanding.id in remaining_token_ids
    assert fresh_terminal.id in remaining_token_ids
    assert count_table(db_session, TelegramLinkToken) == 2
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 1
    assert link_event_snapshot(db_session) == before_snapshot
    assert link.telegram_chat_id == 91_001
    assert event.action == "linked"
    assert caplog.text == ""
    for token in (
        eligible_consumed,
        eligible_invalidated,
        eligible_expired_unused,
        valid_outstanding,
        fresh_terminal,
    ):
        assert token.token_hash not in caplog.text
        assert str(token.user_id) not in caplog.text
    assert str(link.telegram_chat_id) not in caplog.text


@pytest.mark.integration
def test_purge_boundary_idempotency_batch_size_and_invalid_after_purge_matrix(
    caplog,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 25, 16, 20, tzinfo=UTC)
    cutoff = now - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    phone_numbers = [
        "+998900011101",
        "+998900011102",
        "+998900011103",
        "+998900011104",
        "+998900011105",
        "+998900011106",
        "+998900011107",
    ]
    users = [add_user(db_session, phone) for phone in phone_numbers]
    link = add_link(
        db_session,
        users[0],
        telegram_chat_id=91_101,
        linked_at=now - timedelta(days=90),
    )
    event = add_event(
        db_session,
        users[0],
        action="linked",
        occurred_at=now - timedelta(days=90),
    )
    consumed_raw = "purge_matrix_consumed_exact_token"
    consumed_exact = add_token(
        db_session,
        users[0],
        token_hash_value=raw_token_hash(consumed_raw),
        created_at=cutoff - timedelta(days=3),
        consumed_at=cutoff,
    )
    consumed_before_cutoff = add_token(
        db_session,
        users[1],
        token_hash_value=token_hash(401),
        created_at=cutoff - timedelta(days=2),
        consumed_at=cutoff + timedelta(microseconds=1),
    )
    invalidated_exact = add_token(
        db_session,
        users[2],
        token_hash_value=token_hash(402),
        created_at=cutoff - timedelta(days=1),
        invalidated_at=cutoff,
    )
    invalidated_before_cutoff = add_token(
        db_session,
        users[3],
        token_hash_value=token_hash(403),
        created_at=cutoff - timedelta(hours=12),
        invalidated_at=cutoff + timedelta(microseconds=1),
    )
    expired_exact = add_token(
        db_session,
        users[4],
        token_hash_value=token_hash(404),
        created_at=cutoff - timedelta(hours=6),
        expires_at=cutoff,
    )
    expired_before_cutoff = add_token(
        db_session,
        users[5],
        token_hash_value=token_hash(405),
        created_at=cutoff - timedelta(hours=5),
        expires_at=cutoff + timedelta(microseconds=1),
    )
    valid_outstanding = add_token(
        db_session,
        users[6],
        token_hash_value=token_hash(406),
        created_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(minutes=9),
    )
    before_snapshot = link_event_snapshot(db_session)

    with pytest.raises(ValueError):
        purge_terminal_link_tokens(db_session, now, batch_size=0)
    with pytest.raises(ValueError):
        purge_terminal_link_tokens(db_session, now, batch_size=5001)

    with caplog.at_level("DEBUG"):
        first_deleted_count = purge_terminal_link_tokens(
            db_session,
            now,
            batch_size=1,
        )
        second_deleted_count = purge_terminal_link_tokens(
            db_session,
            now,
            batch_size=5000,
        )
        third_deleted_count = purge_terminal_link_tokens(
            db_session,
            now,
            batch_size=5000,
        )

    remaining_token_ids = token_ids(db_session)
    assert first_deleted_count == 1
    assert second_deleted_count == 2
    assert third_deleted_count == 0
    assert consumed_exact.id not in remaining_token_ids
    assert invalidated_exact.id not in remaining_token_ids
    assert expired_exact.id not in remaining_token_ids
    assert consumed_before_cutoff.id in remaining_token_ids
    assert invalidated_before_cutoff.id in remaining_token_ids
    assert expired_before_cutoff.id in remaining_token_ids
    assert valid_outstanding.id in remaining_token_ids
    assert count_table(db_session, TelegramLinkToken) == 4
    assert count_table(db_session, TelegramLink) == 1
    assert count_table(db_session, TelegramLinkEvent) == 1
    assert link_event_snapshot(db_session) == before_snapshot
    assert link.telegram_chat_id == 91_101
    assert event.action == "linked"

    assert_purged_token_is_uniform_invalid(
        db_session,
        consumed_raw,
        now,
        log_text=caplog.text,
    )
    assert caplog.text == ""
    for token in (
        consumed_exact,
        consumed_before_cutoff,
        invalidated_exact,
        invalidated_before_cutoff,
        expired_exact,
        expired_before_cutoff,
        valid_outstanding,
    ):
        assert token.token_hash not in caplog.text
        assert str(token.user_id) not in caplog.text
    for phone_number in phone_numbers:
        assert phone_number not in caplog.text
    assert "91_101" not in caplog.text
    assert "91101" not in caplog.text
    assert "127.0.0.1" not in caplog.text


@pytest.mark.integration
def test_parallel_purge_deletes_each_eligible_row_at_most_once(
    caplog,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 7, 25, 16, 30, tzinfo=UTC)
    cutoff = now - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    setup_session = session_factory()
    try:
        users = [
            add_user(setup_session, "+998900011201"),
            add_user(setup_session, "+998900011202"),
            add_user(setup_session, "+998900011203"),
            add_user(setup_session, "+998900011204"),
        ]
        link = add_link(
            setup_session,
            users[0],
            telegram_chat_id=91_201,
            linked_at=now - timedelta(days=90),
        )
        event = add_event(
            setup_session,
            users[0],
            action="linked",
            occurred_at=now - timedelta(days=90),
        )
        eligible_tokens = [
            add_token(
                setup_session,
                users[0],
                token_hash_value=token_hash(501),
                created_at=cutoff - timedelta(days=4),
                consumed_at=cutoff,
            ),
            add_token(
                setup_session,
                users[0],
                token_hash_value=token_hash(502),
                created_at=cutoff - timedelta(days=3),
                consumed_at=cutoff - timedelta(seconds=1),
            ),
            add_token(
                setup_session,
                users[1],
                token_hash_value=token_hash(503),
                created_at=cutoff - timedelta(days=2),
                invalidated_at=cutoff,
            ),
            add_token(
                setup_session,
                users[2],
                token_hash_value=token_hash(504),
                created_at=cutoff - timedelta(days=1),
                expires_at=cutoff,
            ),
        ]
        valid_outstanding = add_token(
            setup_session,
            users[3],
            token_hash_value=token_hash(505),
            created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=9),
        )
        initial_eligible_ids = {token.id for token in eligible_tokens}
        valid_outstanding_id = valid_outstanding.id
        before_snapshot = link_event_snapshot(setup_session)
        sensitive_values = tuple(
            str(value)
            for value in (
                *(token.token_hash for token in eligible_tokens),
                valid_outstanding.token_hash,
                *(user.id for user in users),
                *(user.phone for user in users),
                link.telegram_chat_id,
                event.user_id,
            )
        )
        setup_session.commit()
    finally:
        setup_session.close()

    start_barrier = Barrier(2)

    def worker(label: str) -> ParallelPurgeOutcome:
        session = session_factory()
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            deleted_count = purge_terminal_link_tokens(
                session,
                now,
                batch_size=10,
            )
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return ParallelPurgeOutcome(
                label=label,
                kind="purged",
                deleted_count=deleted_count,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelPurgeOutcome(
                label=label,
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelPurgeOutcome(
                label=label,
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [
                executor.submit(worker, label) for label in ("first", "second")
            ]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("parallel Telegram token purge timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    purges = [outcome for outcome in outcomes if outcome.kind == "purged"]
    aggregate_deleted_count = sum(outcome.deleted_count for outcome in purges)

    final_session = session_factory()
    try:
        remaining_token_ids = token_ids(final_session)
        unique_deleted_ids = initial_eligible_ids - remaining_token_ids

        assert unexpected == []
        assert len(purges) == 2
        assert all(outcome.session_usable for outcome in purges)
        assert aggregate_deleted_count == len(unique_deleted_ids)
        assert aggregate_deleted_count <= len(initial_eligible_ids)
        assert initial_eligible_ids.isdisjoint(remaining_token_ids)
        assert valid_outstanding_id in remaining_token_ids
        assert count_table(final_session, TelegramLink) == 1
        assert count_table(final_session, TelegramLinkEvent) == 1
        assert link_event_snapshot(final_session) == before_snapshot
    finally:
        final_session.rollback()
        final_session.close()

    assert_sensitive_values_absent(sensitive_values, captured_log, outcome_text)
    assert "127.0.0.1" not in captured_log


@pytest.mark.integration
def test_exact_expired_token_consume_racing_purge_stays_uniform_invalid(
    caplog,
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    now = datetime(2026, 7, 25, 16, 40, tzinfo=UTC)
    cutoff = now - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    raw_value = "purge_race_exact_expired_token"
    setup_session = session_factory()
    try:
        expired_user = add_user(setup_session, "+998900011301")
        valid_user = add_user(setup_session, "+998900011302")
        link = add_link(
            setup_session,
            expired_user,
            telegram_chat_id=91_301,
            linked_at=now - timedelta(days=90),
        )
        event = add_event(
            setup_session,
            expired_user,
            action="linked",
            occurred_at=now - timedelta(days=90),
        )
        expired_token = add_token(
            setup_session,
            expired_user,
            token_hash_value=raw_token_hash(raw_value),
            created_at=cutoff - timedelta(minutes=10),
            expires_at=cutoff,
        )
        valid_outstanding = add_token(
            setup_session,
            valid_user,
            token_hash_value=token_hash(601),
            created_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=9),
        )
        expired_token_id = expired_token.id
        valid_outstanding_id = valid_outstanding.id
        before_snapshot = link_event_snapshot(setup_session)
        sensitive_values = tuple(
            str(value)
            for value in (
                raw_value,
                expired_token.token_hash,
                valid_outstanding.token_hash,
                expired_user.id,
                valid_user.id,
                expired_user.phone,
                valid_user.phone,
                link.telegram_chat_id,
                event.user_id,
            )
        )
        setup_session.commit()
    finally:
        setup_session.close()

    start_barrier = Barrier(2)

    def purge_worker() -> ParallelPurgeOutcome:
        session = session_factory()
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            deleted_count = purge_terminal_link_tokens(
                session,
                now,
                batch_size=10,
            )
            session_usable = session.scalar(select(1)) == 1
            session.commit()
            return ParallelPurgeOutcome(
                label="purge",
                kind="purged",
                deleted_count=deleted_count,
                session_usable=session_usable,
            )
        except BrokenBarrierError:
            session.rollback()
            return ParallelPurgeOutcome(
                label="purge",
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelPurgeOutcome(
                label="purge",
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    def consume_worker() -> ParallelPurgeOutcome:
        session = session_factory()
        try:
            start_barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            session.execute(text("SET LOCAL lock_timeout = '5000ms'"))
            session.execute(text("SET LOCAL statement_timeout = '10000ms'"))
            try:
                get_valid_link_token_for_consume(
                    session,
                    RawTelegramLinkToken(raw_value),
                    now,
                )
            except TelegramLinkTokenConsumeError as exc:
                session_usable = session.scalar(select(1)) == 1
                session.commit()
                return ParallelPurgeOutcome(
                    label="consume",
                    kind="domain_error",
                    error_code=exc.error_code,
                    session_usable=session_usable,
                )
            session.rollback()
            return ParallelPurgeOutcome(label="consume", kind="unexpected")
        except BrokenBarrierError:
            session.rollback()
            return ParallelPurgeOutcome(
                label="consume",
                kind="unexpected",
                exception_class="BrokenBarrierError",
            )
        except Exception as exc:
            session.rollback()
            return ParallelPurgeOutcome(
                label="consume",
                kind="unexpected",
                exception_class=type(exc).__name__,
            )
        finally:
            session.close()

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        with caplog.at_level(logging.DEBUG):
            futures = [executor.submit(purge_worker), executor.submit(consume_worker)]
            done, not_done = wait(futures, timeout=_FUTURE_TIMEOUT_SECONDS)
        if not_done:
            start_barrier.abort()
            for future in not_done:
                future.cancel()
            pytest.fail("purge versus consume timed out", pytrace=False)
        outcomes = [future.result(timeout=0) for future in futures]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    captured_log = caplog.text
    outcome_text = " ".join(repr(outcome) for outcome in outcomes)
    unexpected = [outcome for outcome in outcomes if outcome.kind == "unexpected"]
    purge_outcome = next(outcome for outcome in outcomes if outcome.label == "purge")
    consume_outcome = next(
        outcome for outcome in outcomes if outcome.label == "consume"
    )

    final_session = session_factory()
    try:
        assert unexpected == []
        assert purge_outcome.kind == "purged"
        assert purge_outcome.deleted_count in {0, 1}
        assert purge_outcome.session_usable is True
        assert consume_outcome.kind == "domain_error"
        assert consume_outcome.error_code is ErrorCode.LINK_TOKEN_INVALID
        assert consume_outcome.session_usable is True
        assert final_session.get(TelegramLinkToken, valid_outstanding_id) is not None
        assert count_table(final_session, TelegramLink) == 1
        assert count_table(final_session, TelegramLinkEvent) == 1
        assert link_event_snapshot(final_session) == before_snapshot

        if final_session.get(TelegramLinkToken, expired_token_id) is not None:
            assert purge_terminal_link_tokens(final_session, now, batch_size=10) == 1
        else:
            assert purge_terminal_link_tokens(final_session, now, batch_size=10) == 0
        assert final_session.get(TelegramLinkToken, expired_token_id) is None
        assert final_session.get(TelegramLinkToken, valid_outstanding_id) is not None
        assert link_event_snapshot(final_session) == before_snapshot
    finally:
        final_session.rollback()
        final_session.close()

    assert_sensitive_values_absent(sensitive_values, captured_log, outcome_text)
    assert "127.0.0.1" not in captured_log


@pytest.mark.integration
def test_purge_terminal_link_tokens_validates_utc_now_and_batch_size(
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 25, 16, 5, tzinfo=UTC)

    with pytest.raises(ValueError) as naive_error:
        purge_terminal_link_tokens(db_session, datetime(2026, 7, 25, 16, 5))
    for invalid_batch_size in (0, -1, 5001, True):
        with pytest.raises(ValueError) as batch_error:
            purge_terminal_link_tokens(
                db_session,
                now,
                batch_size=invalid_batch_size,
            )
        assert "1..5000" in str(batch_error.value)

    assert "timezone-aware" in str(naive_error.value)


@pytest.mark.integration
def test_purge_terminal_link_tokens_does_not_commit_or_full_rollback(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    first_session = session_factory()
    second_session = session_factory()
    now = datetime(2026, 7, 25, 16, 10, tzinfo=UTC)
    cutoff = now - timedelta(days=TELEGRAM_LINK_TOKEN_TERMINAL_RETENTION_DAYS)
    try:
        user = add_user(setup_session, "+998900011006")
        token = add_token(
            setup_session,
            user,
            token_hash_value=token_hash(306),
            created_at=cutoff - timedelta(days=1),
            consumed_at=cutoff,
        )
        token_id = token.id
        setup_session.commit()
        session_spy = SessionSpy(first_session)

        deleted_count = purge_terminal_link_tokens(
            session_spy,
            now,
            batch_size=1,
        )
        still_visible_before_caller_commit = second_session.scalar(
            select(func.count())
            .select_from(TelegramLinkToken)
            .where(TelegramLinkToken.id == token_id)
        )

        assert deleted_count == 1
        assert session_spy.commit_called is False
        assert session_spy.rollback_called is False
        assert session_spy.close_called is False
        assert still_visible_before_caller_commit == 1
    finally:
        setup_session.close()
        first_session.rollback()
        first_session.close()
        second_session.rollback()
        second_session.close()
