import asyncio
import inspect
import subprocess
import sys
from collections.abc import Awaitable, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.main as app_main
import app.telegram.worker as worker_module
from app.db import create_database_session_factory
from app.settings import Settings
from app.telegram.bot import TelegramBotUsername
from app.telegram.bot_api import (
    TelegramApiError,
    TelegramApiErrorCode,
    TelegramUpdateEnvelope,
)
from app.telegram.models import TelegramPollingState
from app.telegram.polling_repository import (
    get_next_offset,
    load_or_create_polling_state,
    update_polling_heartbeat,
)
from app.telegram.worker import (
    TELEGRAM_HEARTBEAT_INTERVAL_SECONDS,
    PollingUpdateOutcome,
    ShutdownController,
    WorkerExitCode,
    run_healthcheck_command,
    run_polling_loop,
    run_worker,
    run_worker_command,
    worker_health_is_fresh,
)
from app.telegram.worker_lock import (
    TelegramPollingLockUnavailable,
    acquire_telegram_polling_lock,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 28, 4, 0, tzinfo=UTC)
RAW_TOKEN = "123456789:WorkerSecretToken"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(
    engine: Engine,
    *,
    with_credentials: bool = True,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=SecretStr("worker-test-hmac-key-at-least-32-characters"),
        telegram_bot_username=(
            TelegramBotUsername("Nasiya_LinkBot") if with_credentials else None
        ),
        telegram_bot_token=SecretStr(RAW_TOKEN) if with_credentials else None,
    )


def run(coroutine: Awaitable[object]):
    return asyncio.run(coroutine)


