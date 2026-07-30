import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.auth.error_codes import ErrorCode
from app.main import create_app
from app.otp import dispatcher as otp_dispatcher
from app.settings import Settings
from app.storage.errors import StorageUploadError
from app.storage.s3 import S3_TOTAL_MAX_ATTEMPTS
from app.storage.service import ingest_sanitized_image
from app.telegram import worker as telegram_worker
from app.telegram.client_ip import ResolvedClientIp
from tests.storage_fake import FakeObjectStorageService

RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-storage-degraded-mode"
UNREACHABLE_ENDPOINT = "http://127.0.0.1:1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UnreadableSource:
    async def seek(self, _offset: int) -> None:
        pytest.fail("missing storage config must fail before source access")

    async def read(self, _size: int) -> bytes:
        pytest.fail("missing storage config must fail before source access")


def _settings(
    database_url: str,
    *,
    with_storage: bool,
) -> Settings:
    storage_values: dict[str, object] = {}
    if with_storage:
        storage_values = {
            "object_storage_endpoint_url": UNREACHABLE_ENDPOINT,
            "object_storage_region": "region-1",
            "object_storage_bucket": "nasiya-degraded-private",
            "object_storage_access_key": "degraded-storage-access-private",
            "object_storage_secret_key": "degraded-storage-secret-private",
            "object_storage_use_ssl": False,
        }
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=database_url,
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        **storage_values,
    )


def test_web_auth_otp_and_telegram_routes_ignore_stopped_storage(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.storage.s3.create_s3_client",
        lambda _config: pytest.fail("web must not construct a storage client"),
    )
    application = create_app(
        settings=_settings(test_database_url, with_storage=True),
    )

    with TestClient(application) as client:
        health = client.get("/health")
        password_login = client.get("/auth/login")
        otp_login = client.get("/auth/otp")
        telegram_linking = client.get(
            "/auth/telegram",
            follow_redirects=False,
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert password_login.status_code == 200
    assert otp_login.status_code == 200
    assert telegram_linking.status_code in {303, 307}
    assert telegram_linking.headers["location"] == "/auth/login"
    application.state.database_engine.dispose()


def test_web_missing_storage_config_starts_and_health_is_green(
    test_database_url: str,
) -> None:
    application = create_app(
        settings=_settings(test_database_url, with_storage=False),
    )

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    application.state.database_engine.dispose()


def test_missing_storage_config_fails_operation_before_db_source_or_provider(
    test_database_url: str,
) -> None:
    storage = FakeObjectStorageService()

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                sessionmaker(),
                source=UnreadableSource(),
                actor_user_id=uuid4(),
                client_ip=ResolvedClientIp("192.0.2.20"),
                now=datetime.now(UTC),
                settings=_settings(test_database_url, with_storage=False),
                storage=storage,
            )
        )

    assert exc_info.value.code is ErrorCode.FILE_STORAGE_ERROR
    assert storage.calls == ()


def test_web_worker_dispatcher_and_compose_have_no_storage_runtime_dependency() -> None:
    main_source = (PROJECT_ROOT / "app/main.py").read_text()
    worker_source = inspect.getsource(telegram_worker)
    dispatcher_source = inspect.getsource(otp_dispatcher)
    compose = (PROJECT_ROOT / "compose.yaml").read_text()
    web_section = compose.split("  web:", 1)[1].split("  telegram-worker:", 1)[0]
    worker_section = compose.split("  telegram-worker:", 1)[1].split(
        "  otp-dispatcher:",
        1,
    )[0]
    dispatcher_section = compose.split("  otp-dispatcher:", 1)[1].split(
        "volumes:",
        1,
    )[0]

    assert "app.storage" not in main_source
    assert "object_storage" not in main_source
    assert "app.storage" not in worker_source
    assert "object_storage" not in worker_source
    assert "app.storage" not in dispatcher_source
    assert "object_storage" not in dispatcher_source
    assert "condition: service_healthy" not in web_section
    assert "minio" not in worker_section.casefold()
    assert "object_storage" not in worker_section.casefold()
    assert "minio" not in dispatcher_section.casefold()
    assert "object_storage" not in dispatcher_section.casefold()


def test_storage_provider_has_one_attempt_and_preflight_has_no_retry_loop() -> None:
    from app import cli

    preflight_source = inspect.getsource(cli.storage_preflight_command)

    assert S3_TOTAL_MAX_ATTEMPTS == 1
    assert preflight_source.count("check_bucket_access") == 1
    for administrative_operation in (
        "create_bucket",
        "get_bucket_acl",
        "get_bucket_policy",
        "put_public_access_block",
        "get_public_access_block",
    ):
        assert administrative_operation not in preflight_source
    assert "while " not in preflight_source
    assert "sleep(" not in preflight_source
