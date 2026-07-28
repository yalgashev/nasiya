import inspect
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.telegram.polling_repository as polling_repository
from app.db import create_database_session_factory
from app.telegram.models import TelegramPollingState, TelegramUpdateFailure
from app.telegram.polling_repository import (
    PollingCursorRegressionError,
    advance_next_offset,
    delete_non_quarantined_update_failure,
    get_next_offset,
    increment_update_failure,
    load_or_create_polling_state,
    read_polling_health,
    record_update_failure_and_maybe_advance_cursor,
    update_polling_heartbeat,
)

NOW = datetime(2026, 7, 28, 2, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_polling_state_load_create_is_idempotent_and_initializes_zero(
    db_session: Session,
) -> None:
    first = load_or_create_polling_state(db_session)
    second = load_or_create_polling_state(db_session)

    assert first is second
    assert get_next_offset(db_session) == 0
    assert (
        db_session.scalar(select(func.count()).select_from(TelegramPollingState)) == 1
    )


@pytest.mark.integration
def test_cursor_is_monotonic_and_heartbeat_is_independent(
    db_session: Session,
) -> None:
    state = advance_next_offset(db_session, next_offset=17, now=NOW)
    updated = update_polling_heartbeat(
        db_session,
        now=NOW + timedelta(seconds=3),
        ready=True,
    )

    assert state.next_offset == 17
    assert updated.next_offset == 17
    assert updated.heartbeat_at == NOW + timedelta(seconds=3)
    assert updated.ready_at == NOW + timedelta(seconds=3)
    assert updated.heartbeat_at.utcoffset() == timedelta(0)

    with pytest.raises(PollingCursorRegressionError):
        advance_next_offset(db_session, next_offset=16, now=NOW)

    health = read_polling_health(db_session)
    assert health.next_offset == 17
    assert health.heartbeat_at == NOW + timedelta(seconds=3)


@pytest.mark.integration
def test_failure_attempts_quarantine_at_five_and_do_not_reset(
    db_session: Session,
) -> None:
    for attempt in range(1, 6):
        failure = increment_update_failure(
            db_session,
            update_id=42,
            failure_code="HANDLER_FAILURE",
            now=NOW + timedelta(seconds=attempt),
        )
        assert failure.attempt_count == attempt
        assert (failure.quarantined_at is not None) is (attempt == 5)

    retained = increment_update_failure(
        db_session,
        update_id=42,
        failure_code="OTHER_FAILURE",
        now=NOW + timedelta(minutes=1),
    )
    assert retained.attempt_count == 5
    assert retained.failure_code == "HANDLER_FAILURE"
    assert retained.last_failed_at == NOW + timedelta(seconds=5)


@pytest.mark.integration
def test_success_deletes_only_non_quarantined_failure(db_session: Session) -> None:
    increment_update_failure(
        db_session,
        update_id=51,
        failure_code="HANDLER_FAILURE",
        now=NOW,
    )
    assert delete_non_quarantined_update_failure(db_session, update_id=51) is True
    assert db_session.get(TelegramUpdateFailure, 51) is None

    for attempt in range(5):
        increment_update_failure(
            db_session,
            update_id=52,
            failure_code="HANDLER_FAILURE",
            now=NOW + timedelta(seconds=attempt),
        )
    assert delete_non_quarantined_update_failure(db_session, update_id=52) is False
    assert db_session.get(TelegramUpdateFailure, 52) is not None


@pytest.mark.integration
def test_quarantine_and_cursor_advance_commit_atomically(db_session: Session) -> None:
    load_or_create_polling_state(db_session)
    for attempt in range(4):
        record_update_failure_and_maybe_advance_cursor(
            db_session,
            update_id=70,
            failure_code="HANDLER_FAILURE",
            now=NOW + timedelta(seconds=attempt),
        )
    assert get_next_offset(db_session) == 0

    failure = record_update_failure_and_maybe_advance_cursor(
        db_session,
        update_id=70,
        failure_code="HANDLER_FAILURE",
        now=NOW + timedelta(seconds=5),
    )
    db_session.commit()

    assert failure.quarantined_at is not None
    assert get_next_offset(db_session) == 71


@pytest.mark.integration
def test_injected_cursor_failure_rolls_back_fifth_attempt(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_or_create_polling_state(db_session)
    for attempt in range(4):
        increment_update_failure(
            db_session,
            update_id=80,
            failure_code="HANDLER_FAILURE",
            now=NOW + timedelta(seconds=attempt),
        )
    db_session.commit()

    def fail_cursor(*args, **kwargs):
        raise RuntimeError("injected cursor failure")

    monkeypatch.setattr(polling_repository, "advance_next_offset", fail_cursor)
    with pytest.raises(RuntimeError, match="injected cursor failure"):
        record_update_failure_and_maybe_advance_cursor(
            db_session,
            update_id=80,
            failure_code="HANDLER_FAILURE",
            now=NOW + timedelta(seconds=5),
        )
    db_session.rollback()

    failure = db_session.get(TelegramUpdateFailure, 80)
    assert failure is not None
    assert failure.attempt_count == 4
    assert failure.quarantined_at is None
    assert get_next_offset(db_session) == 0


@pytest.mark.integration
def test_parallel_failure_upsert_has_no_lost_updates(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    barrier = Barrier(5)

    def record(attempt: int) -> None:
        with session_factory() as session:
            barrier.wait()
            increment_update_failure(
                session,
                update_id=90,
                failure_code="PARALLEL_FAILURE",
                now=NOW + timedelta(seconds=attempt),
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(record, range(5)))

    with session_factory() as session:
        failure = session.get(TelegramUpdateFailure, 90)
        assert failure is not None
        assert failure.attempt_count == 5
        assert failure.quarantined_at is not None


@pytest.mark.integration
@pytest.mark.parametrize(
    ("values", "constraint_name"),
    [
        (
            {
                "update_id": 101,
                "attempt_count": 0,
                "failure_code": "BAD_ATTEMPT",
                "first_failed_at": NOW,
                "last_failed_at": NOW,
            },
            "ck_telegram_update_failures_attempt_count",
        ),
        (
            {
                "update_id": 102,
                "attempt_count": 5,
                "failure_code": "NO_QUARANTINE",
                "first_failed_at": NOW,
                "last_failed_at": NOW,
            },
            "ck_telegram_update_failures_quarantine_state",
        ),
        (
            {
                "update_id": 103,
                "attempt_count": 1,
                "failure_code": "EARLY_QUARANTINE",
                "first_failed_at": NOW,
                "last_failed_at": NOW,
                "quarantined_at": NOW,
            },
            "ck_telegram_update_failures_quarantine_state",
        ),
    ],
)
def test_database_rejects_invalid_failure_states(
    db_session: Session,
    values: dict[str, object],
    constraint_name: str,
) -> None:
    db_session.add(TelegramUpdateFailure(**values))
    with pytest.raises(IntegrityError) as exc_info:
        db_session.flush()
    assert constraint_name in str(exc_info.value.orig)
    db_session.rollback()


def test_operational_schema_has_no_forbidden_payload_or_identity_fields() -> None:
    forbidden = {
        "raw_update",
        "message",
        "json",
        "payload",
        "token",
        "token_hash",
        "telegram_chat_id",
        "telegram_user_id",
        "user_id",
        "shop_id",
        "customer_id",
        "phone",
        "client_ip",
        "credential",
        "username",
        "exception",
        "traceback",
        "http_body",
        "sql",
        "metadata",
    }
    for model in (TelegramPollingState, TelegramUpdateFailure):
        assert forbidden.isdisjoint(model.__table__.columns.keys())


def test_polling_repository_keeps_transaction_ownership_with_caller() -> None:
    source = inspect.getsource(polling_repository)
    public_functions = (
        load_or_create_polling_state,
        advance_next_offset,
        update_polling_heartbeat,
        read_polling_health,
        increment_update_failure,
        delete_non_quarantined_update_failure,
        record_update_failure_and_maybe_advance_cursor,
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    for function in public_functions:
        parameters = list(inspect.signature(function).parameters.values())
        assert parameters[0].name == "session"
        assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in parameters[1:]
        )


@pytest.mark.integration
def test_database_schema_matches_approved_operational_tables(
    m2_test_database: Engine,
) -> None:
    database_inspector = sqlalchemy_inspect(m2_test_database)
    assert {
        "telegram_polling_state",
        "telegram_update_failures",
    }.issubset(database_inspector.get_table_names())
