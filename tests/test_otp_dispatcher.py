import asyncio
import inspect
import subprocess
import sys
from collections.abc import Awaitable, Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.main as app_main
import app.otp.dispatcher as dispatcher_module
from app.auth.models import User
from app.db import create_database_session_factory
from app.otp.code import OtpCode
from app.otp.contracts import OtpChallengeStatus, OtpDispatchStatus
from app.otp.dispatch_service import PreparedOtpDispatch
from app.otp.dispatcher import (
    OtpDispatcherExitCode,
    ShutdownController,
    dispatcher_health_is_fresh,
    run_dispatch_loop,
    run_dispatcher,
    run_dispatcher_command,
    run_healthcheck_command,
)
from app.otp.models import OtpChallenge, OtpDispatch, OtpDispatcherState
from app.otp.provider import (
    OtpDeliverySendResult,
    OtpDeliverySendStatus,
    TelegramOtpTarget,
)
from app.otp.repository import (
    create_pending_challenge,
    create_pending_dispatch,
    mark_dispatcher_heartbeat,
)
from app.settings import Settings
from app.telegram.bot import TelegramBotUsername
from app.telegram.inbound import VerifiedPrivateTelegramChatIdentity
from app.telegram.models import TelegramLink

NOW = datetime(2026, 7, 29, 14, 0, tzinfo=UTC)
RAW_TOKEN = "123456789:OtpDispatcherSecretToken"
OTP_HMAC_KEY = "test-otp-dispatch-command-hmac-key-at-least-32-chars"
RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-dispatcher"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def run(coroutine: Awaitable[object]):
    return asyncio.run(coroutine)


def make_settings(
    engine: Engine,
    *,
    with_credentials: bool = True,
    with_otp_key: bool = True,
    **overrides,
) -> Settings:
    values = {
        "app_environment": "testing",
        "debug": False,
        "database_url": engine.url.render_as_string(hide_password=False),
        "session_cookie_secure": False,
        "rate_limit_hmac_key": RATE_LIMIT_HMAC_KEY,
        "telegram_bot_username": (
            TelegramBotUsername("Nasiya_LinkBot") if with_credentials else None
        ),
        "telegram_bot_token": SecretStr(RAW_TOKEN) if with_credentials else None,
        "otp_hmac_key": SecretStr(OTP_HMAC_KEY) if with_otp_key else None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class ImmediateShutdownController(ShutdownController):
    def __init__(self, *, stop_after_waits: int = 1) -> None:
        super().__init__()
        self.stop_after_waits = stop_after_waits
        self.waits: list[float] = []

    async def wait_async(self, seconds: float) -> bool:
        self.waits.append(seconds)
        await asyncio.sleep(0)
        if len(self.waits) >= self.stop_after_waits:
            self.request()
            return True
        return False


class RecordingProvider:
    def __init__(
        self,
        *,
        shutdown: ShutdownController | None = None,
        result: OtpDeliverySendResult | None = None,
        session_factory=None,
        dispatch_id=None,
    ) -> None:
        self.shutdown = shutdown
        self.result = result or OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT)
        self.session_factory = session_factory
        self.dispatch_id = dispatch_id
        self.calls = []
        self.seen_dispatch_status: str | None = None

    async def send_otp(self, **kwargs) -> OtpDeliverySendResult:
        self.calls.append(kwargs)
        if self.session_factory is not None and self.dispatch_id is not None:
            with self.session_factory() as session:
                dispatch = session.get(OtpDispatch, self.dispatch_id)
                self.seen_dispatch_status = (
                    None if dispatch is None else dispatch.status
                )
        if self.shutdown is not None:
            self.shutdown.request()
        return self.result


class RaisingProvider:
    def __init__(self, shutdown: ShutdownController) -> None:
        self.shutdown = shutdown
        self.calls = 0

    async def send_otp(self, **_kwargs) -> OtpDeliverySendResult:
        self.calls += 1
        self.shutdown.request()
        raise RuntimeError("sensitive provider failure")


class ThreadSafeRecordingProvider:
    def __init__(self) -> None:
        self.calls = []
        self._lock = Lock()

    async def send_otp(self, **kwargs) -> OtpDeliverySendResult:
        with self._lock:
            self.calls.append(kwargs)
        await asyncio.sleep(0)
        return OtpDeliverySendResult(status=OtpDeliverySendStatus.SENT)


def add_user_and_link(
    session: Session,
    *,
    phone: str = "+998900009101",
    chat_id: int = 9_981_000_101,
) -> tuple[User, TelegramLink]:
    user = User(phone=phone)
    session.add(user)
    session.flush()
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=chat_id,
        linked_at=NOW,
        phone_verified_at=NOW,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    return user, link


