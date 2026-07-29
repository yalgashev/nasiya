from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Final

from sqlalchemy.engine import Connection, Engine

OTP_DISPATCHER_ADVISORY_LOCK_KEY: Final = 0x4E415349594F5450
OTP_LOCK_ACQUISITION_SECONDS: Final = 60.0
OTP_LOCK_POLL_INTERVAL_SECONDS: Final = 1.0


class OtpDispatcherLockUnavailable(RuntimeError):
    pass


class OtpDispatcherLockCancelled(RuntimeError):
    pass


@dataclass
class OtpDispatcherLock:
    connection: Connection
    acquired: bool = False

    def try_acquire(self) -> bool:
        if self.acquired:
            return True
        self.acquired = bool(
            self.connection.exec_driver_sql(
                "SELECT pg_try_advisory_lock(%s)",
                (OTP_DISPATCHER_ADVISORY_LOCK_KEY,),
            ).scalar_one()
        )
        return self.acquired

    def release(self) -> None:
        if not self.acquired or self.connection.closed:
            return
        self.connection.exec_driver_sql(
            "SELECT pg_advisory_unlock(%s)",
            (OTP_DISPATCHER_ADVISORY_LOCK_KEY,),
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


def acquire_otp_dispatcher_lock(
    engine: Engine,
    *,
    deadline_seconds: float = OTP_LOCK_ACQUISITION_SECONDS,
    poll_interval_seconds: float = OTP_LOCK_POLL_INTERVAL_SECONDS,
    monotonic_clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
    cancelled: Callable[[], bool] = lambda: False,
) -> OtpDispatcherLock:
    if deadline_seconds < 0:
        raise ValueError("Lock deadline must be non-negative")
    if poll_interval_seconds <= 0:
        raise ValueError("Lock poll interval must be positive")

    dispatcher_lock = OtpDispatcherLock(connection=engine.connect())
    started_at = monotonic_clock()
    try:
        while True:
            if cancelled():
                raise OtpDispatcherLockCancelled(
                    "OTP dispatcher lock acquisition cancelled"
                )
            if dispatcher_lock.try_acquire():
                return dispatcher_lock

            remaining = deadline_seconds - (monotonic_clock() - started_at)
            if remaining <= 0:
                raise OtpDispatcherLockUnavailable(
                    "OTP dispatcher lock is already held"
                )
            sleeper(min(poll_interval_seconds, remaining))
    except Exception:
        dispatcher_lock.close()
        raise
