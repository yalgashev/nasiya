import inspect
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.engine import Engine

import app.telegram.worker_lock as worker_lock_module
from app.telegram.worker_lock import (
    TELEGRAM_POLLER_ADVISORY_LOCK_KEY,
    TelegramPollingLockCancelled,
    TelegramPollingLockUnavailable,
    acquire_telegram_polling_lock,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def release_process_advisory_locks(
    test_database_engine: Engine,
) -> Generator[None, None, None]:
    yield
    with test_database_engine.connect() as connection:
        connection.exec_driver_sql("SELECT pg_advisory_unlock_all()")


def test_advisory_lock_key_is_stable_non_secret_signed_64_bit() -> None:
    source = inspect.getsource(worker_lock_module)

    assert -(1 << 63) <= TELEGRAM_POLLER_ADVISORY_LOCK_KEY < (1 << 63)
    assert "hash(" not in source
    assert "bot_token" not in source
    assert "chat_id" not in source
    assert "pg_advisory_lock(" not in source
    assert "pg_try_advisory_lock" in source


@pytest.mark.integration
def test_first_connection_owns_lock_until_explicit_release(
    test_database_engine: Engine,
) -> None:
    first = acquire_telegram_polling_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    try:
        with pytest.raises(TelegramPollingLockUnavailable):
            acquire_telegram_polling_lock(
                test_database_engine,
                deadline_seconds=0,
            )
    finally:
        first.close()

    second = acquire_telegram_polling_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    second.close()


@pytest.mark.integration
def test_second_connection_acquires_after_release_during_bounded_wait(
    test_database_engine: Engine,
) -> None:
    first = acquire_telegram_polling_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    elapsed = 0.0

    def clock() -> float:
        return elapsed

    def sleeper(seconds: float) -> None:
        nonlocal elapsed
        elapsed += seconds
        first.close()

    second = acquire_telegram_polling_lock(
        test_database_engine,
        deadline_seconds=2,
        poll_interval_seconds=1,
        monotonic_clock=clock,
        sleeper=sleeper,
    )
    try:
        assert second.acquired is True
        assert elapsed == 1
    finally:
        second.close()


@pytest.mark.integration
def test_lock_timeout_is_deterministic_and_closes_waiting_connection(
    test_database_engine: Engine,
) -> None:
    first = acquire_telegram_polling_lock(
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
        with pytest.raises(TelegramPollingLockUnavailable):
            acquire_telegram_polling_lock(
                test_database_engine,
                deadline_seconds=2.5,
                poll_interval_seconds=1,
                monotonic_clock=clock,
                sleeper=sleeper,
            )
        assert sleeps == [1, 1, 0.5]
    finally:
        first.close()


@pytest.mark.integration
def test_lock_wait_can_be_cancelled_without_leaking_connection(
    test_database_engine: Engine,
) -> None:
    first = acquire_telegram_polling_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    cancelled = False

    def sleeper(_seconds: float) -> None:
        nonlocal cancelled
        cancelled = True

    try:
        with pytest.raises(TelegramPollingLockCancelled):
            acquire_telegram_polling_lock(
                test_database_engine,
                deadline_seconds=60,
                sleeper=sleeper,
                cancelled=lambda: cancelled,
            )
    finally:
        first.close()


@pytest.mark.integration
def test_advisory_lock_coordinates_two_processes(
    test_database_engine: Engine,
) -> None:
    first = acquire_telegram_polling_lock(
        test_database_engine,
        deadline_seconds=0,
    )
    script = textwrap.dedent(
        """
        import os
        from sqlalchemy import create_engine
        from app.telegram.worker_lock import acquire_telegram_polling_lock

        engine = create_engine(os.environ["TEST_DATABASE_URL"])
        lock = acquire_telegram_polling_lock(
            engine,
            deadline_seconds=5,
            poll_interval_seconds=0.05,
        )
        lock.close()
        engine.dispose()
        """
    )
    environment = os.environ.copy()
    environment["TEST_DATABASE_URL"] = test_database_engine.url.render_as_string(
        hide_password=False
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.25)
        assert process.poll() is None
        first.close()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0
        assert stdout == ""
        assert stderr == ""
    finally:
        first.close()
        if process.poll() is None:
            process.kill()
            process.communicate()
