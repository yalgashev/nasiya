from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine

from app import cli
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    SanitizedImage,
    SanitizedImageBytes,
    SanitizedImageMetadata,
)
from app.storage.errors import StorageInternalCode
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.repository import (
    create_pending_object_file,
    mark_object_file_available,
    mark_object_file_delete_pending,
)
from tests.storage_fake import (
    FakeObjectStorageService,
    FakeStorageOperation,
    FakeStorageOutcome,
)

BASE = datetime(2020, 1, 1, tzinfo=UTC)
BUCKET = BucketName("nasiya-cli-private")
RAW_ENDPOINT = "https://storage-cli-private.invalid"
RAW_ACCESS_KEY = "storage-cli-access-private"
RAW_SECRET_KEY = "storage-cli-secret-private"
RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-storage-cli"


def _settings(
    database_url: str,
    *,
    environment: str = "development",
    complete_storage: bool = True,
) -> Settings:
    storage_values: dict[str, object] = {}
    if complete_storage:
        storage_values = {
            "object_storage_endpoint_url": RAW_ENDPOINT,
            "object_storage_region": "region-1",
            "object_storage_bucket": BUCKET.as_internal_value(),
            "object_storage_access_key": RAW_ACCESS_KEY,
            "object_storage_secret_key": RAW_SECRET_KEY,
            "object_storage_use_ssl": True,
            "object_storage_reconcile_stale_seconds": 60,
        }
    return Settings(
        _env_file=None,
        app_environment=environment,
        debug=False,
        database_url=database_url,
        session_cookie_secure=environment == "production",
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        **storage_values,
    )


def _image() -> SanitizedImage:
    payload = b"storage-cli-sanitized-image"
    return SanitizedImage(
        metadata=SanitizedImageMetadata(
            content_type="image/png",
            canonical_extension="png",
            size_bytes=len(payload),
            width_px=4,
            height_px=3,
            checksum_sha256=ObjectChecksumSha256(sha256(payload).hexdigest()),
        ),
        sanitized_bytes=SanitizedImageBytes(payload),
    )


def _seed_object(
    engine: Engine,
    *,
    image: SanitizedImage,
    status: ObjectFileStatus,
) -> tuple[UUID, ObjectKey]:
    session_factory = create_database_session_factory(engine)
    object_file_id = uuid4()
    object_key = ObjectKey(f"v1/objects/{uuid4().hex}.png")
    phone_by_status = {
        ObjectFileStatus.PENDING_UPLOAD: "+998900008621",
        ObjectFileStatus.AVAILABLE: "+998900008622",
        ObjectFileStatus.DELETE_PENDING: "+998900008623",
    }
    with session_factory.begin() as session:
        user = User(
            phone=phone_by_status[status],
            created_at=BASE,
            updated_at=BASE,
        )
        session.add(user)
        session.flush()
        create_pending_object_file(
            session,
            object_file_id=object_file_id,
            bucket=BUCKET,
            object_key=object_key,
            metadata=image.metadata,
            created_by_user_id=user.id,
            now=BASE,
        )
        if status in {
            ObjectFileStatus.AVAILABLE,
            ObjectFileStatus.DELETE_PENDING,
        }:
            mark_object_file_available(
                session,
                object_file_id=object_file_id,
                now=BASE,
            )
        if status is ObjectFileStatus.DELETE_PENDING:
            mark_object_file_delete_pending(
                session,
                object_file_id=object_file_id,
                failure_code=None,
                now=BASE,
            )
    return object_file_id, object_key


@pytest.mark.parametrize("command", ("preflight", "health"))
def test_storage_preflight_and_health_print_safe_status_only(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    storage = FakeObjectStorageService()
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: (storage, BUCKET),
    )

    exit_code = cli.main(
        ["storage", command],
        settings=_settings(test_database_url),
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == "STORAGE_PREFLIGHT_OK"
    assert captured.err == ""
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.ENSURE_PRIVATE_BUCKET,
    ]


