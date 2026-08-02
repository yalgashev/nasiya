from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event as ThreadEvent
from time import monotonic
from typing import Final

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db import create_database_engine, create_database_session_factory
from app.otp.dispatch_service import (
    PreparedOtpDispatch,
    prepare_next_otp_dispatch,
    record_otp_delivery_result,
    recover_stale_prepared_dispatches,
)
from app.otp.provider import (
    OtpDeliveryProvider,
    OtpDeliverySendResult,
    OtpDeliverySendStatus,
    TelegramOtpProvider,
)
from app.otp.repository import (
    OtpDispatcherStateMissingError,
    mark_dispatcher_heartbeat,
    read_dispatcher_health,
)
from app.otp.worker_lock import (
    OtpDispatcherLockCancelled,
    OtpDispatcherLockUnavailable,
    acquire_otp_dispatcher_lock,
)
from app.settings import OtpHmacKeySettingsError, Settings, TelegramWorkerSettingsError
from app.telegram.bot_api import (
    TelegramApiError,
    TelegramApiErrorCode,
    TelegramBackoffPolicy,
    TelegramBotApiClient,
    TelegramPreflightStatus,
    create_telegram_http_client,
    run_telegram_preflight,
)

OTP_DISPATCHER_HEALTH_STALE_SECONDS: Final = 60.0


class OtpDispatcherExitCode:
    OK = 0
    CONFIGURATION = 2
    LOCK_UNAVAILABLE = 3
    PREFLIGHT = 4
    RUNTIME = 5
    UNHEALTHY = 1


class OtpDispatcherFatalError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"OTP dispatcher stopped ({code})")


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


