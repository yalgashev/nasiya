import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.telegram.models import TelegramPollingState, TelegramUpdateFailure

TELEGRAM_POLLING_STATE_ID: Final = 1
TELEGRAM_FAILURE_QUARANTINE_ATTEMPTS: Final = 5
POSTGRES_BIGINT_MAX: Final = (1 << 63) - 1

_FAILURE_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class PollingCursorRegressionError(ValueError):
    pass


class PollingStateMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelegramPollingHealth:
    next_offset: int
    heartbeat_at: datetime | None
    ready_at: datetime | None
    updated_at: datetime


def load_or_create_polling_state(session: Session) -> TelegramPollingState:
    statement = (
        insert(TelegramPollingState)
        .values(id=TELEGRAM_POLLING_STATE_ID, next_offset=0)
        .on_conflict_do_nothing(index_elements=[TelegramPollingState.id])
    )
    session.execute(statement)
    state = session.get(TelegramPollingState, TELEGRAM_POLLING_STATE_ID)
    if state is None:
        raise RuntimeError("Telegram polling state initialization failed")
    return state


def lock_polling_state(session: Session) -> TelegramPollingState:
    statement = (
        select(TelegramPollingState)
        .where(TelegramPollingState.id == TELEGRAM_POLLING_STATE_ID)
        .with_for_update()
    )
    state = session.scalar(statement)
    if state is not None:
        return state

    load_or_create_polling_state(session)
    state = session.scalar(statement)
    if state is None:
        raise RuntimeError("Telegram polling state lock failed")
    return state


def get_next_offset(session: Session) -> int:
    return load_or_create_polling_state(session).next_offset


def advance_next_offset(
    session: Session,
    *,
    next_offset: int,
    now: datetime,
) -> TelegramPollingState:
    normalized_offset = _validate_bigint(next_offset, field_name="next_offset")
    current_time = _as_utc(now)
    state = lock_polling_state(session)
    if normalized_offset < state.next_offset:
        raise PollingCursorRegressionError("Telegram polling cursor cannot regress")
    if normalized_offset > state.next_offset:
        state.next_offset = normalized_offset
        state.updated_at = current_time
        session.flush()
    return state


def update_polling_heartbeat(
    session: Session,
    *,
    now: datetime,
    ready: bool | None = None,
) -> TelegramPollingState:
    current_time = _as_utc(now)
    state = lock_polling_state(session)
    state.heartbeat_at = current_time
    if ready is True and state.ready_at is None:
        state.ready_at = current_time
    elif ready is False:
        state.ready_at = None
    state.updated_at = current_time
    session.flush()
    return state


def read_polling_health(session: Session) -> TelegramPollingHealth:
    state = session.get(TelegramPollingState, TELEGRAM_POLLING_STATE_ID)
    if state is None:
        raise PollingStateMissingError("Telegram polling state is not initialized")
    return TelegramPollingHealth(
        next_offset=state.next_offset,
        heartbeat_at=state.heartbeat_at,
        ready_at=state.ready_at,
        updated_at=state.updated_at,
    )


def increment_update_failure(
    session: Session,
    *,
    update_id: int,
    failure_code: str,
    now: datetime,
) -> TelegramUpdateFailure:
    normalized_update_id = _validate_bigint(update_id, field_name="update_id")
    normalized_code = _validate_failure_code(failure_code)
    current_time = _as_utc(now)
    insert_statement = insert(TelegramUpdateFailure).values(
        update_id=normalized_update_id,
        attempt_count=1,
        failure_code=normalized_code,
        first_failed_at=current_time,
        last_failed_at=current_time,
        quarantined_at=None,
    )
    next_attempt = TelegramUpdateFailure.attempt_count + 1
    may_increment = (
        TelegramUpdateFailure.attempt_count < TELEGRAM_FAILURE_QUARANTINE_ATTEMPTS
    )
    reaches_quarantine = next_attempt >= TELEGRAM_FAILURE_QUARANTINE_ATTEMPTS
    earliest_failure = func.least(
        TelegramUpdateFailure.first_failed_at,
        insert_statement.excluded.first_failed_at,
    )
    latest_failure = func.greatest(
        TelegramUpdateFailure.last_failed_at,
        insert_statement.excluded.last_failed_at,
    )
    incoming_is_latest = (
        insert_statement.excluded.last_failed_at >= TelegramUpdateFailure.last_failed_at
    )
    statement = insert_statement.on_conflict_do_update(
        index_elements=[TelegramUpdateFailure.update_id],
        set_={
            "attempt_count": case(
                (may_increment, next_attempt),
                else_=TelegramUpdateFailure.attempt_count,
            ),
            "failure_code": case(
                (
                    may_increment & incoming_is_latest,
                    insert_statement.excluded.failure_code,
                ),
                else_=TelegramUpdateFailure.failure_code,
            ),
            "first_failed_at": case(
                (may_increment, earliest_failure),
                else_=TelegramUpdateFailure.first_failed_at,
            ),
            "last_failed_at": case(
                (may_increment, latest_failure),
                else_=TelegramUpdateFailure.last_failed_at,
            ),
            "quarantined_at": case(
                (
                    may_increment & reaches_quarantine,
                    latest_failure,
                ),
                else_=TelegramUpdateFailure.quarantined_at,
            ),
        },
    ).returning(TelegramUpdateFailure)
    return session.execute(
        statement,
        execution_options={"populate_existing": True},
    ).scalar_one()


def delete_non_quarantined_update_failure(
    session: Session,
    *,
    update_id: int,
) -> bool:
    normalized_update_id = _validate_bigint(update_id, field_name="update_id")
    statement = delete(TelegramUpdateFailure).where(
        TelegramUpdateFailure.update_id == normalized_update_id,
        TelegramUpdateFailure.quarantined_at.is_(None),
    )
    result = session.execute(statement)
    return bool(result.rowcount)


def record_update_failure_and_maybe_advance_cursor(
    session: Session,
    *,
    update_id: int,
    failure_code: str,
    now: datetime,
) -> TelegramUpdateFailure:
    failure = increment_update_failure(
        session,
        update_id=update_id,
        failure_code=failure_code,
        now=now,
    )
    if failure.quarantined_at is not None:
        if update_id == POSTGRES_BIGINT_MAX:
            raise OverflowError("Telegram update id cannot advance beyond BIGINT")
        advance_next_offset(
            session,
            next_offset=update_id + 1,
            now=now,
        )
    return failure


def _validate_bigint(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0 or value > POSTGRES_BIGINT_MAX:
        raise ValueError(f"{field_name} is outside the PostgreSQL BIGINT range")
    return value


def _validate_failure_code(value: str) -> str:
    if not _FAILURE_CODE_PATTERN.fullmatch(value):
        raise ValueError("failure_code must be a sanitized stable code")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
