from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Event as ThreadEvent
from time import monotonic
from typing import Final

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db import create_database_engine, create_database_session_factory
from app.settings import Settings, TelegramWorkerSettingsError
from app.telegram.bot_api import (
    TELEGRAM_ALLOWED_UPDATES,
    TELEGRAM_LONG_POLL_SECONDS,
    TelegramApiError,
    TelegramApiErrorCode,
    TelegramBackoffPolicy,
    TelegramBotApiClient,
    TelegramPreflightStatus,
    TelegramUpdateEnvelope,
    create_telegram_http_client,
    run_telegram_preflight,
)
from app.telegram.polling_repository import (
    PollingStateMissingError,
    get_next_offset,
    load_or_create_polling_state,
    read_polling_health,
    update_polling_heartbeat,
)
from app.telegram.worker_lock import (
    TelegramPollingLockCancelled,
    TelegramPollingLockUnavailable,
    acquire_telegram_polling_lock,
)

TELEGRAM_HEARTBEAT_INTERVAL_SECONDS: Final = 10.0
TELEGRAM_HEARTBEAT_STALE_SECONDS: Final = 60.0

LOGGER = logging.getLogger("nasiya.telegram.worker")


class WorkerExitCode:
    OK = 0
    CONFIGURATION = 2
    LOCK_UNAVAILABLE = 3
    PREFLIGHT = 4
    RUNTIME = 5
    UNHEALTHY = 1


class TelegramWorkerFatalError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Telegram worker stopped ({code})")


class PollingUpdateOutcome(StrEnum):
    TERMINAL = "TERMINAL"
    RETRY = "RETRY"


UpdateProcessor = Callable[
    [TelegramUpdateEnvelope],
    Awaitable[PollingUpdateOutcome],
]


class ShutdownController:
    def __init__(self) -> None:
        self._thread_event = ThreadEvent()
        self._async_event: asyncio.Event | None = None

    @property
    def requested(self) -> bool:
        return self._thread_event.is_set()

    def bind_async_loop(self) -> None:
        if self._async_event is None:
            self._async_event = asyncio.Event()
        if self.requested:
            self._async_event.set()

    def request(self) -> None:
        self._thread_event.set()
        if self._async_event is not None:
            self._async_event.set()

    def wait_sync(self, seconds: float) -> None:
        self._thread_event.wait(seconds)

    async def wait_async(self, seconds: float) -> bool:
        self.bind_async_loop()
        if self.requested:
            return True
        assert self._async_event is not None
        try:
            await asyncio.wait_for(self._async_event.wait(), timeout=seconds)
        except TimeoutError:
            return False
        return True

    async def wait_until_requested(self) -> None:
        self.bind_async_loop()
        assert self._async_event is not None
        await self._async_event.wait()


