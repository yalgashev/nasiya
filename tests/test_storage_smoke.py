import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app import cli
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.storage.contracts import (
    BucketName,
    ObjectKey,
    PresignedObjectUrl,
    SanitizedImage,
    StorageProviderOperationResult,
)
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.smoke import FetchedSmokeObject
from tests.storage_fake import (
    FakeObjectStorageService,
    FakeStorageOperation,
)

BASE = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)
FAKE_ACTOR_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
REAL_ACTOR_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
BUCKET = BucketName("nasiya-smoke-private")
RAW_ENDPOINT = "https://storage-smoke-private.invalid"
RAW_ACCESS_KEY = "storage-smoke-access-private"
RAW_SECRET_KEY = "storage-smoke-secret-private"
RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-storage-smoke"
MINIO_ENDPOINT = os.environ.get(
    "M8_MINIO_TEST_ENDPOINT",
    "http://127.0.0.1:9000",
)
MINIO_BUCKET = os.environ.get("M8_MINIO_TEST_BUCKET", "nasiya-private")
MINIO_ACCESS_KEY = os.environ.get(
    "M8_MINIO_TEST_ACCESS_KEY",
    "local-nasiya-storage-app",
)
MINIO_SECRET_KEY = os.environ.get(
    "M8_MINIO_TEST_SECRET_KEY",
    "change-me-local-nasiya-storage-app-secret-at-least-32-chars",
)


class CapturingSmokeStorage(FakeObjectStorageService):
    def __init__(self) -> None:
        super().__init__()
        self.captured_image: SanitizedImage | None = None

    def put_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
        image: SanitizedImage,
    ) -> StorageProviderOperationResult:
        self.captured_image = image
        return super().put_object(bucket=bucket, key=key, image=image)


def _settings(
    database_url: str,
    *,
    environment: str = "testing",
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment=environment,
        debug=False,
        database_url=database_url,
        session_cookie_secure=environment == "production",
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        object_storage_endpoint_url=RAW_ENDPOINT,
        object_storage_region="region-1",
        object_storage_bucket=BUCKET.as_internal_value(),
        object_storage_access_key=RAW_ACCESS_KEY,
        object_storage_secret_key=RAW_SECRET_KEY,
        object_storage_use_ssl=True,
    )


def _minio_settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=database_url,
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        object_storage_endpoint_url=MINIO_ENDPOINT,
        object_storage_region="us-east-1",
        object_storage_bucket=MINIO_BUCKET,
        object_storage_access_key=MINIO_ACCESS_KEY,
        object_storage_secret_key=MINIO_SECRET_KEY,
        object_storage_use_ssl=False,
    )


def _seed_actor(
    engine: Engine,
    *,
    actor_id: UUID,
    phone: str,
) -> None:
    with create_database_session_factory(engine).begin() as session:
        session.add(
            User(
                id=actor_id,
                phone=phone,
                created_at=BASE,
                updated_at=BASE,
            )
        )


def _fetched_from_capture(
    storage: CapturingSmokeStorage,
) -> FetchedSmokeObject:
    assert storage.captured_image is not None
    return FetchedSmokeObject(
        content_type=storage.captured_image.metadata.content_type,
        payload=storage.captured_image.sanitized_bytes.as_internal_bytes(),
    )


@pytest.mark.integration
def test_storage_smoke_cli_fake_runs_full_safe_matrix(
    m2_test_database: Engine,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_actor(
        m2_test_database,
        actor_id=FAKE_ACTOR_ID,
        phone="+998900008641",
    )
    storage = CapturingSmokeStorage()
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: (storage, BUCKET, lambda: None),
    )

    def fetch(
        url: PresignedObjectUrl,
        max_bytes: int,
    ) -> FetchedSmokeObject:
        assert "presigned-test" not in repr(url)
        fetched = _fetched_from_capture(storage)
        assert len(fetched.payload) <= max_bytes
        return fetched

    monkeypatch.setattr(cli, "fetch_presigned_smoke_object", fetch)

    exit_code = cli.main(
        ["storage", "smoke", "--actor-id", str(FAKE_ACTOR_ID)],
        settings=_settings(test_database_url),
    )

    captured = capsys.readouterr()
    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert exit_code == 0
    assert captured.out.strip() == "STORAGE_SMOKE_PASS checks=8"
    assert captured.err == ""
    assert stored is not None
    assert stored.status == ObjectFileStatus.DELETED.value
    assert storage.object_count == 0
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.PRESIGN_GET,
        FakeStorageOperation.DELETE,
        FakeStorageOperation.HEAD,
    ]
    for hidden in (
        str(FAKE_ACTOR_ID),
        stored.object_key,
        RAW_ENDPOINT,
        RAW_ACCESS_KEY,
        RAW_SECRET_KEY,
        "presigned-test",
    ):
        assert hidden not in f"{captured.out} {captured.err}"


@pytest.mark.integration
def test_storage_smoke_fetch_mismatch_fails_safely_and_cleans_up(
    m2_test_database: Engine,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_actor(
        m2_test_database,
        actor_id=FAKE_ACTOR_ID,
        phone="+998900008642",
    )
    storage = CapturingSmokeStorage()
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: (storage, BUCKET, lambda: None),
    )
    monkeypatch.setattr(
        cli,
        "fetch_presigned_smoke_object",
        lambda _url, _max_bytes: FetchedSmokeObject(
            content_type="image/png",
            payload=b"mismatched-private-payload",
        ),
    )

    exit_code = cli.main(
        ["storage", "smoke", "--actor-id", str(FAKE_ACTOR_ID)],
        settings=_settings(test_database_url),
    )

    captured = capsys.readouterr()
    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "STORAGE_SMOKE_FAILED"
    assert stored is not None
    assert stored.status == ObjectFileStatus.DELETED.value
    assert storage.object_count == 0
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.PRESIGN_GET,
        FakeStorageOperation.DELETE,
    ]
    assert "mismatched-private-payload" not in captured.err


def test_storage_smoke_is_production_guarded_before_dependencies(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: pytest.fail("production smoke must not construct storage"),
    )

    exit_code = cli.main(
        ["storage", "smoke", "--actor-id", str(FAKE_ACTOR_ID)],
        settings=_settings(test_database_url, environment="production"),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "local development" in captured.err


@pytest.mark.integration
def test_storage_smoke_cli_real_minio_acceptance(
    m2_test_database: Engine,
    test_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_actor(
        m2_test_database,
        actor_id=REAL_ACTOR_ID,
        phone="+998900008643",
    )

    exit_code = cli.main(
        ["storage", "smoke", "--actor-id", str(REAL_ACTOR_ID)],
        settings=_minio_settings(test_database_url),
    )

    captured = capsys.readouterr()
    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert exit_code == 0
    assert captured.out.strip() == "STORAGE_SMOKE_PASS checks=8"
    assert captured.err == ""
    assert stored is not None
    assert stored.status == ObjectFileStatus.DELETED.value
    for hidden in (
        str(REAL_ACTOR_ID),
        stored.object_key,
        MINIO_ENDPOINT,
        MINIO_BUCKET,
        MINIO_ACCESS_KEY,
        MINIO_SECRET_KEY,
        "X-Amz-Signature",
    ):
        assert hidden not in f"{captured.out} {captured.err}"
