import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine

import app.storage.rate_limit as storage_rate_limit
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit
from app.auth.rate_limit import RateLimitResult, hash_rate_limit_key
from app.db import create_database_session_factory
from app.settings import Settings
from app.storage.rate_limit import (
    STORAGE_UPLOAD_IP_KEY_PREFIX,
    STORAGE_UPLOAD_IP_SCOPE,
    STORAGE_UPLOAD_USER_KEY_PREFIX,
    STORAGE_UPLOAD_USER_SCOPE,
    StorageUploadRateLimitPolicy,
    record_storage_upload_attempt,
)
from app.telegram.client_ip import ResolvedClientIp

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-storage-upload-policy"
NOW = datetime(2026, 7, 30, 21, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _settings(
    engine: Engine,
    *,
    user_attempts: int = 5,
    ip_attempts: int = 20,
    window_seconds: int = 900,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        object_storage_upload_rate_limit_user_attempts=user_attempts,
        object_storage_upload_rate_limit_ip_attempts=ip_attempts,
        object_storage_upload_rate_limit_window_seconds=window_seconds,
    )


def _record_committed(
    engine: Engine,
    settings: Settings,
    *,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> bool:
    session_factory = create_database_session_factory(engine)
    with session_factory.begin() as session:
        return record_storage_upload_attempt(
            session,
            settings,
            actor_user_id,
            client_ip,
            now,
        ).allowed


def _record_many(
    engine: Engine,
    settings: Settings,
    *,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    count: int,
    now: datetime = NOW,
) -> list[bool]:
    return [
        _record_committed(
            engine,
            settings,
            actor_user_id=actor_user_id,
            client_ip=client_ip,
            now=now + timedelta(seconds=offset),
        )
        for offset in range(count)
    ]


def _get_record(
    engine: Engine,
    *,
    settings: Settings,
    scope: str,
    raw_key: str,
) -> AuthRateLimit:
    session_factory = create_database_session_factory(engine)
    with session_factory() as session:
        record = session.scalar(
            select(AuthRateLimit).where(
                AuthRateLimit.scope == scope,
                AuthRateLimit.key_hash == hash_rate_limit_key(settings, raw_key),
            )
        )
        assert record is not None
        return record


@pytest.mark.integration
def test_rate_limit_checks_and_records_user_then_ip_in_stable_order(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_settings = _settings(m2_test_database)
    actor_user_id = uuid4()
    client_ip = ResolvedClientIp("203.0.113.49")
    sentinel_db = object()
    events: list[tuple[str, str, int, int]] = []

    class RecordingRateLimiter:
        def __init__(self, *, db: object, settings: Settings) -> None:
            assert db is sentinel_db
            assert settings is configured_settings

        def check(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            assert raw_key
            assert now == NOW
            events.append(("check", scope, limit, window_seconds))
            return RateLimitResult(allowed=True)

        def record_failure(
            self,
            scope: str,
            raw_key: str,
            now: datetime,
            limit: int,
            window_seconds: int,
        ) -> RateLimitResult:
            assert raw_key
            assert now == NOW
            events.append(("record", scope, limit, window_seconds))
            return RateLimitResult(allowed=True)

    monkeypatch.setattr(
        storage_rate_limit,
        "AuthRateLimiter",
        RecordingRateLimiter,
    )

    result = record_storage_upload_attempt(
        sentinel_db,  # type: ignore[arg-type]
        configured_settings,
        actor_user_id,
        client_ip,
        NOW,
    )

    assert result.allowed is True
    assert events == [
        ("check", STORAGE_UPLOAD_USER_SCOPE, 6, 900),
        ("check", STORAGE_UPLOAD_IP_SCOPE, 21, 900),
        ("record", STORAGE_UPLOAD_USER_SCOPE, 6, 900),
        ("record", STORAGE_UPLOAD_IP_SCOPE, 21, 900),
    ]


@pytest.mark.integration
def test_default_policy_allows_five_user_attempts_and_rejects_sixth(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)

    outcomes = _record_many(
        m2_test_database,
        settings,
        actor_user_id=uuid4(),
        client_ip=ResolvedClientIp("203.0.113.50"),
        count=6,
    )

    assert settings.object_storage_upload_rate_limit_user_attempts == 5
    assert outcomes == [True, True, True, True, True, False]


@pytest.mark.integration
def test_default_policy_allows_twenty_ip_attempts_and_rejects_twenty_first(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    shared_ip = ResolvedClientIp("203.0.113.51")

    outcomes = [
        _record_committed(
            m2_test_database,
            settings,
            actor_user_id=uuid4(),
            client_ip=shared_ip,
            now=NOW + timedelta(seconds=offset),
        )
        for offset in range(21)
    ]

    assert settings.object_storage_upload_rate_limit_ip_attempts == 20
    assert outcomes == ([True] * 20) + [False]


@pytest.mark.integration
def test_window_resets_at_exact_nine_hundred_seconds(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    actor_user_id = uuid4()
    client_ip = ResolvedClientIp("203.0.113.52")
    first_five = _record_many(
        m2_test_database,
        settings,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        count=5,
    )
    before_reset = _record_committed(
        m2_test_database,
        settings,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=NOW + timedelta(seconds=899),
    )
    at_reset = _record_committed(
        m2_test_database,
        settings,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=NOW + timedelta(seconds=900),
    )
    user_record = _get_record(
        m2_test_database,
        settings=settings,
        scope=STORAGE_UPLOAD_USER_SCOPE,
        raw_key=f"{STORAGE_UPLOAD_USER_KEY_PREFIX}{actor_user_id}",
    )

    assert settings.object_storage_upload_rate_limit_window_seconds == 900
    assert first_five == [True] * 5
    assert before_reset is False
    assert at_reset is True
    assert user_record.window_started_at == NOW + timedelta(seconds=900)
    assert user_record.attempt_count == 1


@pytest.mark.integration
def test_user_and_ip_buckets_are_independent_and_non_colliding(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database, user_attempts=1, ip_attempts=2)
    shared_ip = ResolvedClientIp("203.0.113.53")
    first_actor = uuid4()
    second_actor = uuid4()

    first = _record_committed(
        m2_test_database,
        settings,
        actor_user_id=first_actor,
        client_ip=shared_ip,
        now=NOW,
    )
    second = _record_committed(
        m2_test_database,
        settings,
        actor_user_id=second_actor,
        client_ip=shared_ip,
        now=NOW + timedelta(seconds=1),
    )
    blocked_by_ip = _record_committed(
        m2_test_database,
        settings,
        actor_user_id=uuid4(),
        client_ip=shared_ip,
        now=NOW + timedelta(seconds=2),
    )
    blocked_by_user = _record_committed(
        m2_test_database,
        settings,
        actor_user_id=first_actor,
        client_ip=ResolvedClientIp("203.0.113.54"),
        now=NOW + timedelta(seconds=3),
    )

    assert (first, second, blocked_by_ip, blocked_by_user) == (
        True,
        True,
        False,
        False,
    )


@pytest.mark.integration
def test_only_hmac_keys_and_storage_scopes_are_persisted(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    actor_user_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    raw_ip = "203.0.113.55"
    client_ip = ResolvedClientIp(raw_ip)

    assert _record_committed(
        m2_test_database,
        settings,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=NOW,
    )

    with m2_test_database.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT scope, key_hash, window_started_at::text, "
                "attempt_count::text, updated_at::text "
                "FROM auth_rate_limits ORDER BY scope"
            )
        ).all()
    rendered = "|".join(str(value) for row in rows for value in row)
    scopes = {row[0] for row in rows}
    key_hashes = {row[1] for row in rows}

    assert scopes == {STORAGE_UPLOAD_IP_SCOPE, STORAGE_UPLOAD_USER_SCOPE}
    assert key_hashes == {
        hash_rate_limit_key(
            settings,
            f"{STORAGE_UPLOAD_USER_KEY_PREFIX}{actor_user_id}",
        ),
        hash_rate_limit_key(
            settings,
            f"{STORAGE_UPLOAD_IP_KEY_PREFIX}{raw_ip}",
        ),
    }
    assert str(actor_user_id) not in rendered
    assert raw_ip not in rendered
    assert "login_" not in rendered
    assert "telegram_" not in rendered


@pytest.mark.integration
def test_blocked_result_is_identical_for_user_and_ip_buckets(
    m2_test_database: Engine,
) -> None:
    user_settings = _settings(m2_test_database, user_attempts=1, ip_attempts=20)
    actor_user_id = uuid4()
    first_ip = ResolvedClientIp("203.0.113.56")
    assert _record_committed(
        m2_test_database,
        user_settings,
        actor_user_id=actor_user_id,
        client_ip=first_ip,
        now=NOW,
    )
    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as session:
        user_blocked = StorageUploadRateLimitPolicy(
            db=session,
            settings=user_settings,
        ).record_attempt(
            actor_user_id,
            ResolvedClientIp("203.0.113.57"),
            NOW + timedelta(seconds=1),
        )

    ip_settings = _settings(m2_test_database, user_attempts=5, ip_attempts=1)
    shared_ip = ResolvedClientIp("203.0.113.58")
    assert _record_committed(
        m2_test_database,
        ip_settings,
        actor_user_id=uuid4(),
        client_ip=shared_ip,
        now=NOW + timedelta(seconds=2),
    )
    with session_factory.begin() as session:
        ip_blocked = StorageUploadRateLimitPolicy(
            db=session,
            settings=ip_settings,
        ).record_attempt(
            uuid4(),
            shared_ip,
            NOW + timedelta(seconds=3),
        )

    assert user_blocked.allowed is False
    assert ip_blocked.allowed is False
    assert user_blocked.error_code is ip_blocked.error_code is ErrorCode.RATE_LIMITED
    assert user_blocked.public_error == ip_blocked.public_error
    assert "user" not in repr(user_blocked).casefold()
    assert "ip" not in repr(ip_blocked).casefold()


@pytest.mark.integration
def test_concurrent_user_boundary_allows_only_fifth_attempt(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    actor_user_id = uuid4()
    client_ip = ResolvedClientIp("203.0.113.59")
    assert (
        _record_many(
            m2_test_database,
            settings,
            actor_user_id=actor_user_id,
            client_ip=client_ip,
            count=4,
        )
        == [True] * 4
    )
    barrier = Barrier(2)

    def worker() -> bool:
        session_factory = create_database_session_factory(m2_test_database)
        with session_factory.begin() as session:
            barrier.wait(timeout=5)
            return record_storage_upload_attempt(
                session,
                settings,
                actor_user_id,
                client_ip,
                NOW + timedelta(seconds=10),
            ).allowed

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(worker), executor.submit(worker))
        outcomes = [future.result(timeout=10) for future in futures]

    user_record = _get_record(
        m2_test_database,
        settings=settings,
        scope=STORAGE_UPLOAD_USER_SCOPE,
        raw_key=f"{STORAGE_UPLOAD_USER_KEY_PREFIX}{actor_user_id}",
    )
    assert sorted(outcomes) == [False, True]
    assert user_record.attempt_count == 6


@pytest.mark.integration
def test_concurrent_ip_boundary_allows_only_twentieth_attempt(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database)
    shared_ip = ResolvedClientIp("203.0.113.60")
    for offset in range(19):
        assert _record_committed(
            m2_test_database,
            settings,
            actor_user_id=uuid4(),
            client_ip=shared_ip,
            now=NOW + timedelta(seconds=offset),
        )
    barrier = Barrier(2)

    def worker(actor_user_id: UUID) -> bool:
        session_factory = create_database_session_factory(m2_test_database)
        with session_factory.begin() as session:
            barrier.wait(timeout=5)
            return record_storage_upload_attempt(
                session,
                settings,
                actor_user_id,
                shared_ip,
                NOW + timedelta(seconds=30),
            ).allowed

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result(timeout=10)
            for future in (
                executor.submit(worker, uuid4()),
                executor.submit(worker, uuid4()),
            )
        ]

    ip_record = _get_record(
        m2_test_database,
        settings=settings,
        scope=STORAGE_UPLOAD_IP_SCOPE,
        raw_key=(f"{STORAGE_UPLOAD_IP_KEY_PREFIX}{shared_ip.as_hmac_input()}"),
    )
    assert sorted(outcomes) == [False, True]
    assert ip_record.attempt_count == 21


@pytest.mark.integration
def test_rejection_creates_no_object_row_and_policy_has_no_provider_surface(
    m2_test_database: Engine,
) -> None:
    settings = _settings(m2_test_database, user_attempts=1)
    actor_user_id = uuid4()
    client_ip = ResolvedClientIp("203.0.113.61")
    assert _record_committed(
        m2_test_database,
        settings,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=NOW,
    )
    assert not _record_committed(
        m2_test_database,
        settings,
        actor_user_id=actor_user_id,
        client_ip=client_ip,
        now=NOW + timedelta(seconds=1),
    )

    with m2_test_database.connect() as connection:
        object_count = connection.scalar(text("SELECT count(*) FROM object_files"))
        rate_count = connection.scalar(select(func.count()).select_from(AuthRateLimit))
    source = inspect.getsource(storage_rate_limit)

    assert object_count == 0
    assert rate_count == 2
    assert "ObjectStorageService" not in source
    assert "create_pending_object_file" not in source
    assert "put_object" not in source
    assert "head_object" not in source
    assert "logger" not in source
    assert "logging" not in source
    assert "print(" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "byte" not in source.casefold()
    assert "quota" not in source.casefold()
    assert "object_storage_upload_rate_limit" in source
    assert (PROJECT_ROOT / "app" / "storage" / "rate_limit.py").is_file()