def load_worker_settings() -> Settings:
    env_file = Path(__file__).resolve().parents[2] / ".env"
    return Settings(_env_file=env_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.telegram.worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    subparsers.add_parser("healthcheck")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_loader: Callable[[], Settings] = load_worker_settings,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = settings_loader()
    except Exception:
        _write_operational_error("WORKER_SETTINGS_INVALID")
        return WorkerExitCode.CONFIGURATION

    if args.command == "healthcheck":
        return run_healthcheck_command(settings)
    return run_worker_command(settings)


def run_worker_command(
    settings: Settings,
    *,
    shutdown: ShutdownController | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    processor: UpdateProcessor | None = None,
) -> int:
    controller = shutdown or ShutdownController()
    previous_handlers = _install_signal_handlers(controller)
    try:
        asyncio.run(
            run_worker(
                settings,
                shutdown=controller,
                transport=transport,
                processor=processor,
            )
        )
    except TelegramWorkerSettingsError:
        _write_operational_error("WORKER_CREDENTIALS_MISSING")
        return WorkerExitCode.CONFIGURATION
    except TelegramPollingLockUnavailable:
        _write_operational_error("WORKER_LOCK_UNAVAILABLE")
        return WorkerExitCode.LOCK_UNAVAILABLE
    except TelegramPollingLockCancelled:
        return WorkerExitCode.OK
    except TelegramWorkerFatalError as exc:
        _write_operational_error(exc.code)
        return WorkerExitCode.PREFLIGHT
    except (SQLAlchemyError, OSError, RuntimeError):
        _write_operational_error("WORKER_RUNTIME_FAILURE")
        return WorkerExitCode.RUNTIME
    finally:
        _restore_signal_handlers(previous_handlers)
    return WorkerExitCode.OK


async def run_worker(
    settings: Settings,
    *,
    shutdown: ShutdownController,
    transport: httpx.AsyncBaseTransport | None,
    processor: UpdateProcessor | None,
    monotonic_clock: Callable[[], float] = monotonic,
) -> None:
    credentials = settings.require_telegram_worker_credentials()
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    polling_lock = None
    heartbeat_task: asyncio.Task[None] | None = None
    shutdown.bind_async_loop()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        polling_lock = acquire_telegram_polling_lock(
            engine,
            monotonic_clock=monotonic_clock,
            sleeper=shutdown.wait_sync,
            cancelled=lambda: shutdown.requested,
        )
        if shutdown.requested:
            return

        async with create_telegram_http_client(
            credentials,
            transport=transport,
        ) as http_client:
            client = TelegramBotApiClient(http_client=http_client)
            await _run_preflight_until_ready(
                client,
                configured_username=credentials.bot_username,
                shutdown=shutdown,
            )
            if shutdown.requested:
                return

            _initialize_polling_state(session_factory)
            resolved_processor = processor
            if resolved_processor is None:
                from app.telegram.update_processing import TelegramUpdateProcessor

                resolved_processor = TelegramUpdateProcessor(
                    session_factory,
                    sleeper=shutdown.wait_async,
                )
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(session_factory, shutdown=shutdown)
            )
            try:
                await run_polling_loop(
                    client,
                    session_factory=session_factory,
                    processor=resolved_processor,
                    shutdown=shutdown,
                    heartbeat_task=heartbeat_task,
                )
            finally:
                shutdown.request()
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                heartbeat_task = None
                _mark_worker_not_ready(session_factory)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if polling_lock is not None:
            polling_lock.close()
        engine.dispose()


async def run_polling_loop(
    client: TelegramBotApiClient,
    *,
    session_factory: sessionmaker[Session],
    processor: UpdateProcessor,
    shutdown: ShutdownController,
    heartbeat_task: asyncio.Task[None] | None = None,
) -> None:
    backoff = TelegramBackoffPolicy()
    retry_attempt = 0
    while not shutdown.requested:
        _raise_heartbeat_failure(heartbeat_task)
        offset = _read_next_offset(session_factory)
        try:
            updates = await _await_or_shutdown(
                client.get_updates(
                    offset=offset,
                    timeout=TELEGRAM_LONG_POLL_SECONDS,
                    allowed_updates=TELEGRAM_ALLOWED_UPDATES,
                ),
                shutdown=shutdown,
            )
        except TelegramApiError as exc:
            if not exc.is_transient:
                raise TelegramWorkerFatalError(exc.code.value) from None
            delay = backoff.delay_seconds(exc, attempt=retry_attempt)
            retry_attempt += 1
            await shutdown.wait_async(delay)
            continue

        retry_attempt = 0
        if updates is None or shutdown.requested:
            _raise_heartbeat_failure(heartbeat_task)
            return
        ordered_updates = sorted(
            {update.update_id: update for update in updates}.values(),
            key=lambda update: update.update_id,
        )
        for update in ordered_updates:
            if shutdown.requested:
                return
            outcome = await processor(update)
            if outcome is PollingUpdateOutcome.RETRY:
                break