def test_worker_fresh_process_resolves_all_registered_foreign_key_targets() -> None:
    script = """
import app.telegram.worker  # noqa: F401
import app.telegram.update_processing  # noqa: F401
from app.db import Base, create_database_engine
from app.settings import Settings

engine = create_database_engine(
    Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url="postgresql+psycopg://test:test@127.0.0.1:1/test",
        session_cookie_secure=False,
        rate_limit_hmac_key="fresh-process-test-key-at-least-32-chars",
    )
)

missing = sorted(
    {
        foreign_key.target_fullname.split(".", 1)[0]
        for table in Base.metadata.tables.values()
        for foreign_key in table.foreign_keys
        if foreign_key.target_fullname.split(".", 1)[0]
        not in Base.metadata.tables
    }
)
if missing:
    raise SystemExit(f"unresolved worker metadata targets: {','.join(missing)}")
for table in Base.metadata.tables.values():
    for foreign_key in table.foreign_keys:
        foreign_key.column
tuple(Base.metadata.sorted_tables)
engine.dispose()
print("WORKER_MODEL_METADATA_COMPLETE")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "WORKER_MODEL_METADATA_COMPLETE\n"


class FakePollingClient:
    def __init__(
        self,
        outcomes: list[tuple[TelegramUpdateEnvelope, ...] | TelegramApiError],
        shutdown: ShutdownController,
    ) -> None:
        self.outcomes = outcomes
        self.shutdown = shutdown
        self.offsets: list[int] = []

    async def get_updates(self, *, offset, timeout, allowed_updates):
        self.offsets.append(offset)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, TelegramApiError):
            raise outcome
        return outcome


class ImmediateShutdownController(ShutdownController):
    def __init__(self, *, stop_after_waits: int | None = None) -> None:
        super().__init__()
        self.waits: list[float] = []
        self.stop_after_waits = stop_after_waits

    async def wait_async(self, seconds: float) -> bool:
        if seconds == TELEGRAM_HEARTBEAT_INTERVAL_SECONDS:
            return await super().wait_async(seconds)
        self.waits.append(seconds)
        await asyncio.sleep(0)
        if (
            self.stop_after_waits is not None
            and len(self.waits) >= self.stop_after_waits
        ):
            self.request()
            return True
        return False


def test_worker_module_import_has_no_web_network_or_database_side_effect() -> None:
    source = inspect.getsource(worker_module)
    web_source = inspect.getsource(app_main)

    assert "run_worker(" not in web_source
    assert "threading.Thread" not in web_source
    assert "create_database_engine(" not in source.split("def run_worker(", 1)[0]
    assert (
        "create_telegram_http_client("
        not in source.split("async def run_worker(", 1)[0]
    )


def test_worker_command_fails_closed_without_credentials(
    m2_test_database: Engine,
    capsys,
) -> None:
    exit_code = run_worker_command(
        make_settings(m2_test_database, with_credentials=False)
    )

    assert exit_code == WorkerExitCode.CONFIGURATION
    assert capsys.readouterr().err.strip() == "WORKER_CREDENTIALS_MISSING"


@pytest.mark.integration
def test_poll_loop_sorts_deduplicates_and_never_preadvances_cursor(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
    shutdown = ShutdownController()
    client = FakePollingClient(
        [
            (
                TelegramUpdateEnvelope(update_id=7),
                TelegramUpdateEnvelope(update_id=4),
                TelegramUpdateEnvelope(update_id=7),
                TelegramUpdateEnvelope(update_id=5),
            )
        ],
        shutdown,
    )
    processed = []

    async def processor(update: TelegramUpdateEnvelope) -> PollingUpdateOutcome:
        processed.append(update.update_id)
        if len(processed) == 3:
            shutdown.request()
        return PollingUpdateOutcome.TERMINAL

    run(
        run_polling_loop(
            client,  # type: ignore[arg-type]
            session_factory=session_factory,
            processor=processor,
            shutdown=shutdown,
        )
    )

    assert client.offsets == [0]
    assert processed == [4, 5, 7]
    with session_factory() as session:
        assert get_next_offset(session) == 0


@pytest.mark.integration
def test_poll_loop_stops_batch_on_processor_retry(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
    shutdown = ShutdownController()

    class OneBatchClient:
        async def get_updates(self, **_kwargs):
            return (
                TelegramUpdateEnvelope(update_id=1),
                TelegramUpdateEnvelope(update_id=2),
            )

    processed = []

    async def processor(update: TelegramUpdateEnvelope) -> PollingUpdateOutcome:
        processed.append(update.update_id)
        shutdown.request()
        return PollingUpdateOutcome.RETRY

    run(
        run_polling_loop(
            OneBatchClient(),  # type: ignore[arg-type]
            session_factory=session_factory,
            processor=processor,
            shutdown=shutdown,
        )
    )
    assert processed == [1]


@pytest.mark.integration
def test_poll_retry_honors_rate_limit_and_capped_backoff(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
    shutdown = ImmediateShutdownController(stop_after_waits=3)
    client = FakePollingClient(
        [
            TelegramApiError(
                TelegramApiErrorCode.TRANSIENT_RATE_LIMIT,
                retry_after_seconds=19,
            ),
            TelegramApiError(TelegramApiErrorCode.TRANSIENT_SERVER),
            TelegramApiError(TelegramApiErrorCode.TRANSIENT_NETWORK),
        ],
        shutdown,
    )

    async def processor(_update):
        return PollingUpdateOutcome.TERMINAL

    run(
        run_polling_loop(
            client,  # type: ignore[arg-type]
            session_factory=session_factory,
            processor=processor,
            shutdown=shutdown,
        )
    )

    assert shutdown.waits == [19, 2, 4]


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code",
    [
        TelegramApiErrorCode.FATAL_CREDENTIAL,
        TelegramApiErrorCode.FATAL_POLLER_CONFLICT,
        TelegramApiErrorCode.PROTOCOL,
    ],
)
def test_poll_fatal_api_errors_exit_without_retry(
    m2_test_database: Engine,
    error_code: TelegramApiErrorCode,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        load_or_create_polling_state(session)
    shutdown = ImmediateShutdownController()
    client = FakePollingClient([TelegramApiError(error_code)], shutdown)

    async def processor(_update):
        return PollingUpdateOutcome.TERMINAL

    with pytest.raises(worker_module.TelegramWorkerFatalError) as exc_info:
        run(
            run_polling_loop(
                client,  # type: ignore[arg-type]
                session_factory=session_factory,
                processor=processor,
                shutdown=shutdown,
            )
        )

    assert exc_info.value.code == error_code.value
    assert shutdown.waits == []


def preflight_and_empty_poll_handler(
    shutdown: ShutdownController,
    calls: list[str],
):
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "getMe":
            result = {
                "id": 123456789,
                "is_bot": True,
                "username": "Nasiya_LinkBot",
            }
        elif method == "getWebhookInfo":
            result = {"url": ""}
        else:
            shutdown.request()
            result = []
        return httpx.Response(200, json={"ok": True, "result": result})

    return handler


@pytest.mark.integration
def test_worker_startup_preflight_initializes_then_shutdown_marks_not_ready(
    m2_test_database: Engine,
) -> None:
    shutdown = ShutdownController()
    calls: list[str] = []

    async def processor(_update):
        return PollingUpdateOutcome.TERMINAL

    run(
        run_worker(
            make_settings(m2_test_database),
            shutdown=shutdown,
            transport=httpx.MockTransport(
                preflight_and_empty_poll_handler(shutdown, calls)
            ),
            processor=processor,
        )
    )

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        state = session.get(TelegramPollingState, 1)
        assert state is not None
        assert state.next_offset == 0
        assert state.heartbeat_at is not None
        assert state.ready_at is None
    assert calls == ["getMe", "getWebhookInfo", "getUpdates"]


@pytest.mark.integration
def test_worker_lock_precedes_preflight_and_transient_preflight_retries(
    m2_test_database: Engine,
) -> None:
    shutdown = ImmediateShutdownController()
    calls: list[str] = []
    get_me_attempts = 0
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        assert session.get(TelegramPollingState, 1) is None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_me_attempts
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "getMe":
            get_me_attempts += 1
            with pytest.raises(TelegramPollingLockUnavailable):
                acquire_telegram_polling_lock(
                    m2_test_database,
                    deadline_seconds=0,
                )
            if get_me_attempts == 1:
                return httpx.Response(503, json={"ok": False, "error_code": 503})
            result = {
                "id": 123456789,
                "is_bot": True,
                "username": "Nasiya_LinkBot",
            }
        elif method == "getWebhookInfo":
            result = {"url": ""}
        else:
            shutdown.request()
            result = []
        return httpx.Response(200, json={"ok": True, "result": result})

    async def processor(_update):
        return PollingUpdateOutcome.TERMINAL

    run(
        run_worker(
            make_settings(m2_test_database),
            shutdown=shutdown,
            transport=httpx.MockTransport(handler),
            processor=processor,
        )
    )

    assert calls == ["getMe", "getMe", "getWebhookInfo", "getUpdates"]
    assert shutdown.waits == [1]


@pytest.mark.integration
def test_fatal_preflight_releases_lock_and_does_not_initialize_cursor(
    m2_test_database: Engine,
) -> None:
    shutdown = ShutdownController()

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            result = {
                "id": 123456789,
                "is_bot": True,
                "username": "Nasiya_LinkBot",
            }
        else:
            result = {"url": "https://example.test/webhook"}
        return httpx.Response(200, json={"ok": True, "result": result})

    async def processor(_update):
        return PollingUpdateOutcome.TERMINAL

    with pytest.raises(worker_module.TelegramWorkerFatalError):
        run(
            run_worker(
                make_settings(m2_test_database),
                shutdown=shutdown,
                transport=httpx.MockTransport(handler),
                processor=processor,
            )
        )

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        assert session.get(TelegramPollingState, 1) is None
    lock = acquire_telegram_polling_lock(
        m2_test_database,
        deadline_seconds=0,
    )
    lock.close()


@pytest.mark.integration
def test_health_fresh_stale_missing_and_not_ready(
    db_session: Session,
) -> None:
    assert worker_health_is_fresh(db_session, now=NOW) is False

    state = load_or_create_polling_state(db_session)
    db_session.flush()
    assert worker_health_is_fresh(db_session, now=NOW) is False

    update_polling_heartbeat(db_session, now=NOW, ready=True)
    assert worker_health_is_fresh(
        db_session,
        now=NOW + timedelta(seconds=59),
    )
    assert not worker_health_is_fresh(
        db_session,
        now=NOW + timedelta(seconds=61),
    )

    update_polling_heartbeat(
        db_session,
        now=NOW + timedelta(seconds=62),
        ready=False,
    )
    assert state.ready_at is None
    assert not worker_health_is_fresh(
        db_session,
        now=NOW + timedelta(seconds=62),
    )


@pytest.mark.integration
def test_healthcheck_command_exit_codes(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database, with_credentials=False)
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        update_polling_heartbeat(session, now=NOW, ready=True)

    assert (
        run_healthcheck_command(settings, now_factory=lambda: NOW) == WorkerExitCode.OK
    )
    assert (
        run_healthcheck_command(
            settings,
            now_factory=lambda: NOW + timedelta(seconds=61),
        )
        == WorkerExitCode.UNHEALTHY
    )


def test_healthcheck_database_unavailable_is_sanitized(capsys) -> None:
    settings = Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url="postgresql+psycopg://nasiya:secret@127.0.0.1:1/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key=SecretStr("worker-test-hmac-key-at-least-32-characters"),
    )

    assert run_healthcheck_command(settings) == WorkerExitCode.UNHEALTHY
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""
    assert "secret" not in f"{output.out}{output.err}"


def test_worker_source_has_no_cursor_shutdown_flush_or_sensitive_logging() -> None:
    source = inspect.getsource(worker_module).casefold()

    assert "advance_next_offset" not in source
    assert "telegram_bot_token" not in source
    assert "database_url}" not in source
    assert "raw_update" not in source
    assert "logger.exception" not in source
