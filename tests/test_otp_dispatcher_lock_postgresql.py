import inspect
from collections.abc import Generator

import pytest
from sqlalchemy.engine import Engine

import app.otp.worker_lock as otp_worker_lock_module
from app.otp.worker_lock import (
    OTP_DISPATCHER_ADVISORY_LOCK_KEY,
    OtpDispatcherLockCancelled,
    OtpDispatcherLockUnavailable,
    acquire_otp_dispatcher_lock,
)
from app.telegram.worker_lock import TELEGRAM_POLLER_ADVISORY_LOCK_KEY


@pytest.fixture(autouse=True)
def release_process_advisory_locks(
    test_database_engine: Engine,
) -> Generator[None, None, None]:
    yield
    with test_database_engine.connect() as connection:
        connection.exec_driver_sql("SELECT pg_advisory_unlock_all()")


def test_otp_dispatcher_lock_key_is_stable_non_secret_and_distinct() -> None:
    source = inspect.getsource(otp_worker_lock_module)

    assert -(1 << 63) <= OTP_DISPATCHER_ADVISORY_LOCK_KEY < (1 << 63)
    assert OTP_DISPATCHER_ADVISORY_LOCK_KEY != TELEGRAM_POLLER_ADVISORY_LOCK_KEY
    assert "hash(" not in source
    assert "bot_token" not in source
    assert "chat_id" not in source
    assert "pg_advisory_lock(" not in source
    assert "pg_try_advisory_lock" in source


@pytest.mark.integration
def test_otp_dispatcher_lock_is_owned_until_release(
    test_database_engine: Engine,
) -> None:
    first = acquire_otp_dispatcher_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    try:
        with pytest.raises(OtpDispatcherLockUnavailable):
            acquire_otp_dispatcher_lock(
                test_database_engine,
                deadline_seconds=0,
            )
    finally:
        first.close()

    second = acquire_otp_dispatcher_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    second.close()


@pytest.mark.integration
def test_otp_dispatcher_lock_timeout_and_cancel_close_waiting_connection(
    test_database_engine: Engine,
) -> None:
    first = acquire_otp_dispatcher_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    elapsed = 0.0
    sleeps = []

    def clock() -> float:
        return elapsed

    def sleeper(seconds: float) -> None:
        nonlocal elapsed
        sleeps.append(seconds)
        elapsed += seconds

    try:
        with pytest.raises(OtpDispatcherLockUnavailable):
            acquire_otp_dispatcher_lock(
                test_database_engine,
                deadline_seconds=2.5,
                poll_interval_seconds=1,
                monotonic_clock=clock,
                sleeper=sleeper,
            )
        assert sleeps == [1, 1, 0.5]

        cancelled = False

        def cancel_sleeper(_seconds: float) -> None:
            nonlocal cancelled
            cancelled = True

        with pytest.raises(OtpDispatcherLockCancelled):
            acquire_otp_dispatcher_lock(
                test_database_engine,
                deadline_seconds=60,
                sleeper=cancel_sleeper,
                cancelled=lambda: cancelled,
            )
    finally:
        first.close()