def worker_health_is_fresh(
    session: Session,
    *,
    now: datetime,
    stale_after_seconds: float = TELEGRAM_HEARTBEAT_STALE_SECONDS,
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Healthcheck time must be timezone-aware")
    if stale_after_seconds <= 0:
        raise ValueError("Healthcheck stale threshold must be positive")
    try:
        health = read_polling_health(session)
    except PollingStateMissingError:
        return False
    if health.ready_at is None or health.heartbeat_at is None:
        return False
    current_time = now.astimezone(UTC)
    return health.heartbeat_at >= current_time - timedelta(seconds=stale_after_seconds)


def run_healthcheck_command(
    settings: Settings,
    *,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    try:
        with session_factory() as session:
            if worker_health_is_fresh(session, now=now_factory()):
                return WorkerExitCode.OK
    except (SQLAlchemyError, OSError):
        pass
    finally:
        engine.dispose()
    return WorkerExitCode.UNHEALTHY


async def _run_preflight_until_ready(
    client: TelegramBotApiClient,
    *,
    configured_username,
    shutdown: ShutdownController,
) -> None:
    backoff = TelegramBackoffPolicy()
    attempt = 0
    while not shutdown.requested:
        result = await run_telegram_preflight(
            client,
            configured_username=configured_username,
        )
        if result.status is TelegramPreflightStatus.READY:
            return
        if result.status is TelegramPreflightStatus.FATAL_FAILURE:
            raise TelegramWorkerFatalError(result.code.value)
        transient_error = TelegramApiError(
            code=TelegramApiErrorCode.TRANSIENT_NETWORK,
        )
        delay = backoff.delay_seconds(transient_error, attempt=attempt)
        attempt += 1
        await shutdown.wait_async(delay)


def _initialize_polling_state(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
        update_polling_heartbeat(session, now=now, ready=True)


def _mark_worker_not_ready(
    session_factory: sessionmaker[Session],
) -> None:
    try:
        with session_factory.begin() as session:
            update_polling_heartbeat(session, now=datetime.now(UTC), ready=False)
    except SQLAlchemyError:
        LOGGER.error("Telegram worker readiness cleanup failed")


async def _heartbeat_loop(
    session_factory: sessionmaker[Session],
    *,
    shutdown: ShutdownController,
) -> None:
    while not shutdown.requested:
        if await shutdown.wait_async(TELEGRAM_HEARTBEAT_INTERVAL_SECONDS):
            return
        try:
            with session_factory.begin() as session:
                update_polling_heartbeat(
                    session,
                    now=datetime.now(UTC),
                    ready=None,
                )
        except SQLAlchemyError:
            shutdown.request()
            raise


def _read_next_offset(session_factory: sessionmaker[Session]) -> int:
    with session_factory.begin() as session:
        return get_next_offset(session)


async def _await_or_shutdown(
    operation: Awaitable[tuple[TelegramUpdateEnvelope, ...]],
    *,
    shutdown: ShutdownController,
) -> tuple[TelegramUpdateEnvelope, ...] | None:
    operation_task = asyncio.create_task(operation)
    shutdown_task = asyncio.create_task(shutdown.wait_until_requested())
    done, pending = await asyncio.wait(
        {operation_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if shutdown_task in done:
        operation_task.cancel()
        await asyncio.gather(operation_task, return_exceptions=True)
        return None
    return await operation_task


def _raise_heartbeat_failure(task: asyncio.Task[None] | None) -> None:
    if task is None or not task.done() or task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        raise exception


def _install_signal_handlers(
    shutdown: ShutdownController,
) -> dict[signal.Signals, object]:
    previous_handlers = {}
    if sys.platform == "win32":
        supported_signals = (signal.SIGINT,)
    else:
        supported_signals = (signal.SIGTERM, signal.SIGINT)
    for supported_signal in supported_signals:
        previous_handlers[supported_signal] = signal.getsignal(supported_signal)
        signal.signal(supported_signal, lambda _signum, _frame: shutdown.request())
    return previous_handlers


def _restore_signal_handlers(
    previous_handlers: dict[signal.Signals, object],
) -> None:
    for supported_signal, previous_handler in previous_handlers.items():
        signal.signal(supported_signal, previous_handler)


def _write_operational_error(code: str) -> None:
    print(code, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