def load_dispatcher_settings() -> Settings:
    env_file = Path(__file__).resolve().parents[2] / ".env"
    return Settings(_env_file=env_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.otp.dispatcher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run")
    subparsers.add_parser("healthcheck")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_loader: Callable[[], Settings] = load_dispatcher_settings,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = settings_loader()
    except Exception:
        _write_operational_error("OTP_DISPATCHER_SETTINGS_INVALID")
        return OtpDispatcherExitCode.CONFIGURATION

    if args.command == "healthcheck":
        return run_healthcheck_command(settings)
    return run_dispatcher_command(settings)


def run_dispatcher_command(
    settings: Settings,
    *,
    shutdown: ShutdownController | None = None,
    transport=None,
    provider: OtpDeliveryProvider | None = None,
) -> int:
    controller = shutdown or ShutdownController()
    previous_handlers = _install_signal_handlers(controller)
    try:
        asyncio.run(
            run_dispatcher(
                settings,
                shutdown=controller,
                transport=transport,
                provider=provider,
            )
        )
    except OtpHmacKeySettingsError:
        _write_operational_error("OTP_DISPATCHER_SECRET_MISSING")
        return OtpDispatcherExitCode.CONFIGURATION
    except TelegramWorkerSettingsError:
        _write_operational_error("OTP_DISPATCHER_CREDENTIALS_MISSING")
        return OtpDispatcherExitCode.CONFIGURATION
    except OtpDispatcherLockUnavailable:
        _write_operational_error("OTP_DISPATCHER_LOCK_UNAVAILABLE")
        return OtpDispatcherExitCode.LOCK_UNAVAILABLE
    except OtpDispatcherLockCancelled:
        return OtpDispatcherExitCode.OK
    except OtpDispatcherFatalError as exc:
        _write_operational_error(exc.code)
        return OtpDispatcherExitCode.PREFLIGHT
    except (SQLAlchemyError, OSError, RuntimeError):
        _write_operational_error("OTP_DISPATCHER_RUNTIME_FAILURE")
        return OtpDispatcherExitCode.RUNTIME
    finally:
        _restore_signal_handlers(previous_handlers)
    return OtpDispatcherExitCode.OK


async def run_dispatcher(
    settings: Settings,
    *,
    shutdown: ShutdownController,
    transport,
    provider: OtpDeliveryProvider | None = None,
    monotonic_clock: Callable[[], float] = monotonic,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    credentials = settings.require_telegram_worker_credentials()
    otp_hmac_key = settings.require_otp_hmac_key()
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    dispatcher_lock = None
    heartbeat_task: asyncio.Task[None] | None = None
    shutdown.bind_async_loop()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        dispatcher_lock = acquire_otp_dispatcher_lock(
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

            resolved_provider = provider or TelegramOtpProvider(
                bot_api_client=client,
                send_timeout_seconds=settings.otp_send_timeout_seconds,
            )
            _initialize_dispatcher_state(
                session_factory,
                now=now_factory(),
            )
            heartbeat_task = asyncio.create_task(
                _heartbeat_loop(
                    session_factory,
                    settings=settings,
                    shutdown=shutdown,
                    now_factory=now_factory,
                )
            )
            try:
                await run_dispatch_loop(
                    resolved_provider,
                    session_factory=session_factory,
                    otp_hmac_key=otp_hmac_key,
                    settings=settings,
                    shutdown=shutdown,
                    heartbeat_task=heartbeat_task,
                    now_factory=now_factory,
                )
            finally:
                shutdown.request()
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                heartbeat_task = None
                _mark_dispatcher_not_ready(session_factory, now=now_factory())
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if dispatcher_lock is not None:
            dispatcher_lock.close()
        engine.dispose()


async def run_dispatch_loop(
    provider: OtpDeliveryProvider,
    *,
    session_factory: sessionmaker[Session],
    otp_hmac_key: SecretStr,
    settings: Settings,
    shutdown: ShutdownController,
    heartbeat_task: asyncio.Task[None] | None = None,
    now_factory: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    while not shutdown.requested:
        _raise_heartbeat_failure(heartbeat_task)
        processed_count = 0
        for _ in range(settings.otp_dispatch_batch_size):
            if shutdown.requested:
                return
            prepared = _prepare_next_item(
                session_factory,
                otp_hmac_key=otp_hmac_key,
                settings=settings,
                now=now_factory(),
            )
            if prepared is None:
                break

            result = await _send_prepared_otp(
                provider,
                prepared=prepared,
                shutdown=shutdown,
            )
            if result is None:
                return

            _record_delivery_result(
                session_factory,
                prepared=prepared,
                result=result,
                now=now_factory(),
            )
            processed_count += 1

        recovered_count = _recover_stale_prepared(
            session_factory,
            settings=settings,
            now=now_factory(),
        )
        _raise_heartbeat_failure(heartbeat_task)
        if processed_count == 0 and recovered_count == 0:
            await shutdown.wait_async(settings.otp_dispatch_poll_seconds)


def dispatcher_health_is_fresh(
    session: Session,
    *,
    now: datetime,
    stale_after_seconds: float = OTP_DISPATCHER_HEALTH_STALE_SECONDS,
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Healthcheck time must be timezone-aware")
    if stale_after_seconds <= 0:
        raise ValueError("Healthcheck stale threshold must be positive")
    try:
        health = read_dispatcher_health(session)
    except OtpDispatcherStateMissingError:
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
            if dispatcher_health_is_fresh(
                session,
                now=now_factory(),
                stale_after_seconds=settings.otp_dispatch_stale_seconds,
            ):
                return OtpDispatcherExitCode.OK
    except (SQLAlchemyError, OSError):
        pass
    finally:
        engine.dispose()
    return OtpDispatcherExitCode.UNHEALTHY


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
            raise OtpDispatcherFatalError(result.code.value)
        transient_error = TelegramApiError(
            code=TelegramApiErrorCode.TRANSIENT_NETWORK,
        )
        delay = backoff.delay_seconds(transient_error, attempt=attempt)
        attempt += 1
        await shutdown.wait_async(delay)


def _initialize_dispatcher_state(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
) -> None:
    with session_factory.begin() as session:
        mark_dispatcher_heartbeat(session, now=now, ready=True)


def _mark_dispatcher_not_ready(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
) -> None:
    try:
        with session_factory.begin() as session:
            mark_dispatcher_heartbeat(session, now=now, ready=False)
    except SQLAlchemyError:
        pass


async def _heartbeat_loop(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    shutdown: ShutdownController,
    now_factory: Callable[[], datetime],
) -> None:
    while not shutdown.requested:
        if await shutdown.wait_async(settings.otp_dispatch_heartbeat_seconds):
            return
        try:
            with session_factory.begin() as session:
                mark_dispatcher_heartbeat(
                    session,
                    now=now_factory(),
                    ready=None,
                )
        except SQLAlchemyError:
            shutdown.request()
            raise


def _prepare_next_item(
    session_factory: sessionmaker[Session],
    *,
    otp_hmac_key: SecretStr,
    settings: Settings,
    now: datetime,
) -> PreparedOtpDispatch | None:
    with session_factory.begin() as session:
        return prepare_next_otp_dispatch(
            session,
            otp_hmac_key=otp_hmac_key,
            now=now,
            ttl_seconds=settings.otp_login_ttl_seconds,
            registration_ttl_seconds=(
                settings.require_registration_otp_config().ttl_seconds
            ),
            claim_stale_seconds=settings.otp_dispatch_claim_stale_seconds,
        )


async def _send_prepared_otp(
    provider: OtpDeliveryProvider,
    *,
    prepared: PreparedOtpDispatch,
    shutdown: ShutdownController,
) -> OtpDeliverySendResult | None:
    try:
        return await _await_or_shutdown(
            provider.send_otp(
                target=prepared.target,
                code=prepared.code,
                locale=prepared.locale,
                ttl_seconds=prepared.ttl_seconds,
                purpose=prepared.purpose,
            ),
            shutdown=shutdown,
        )
    except Exception:
        return OtpDeliverySendResult(
            status=OtpDeliverySendStatus.UNKNOWN,
            failure_code="OTP_UNKNOWN",
        )


def _record_delivery_result(
    session_factory: sessionmaker[Session],
    *,
    prepared: PreparedOtpDispatch,
    result: OtpDeliverySendResult,
    now: datetime,
) -> None:
    with session_factory.begin() as session:
        record_otp_delivery_result(
            session,
            dispatch_id=prepared.dispatch_id,
            result=result,
            now=now,
        )


def _recover_stale_prepared(
    session_factory: sessionmaker[Session],
    *,
    settings: Settings,
    now: datetime,
) -> int:
    with session_factory.begin() as session:
        return recover_stale_prepared_dispatches(
            session,
            now=now,
            stale_seconds=settings.otp_dispatch_stale_seconds,
            limit=settings.otp_dispatch_batch_size,
        )


async def _await_or_shutdown(
    operation: Awaitable[OtpDeliverySendResult],
    *,
    shutdown: ShutdownController,
) -> OtpDeliverySendResult | None:
    operation_task = asyncio.create_task(operation)
    shutdown_task = asyncio.create_task(shutdown.wait_until_requested())
    done, pending = await asyncio.wait(
        {operation_task, shutdown_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if operation_task in done:
        shutdown_task.cancel()
        await asyncio.gather(shutdown_task, return_exceptions=True)
        return await operation_task

    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    operation_task.cancel()
    await asyncio.gather(operation_task, return_exceptions=True)
    return None


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
    sys.stderr.write(f"{code}\n")


if __name__ == "__main__":
    raise SystemExit(main())
