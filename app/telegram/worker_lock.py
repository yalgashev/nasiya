from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Final

from sqlalchemy.engine import Connection, Engine

TELEGRAM_POLLER_ADVISORY_LOCK_KEY: Final = 0x4E41534959415447
TELEGRAM_LOCK_ACQUISITION_SECONDS: Final = 60.0
TELEGRAM_LOCK_POLL_INTERVAL_SECONDS: Final = 1.0


class TelegramPollingLockUnavailable(RuntimeError):
    pass


class TelegramPollingLockCancelled(RuntimeError):
    pass


@dataclass
class TelegramPollingLock:
    connection: Connection
    acquired: bool = False

    def try_acquire(self) -> bool:
        if self.acquired:
            return True
        self.acquired = bool(
            self.connection.exec_driver_sql(
                "SELECT pg_try_advisory_lock(%s)",
                (TELEGRAM_POLLER_ADVISORY_LOCK_KEY,),
            ).scalar_one()
        )
        return self.acquired

    def release(self) -> None:
        if not self.acquired or self.connection.closed:
            return
        self.connection.exec_driver_sql(
            "SELECT pg_advisory_unlock(%s)",
            (TELEGRAM_POLLER_ADVISORY_LOCK_KEY,),
        )
        self.acquired = False

    def close(self) -> None:
        try:
            self.release()
        finally:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def acquire_telegram_polling_lock(
    engine: Engine,
    *,
    deadline_seconds: float = TELEGRAM_LOCK_ACQUISITION_SECONDS,
    poll_interval_seconds: float = TELEGRAM_LOCK_POLL_INTERVAL_SECONDS,
    monotonic_clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
    cancelled: Callable[[], bool] = lambda: False,
) -> TelegramPollingLock:
    if deadline_seconds < 0:
        raise ValueError("Lock deadline must be non-negative")
    if poll_interval_seconds <= 0:
        raise ValueError("Lock poll interval must be positive")

    polling_lock = TelegramPollingLock(connection=engine.connect())
    started_at = monotonic_clock()
    try:
        while True:
            if cancelled():
                raise TelegramPollingLockCancelled(
                    "Telegram polling lock acquisition cancelled"
                )
            if polling_lock.try_acquire():
                return polling_lock

            remaining = deadline_seconds - (monotonic_clock() - started_at)
            if remaining <= 0:
                raise TelegramPollingLockUnavailable(
                    "Telegram polling lock is already held"
                )
            sleeper(min(poll_interval_seconds, remaining))
    except Exception:
        polling_lock.close()
        raise