def test_storage_preflight_provider_failure_is_sanitized(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage = FakeObjectStorageService()
    storage.queue_ensure_private_bucket_outcome(FakeStorageOutcome.DEFINITE_FAILURE)
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: (storage, BUCKET),
    )

    exit_code = cli.main(
        ["storage", "preflight"],
        settings=_settings(test_database_url),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "STORAGE_PROVIDER_UNAVAILABLE"


@pytest.mark.integration
def test_storage_reconcile_cli_outputs_counts_and_safe_codes_only(
    m2_test_database: Engine,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image = _image()
    pending_id, pending_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.PENDING_UPLOAD,
    )
    delete_id, delete_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.DELETE_PENDING,
    )
    storage = FakeObjectStorageService()
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: (storage, BUCKET),
    )

    exit_code = cli.main(
        ["storage", "reconcile", "--batch-size", "2"],
        settings=_settings(test_database_url),
    )

    captured = capsys.readouterr()
    with create_database_session_factory(m2_test_database)() as session:
        pending = session.get(ObjectFile, pending_id)
        delete = session.get(ObjectFile, delete_id)
    assert exit_code == 0
    assert "STORAGE_RECONCILE_OK" in captured.out
    assert "upload_claimed=1" in captured.out
    assert "failed=1" in captured.out
    assert "delete_claimed=1" in captured.out
    assert "delete_completed=1" in captured.out
    assert StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD.value in captured.out
    assert pending is not None
    assert pending.status == ObjectFileStatus.FAILED.value
    assert delete is not None
    assert delete.status == ObjectFileStatus.DELETED.value
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.HEAD,
        FakeStorageOperation.HEAD,
    ]
    for hidden in (
        str(pending_id),
        str(delete_id),
        pending_key.as_internal_value(),
        delete_key.as_internal_value(),
        BUCKET.as_internal_value(),
        RAW_ENDPOINT,
        RAW_ACCESS_KEY,
        RAW_SECRET_KEY,
    ):
        assert hidden not in f"{captured.out} {captured.err}"


@pytest.mark.integration
def test_storage_delete_cli_uses_internal_service_and_hides_identity(
    m2_test_database: Engine,
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    image = _image()
    object_file_id, object_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.AVAILABLE,
    )
    storage = FakeObjectStorageService()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: (storage, BUCKET),
    )

    exit_code = cli.main(
        ["storage", "delete", "--object-id", str(object_file_id)],
        settings=_settings(test_database_url),
    )

    captured = capsys.readouterr()
    with create_database_session_factory(m2_test_database)() as session:
        stored = session.get(ObjectFile, object_file_id)
    assert exit_code == 0
    assert captured.out.strip() == "STORAGE_DELETE status=DELETED code=NONE"
    assert captured.err == ""
    assert stored is not None
    assert stored.status == ObjectFileStatus.DELETED.value
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.DELETE,
    ]
    for hidden in (
        str(object_file_id),
        object_key.as_internal_value(),
        BUCKET.as_internal_value(),
        RAW_ENDPOINT,
        RAW_ACCESS_KEY,
        RAW_SECRET_KEY,
    ):
        assert hidden not in f"{captured.out} {captured.err}"


def test_storage_delete_cli_is_blocked_in_production_before_dependencies(
    test_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "_configure_storage_service",
        lambda _settings: pytest.fail("production delete must fail before storage"),
    )

    exit_code = cli.main(
        ["storage", "delete", "--object-id", str(uuid4())],
        settings=_settings(test_database_url, environment="production"),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "local development" in captured.err


def test_storage_configuration_failure_and_parser_are_closed(
    test_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        ["storage", "preflight"],
        settings=_settings(test_database_url, complete_storage=False),
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.strip() == "STORAGE_CONFIGURATION_UNAVAILABLE"
    for argv in (
        ["storage", "reconcile", "--batch-size", "0"],
        ["storage", "reconcile", "--batch-size", "5001"],
        ["storage", "delete", "--object-id", str(UUID(int=0))],
        ["storage", "upload"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli.main(argv, settings=_settings(test_database_url))
        assert exc_info.value.code == 2