def seed_pending_dispatch(
    engine: Engine,
    *,
    phone: str = "+998900009101",
    chat_id: int = 9_981_000_101,
) -> tuple[object, object]:
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        user, link = add_user_and_link(session, phone=phone, chat_id=chat_id)
        challenge = create_pending_challenge(
            session,
            user_id=user.id,
            telegram_link_id=link.id,
            telegram_linked_at=link.linked_at,
            browser_binding_digest="d" * 64,
            now=NOW,
        )
        dispatch = create_pending_dispatch(
            session,
            challenge_id=challenge.id,
            locale="uz-Latn",
            now=NOW,
        )
        return challenge.id, dispatch.id


def preflight_handler(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "getMe":
            result = {
                "id": 123456789,
                "is_bot": True,
                "username": "Nasiya_LinkBot",
            }
        else:
            result = {"url": ""}
        return httpx.Response(200, json={"ok": True, "result": result})

    return handler


def test_dispatcher_module_import_has_no_web_network_or_database_side_effect() -> None:
    source = inspect.getsource(dispatcher_module)
    web_source = inspect.getsource(app_main)

    assert "run_dispatcher(" not in web_source
    assert (
        "create_database_engine(" not in source.split("async def run_dispatcher(", 1)[0]
    )
    assert (
        "create_telegram_http_client("
        not in source.split(
            "async def run_dispatcher(",
            1,
        )[0]
    )


def test_dispatcher_fresh_process_resolves_all_registered_foreign_key_targets() -> None:
    script = """
import app.otp.dispatcher  # noqa: F401
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
    raise SystemExit(f"unresolved dispatcher metadata targets: {','.join(missing)}")
for table in Base.metadata.tables.values():
    for foreign_key in table.foreign_keys:
        foreign_key.column
tuple(Base.metadata.sorted_tables)
engine.dispose()
print("OTP_DISPATCHER_MODEL_METADATA_COMPLETE")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "OTP_DISPATCHER_MODEL_METADATA_COMPLETE\n"


def test_dispatcher_command_fails_closed_without_required_secrets(
    m2_test_database: Engine,
    capsys,
) -> None:
    assert (
        run_dispatcher_command(make_settings(m2_test_database, with_credentials=False))
        == OtpDispatcherExitCode.CONFIGURATION
    )
    assert capsys.readouterr().err.strip() == "OTP_DISPATCHER_CREDENTIALS_MISSING"

    assert (
        run_dispatcher_command(make_settings(m2_test_database, with_otp_key=False))
        == OtpDispatcherExitCode.CONFIGURATION
    )
    assert capsys.readouterr().err.strip() == "OTP_DISPATCHER_SECRET_MISSING"


@pytest.mark.integration
def test_dispatcher_startup_preflight_heartbeat_and_shutdown_cleanup(
    m2_test_database: Engine,
) -> None:
    shutdown = ImmediateShutdownController()
    calls: list[str] = []

    run(
        run_dispatcher(
            make_settings(m2_test_database),
            shutdown=shutdown,
            transport=httpx.MockTransport(preflight_handler(calls)),
            provider=RecordingProvider(),
            now_factory=lambda: NOW,
        )
    )

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        state = session.get(OtpDispatcherState, 1)
        assert state is not None
        assert state.heartbeat_at == NOW
        assert state.ready_at is None
    assert calls == ["getMe", "getWebhookInfo"]


@pytest.mark.integration
def test_dispatcher_health_fresh_stale_missing_and_not_ready(
    db_session: Session,
) -> None:
    assert dispatcher_health_is_fresh(db_session, now=NOW) is False

    mark_dispatcher_heartbeat(db_session, now=NOW, ready=True)
    assert dispatcher_health_is_fresh(
        db_session,
        now=NOW + timedelta(seconds=59),
    )
    assert not dispatcher_health_is_fresh(
        db_session,
        now=NOW + timedelta(seconds=61),
    )

    mark_dispatcher_heartbeat(
        db_session,
        now=NOW + timedelta(seconds=62),
        ready=False,
    )
    assert not dispatcher_health_is_fresh(
        db_session,
        now=NOW + timedelta(seconds=62),
    )


@pytest.mark.integration
def test_dispatcher_healthcheck_command_exit_codes(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(
        m2_test_database, with_credentials=False, with_otp_key=False
    )
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        mark_dispatcher_heartbeat(session, now=NOW, ready=True)

    assert (
        run_healthcheck_command(settings, now_factory=lambda: NOW)
        == OtpDispatcherExitCode.OK
    )
    assert (
        run_healthcheck_command(
            settings,
            now_factory=lambda: NOW + timedelta(seconds=61),
        )
        == OtpDispatcherExitCode.UNHEALTHY
    )


@pytest.mark.integration
def test_dispatch_loop_sends_only_after_tx_d1_commit_and_records_result(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    challenge_id, dispatch_id = seed_pending_dispatch(m2_test_database)
    shutdown = ShutdownController()
    provider = RecordingProvider(
        shutdown=shutdown,
        session_factory=session_factory,
        dispatch_id=dispatch_id,
    )

    run(
        run_dispatch_loop(
            provider,
            session_factory=session_factory,
            otp_hmac_key=settings.require_otp_hmac_key(),
            settings=settings,
            shutdown=shutdown,
            now_factory=lambda: NOW + timedelta(seconds=1),
        )
    )

    assert len(provider.calls) == 1
    assert provider.seen_dispatch_status == OtpDispatchStatus.PREPARED.value
    assert provider.calls[0]["target"].chat_identity.as_bigint() == 9_981_000_101
    assert provider.calls[0]["ttl_seconds"] == 180
    with session_factory() as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        assert challenge is not None
        assert dispatch is not None
        assert challenge.status == OtpChallengeStatus.ACTIVE.value
        assert dispatch.status == OtpDispatchStatus.SENT.value
        assert dispatch.sent_at == NOW + timedelta(seconds=1)


@pytest.mark.integration
def test_parallel_dispatch_loops_claim_one_pending_dispatch_for_one_send(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(
        m2_test_database,
        otp_dispatch_batch_size=1,
        otp_dispatch_poll_seconds=1,
    )
    session_factory = create_database_session_factory(m2_test_database)
    challenge_id, dispatch_id = seed_pending_dispatch(
        m2_test_database,
        phone="+998900009104",
        chat_id=9_981_000_104,
    )
    provider = ThreadSafeRecordingProvider()

    def run_candidate(_index: int) -> None:
        run(
            run_dispatch_loop(
                provider,
                session_factory=session_factory,
                otp_hmac_key=settings.require_otp_hmac_key(),
                settings=settings,
                shutdown=ImmediateShutdownController(stop_after_waits=1),
                now_factory=lambda: NOW + timedelta(seconds=1),
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(run_candidate, range(2)))

    assert len(provider.calls) == 1
    assert provider.calls[0]["target"].chat_identity.as_bigint() == 9_981_000_104
    with session_factory() as session:
        challenge = session.get(OtpChallenge, challenge_id)
        dispatch = session.get(OtpDispatch, dispatch_id)
        assert challenge is not None
        assert dispatch is not None
        assert challenge.status == OtpChallengeStatus.ACTIVE.value
        assert dispatch.status == OtpDispatchStatus.SENT.value


@pytest.mark.integration
def test_dispatch_loop_sanitizes_provider_exception_to_unknown(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    _challenge_id, dispatch_id = seed_pending_dispatch(
        m2_test_database,
        phone="+998900009102",
        chat_id=9_981_000_102,
    )
    shutdown = ShutdownController()
    provider = RaisingProvider(shutdown)

    run(
        run_dispatch_loop(
            provider,
            session_factory=session_factory,
            otp_hmac_key=settings.require_otp_hmac_key(),
            settings=settings,
            shutdown=shutdown,
            now_factory=lambda: NOW + timedelta(seconds=1),
        )
    )

    with session_factory() as session:
        dispatch = session.get(OtpDispatch, dispatch_id)
        assert dispatch is not None
        assert dispatch.status == OtpDispatchStatus.UNKNOWN.value
        assert dispatch.failure_code == "OTP_UNKNOWN"
    assert provider.calls == 1


def test_commit_failure_before_send_propagates_without_provider_call(
    monkeypatch,
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    prepared = PreparedOtpDispatch(
        dispatch_id=uuid4(),
        challenge_id=uuid4(),
        target=TelegramOtpTarget(
            chat_identity=VerifiedPrivateTelegramChatIdentity(9_981_000_103)
        ),
        code=OtpCode("123456"),
        locale="uz-Latn",
        ttl_seconds=180,
    )
    provider = RecordingProvider()

    class CommitFailingContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc_value, traceback) -> None:
            if exc_type is None:
                raise RuntimeError("commit failed")

    class CommitFailingSessionFactory:
        def begin(self):
            return CommitFailingContext()

    def fake_prepare_next_otp_dispatch(_session, **_kwargs):
        return prepared

    monkeypatch.setattr(
        dispatcher_module,
        "prepare_next_otp_dispatch",
        fake_prepare_next_otp_dispatch,
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        run(
            run_dispatch_loop(
                provider,
                session_factory=CommitFailingSessionFactory(),  # type: ignore[arg-type]
                otp_hmac_key=settings.require_otp_hmac_key(),
                settings=settings,
                shutdown=ShutdownController(),
                now_factory=lambda: NOW,
            )
        )

    assert provider.calls == []
