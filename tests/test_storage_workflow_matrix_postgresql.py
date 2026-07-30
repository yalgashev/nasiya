import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from threading import Event
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.storage.contracts import (
    ObjectFileAccessAuthorizer,
    ObjectReadAuthorizationRequest,
    ObjectReadAuthorizationResult,
    StorageProviderOperationResult,
)
from app.storage.errors import (
    StorageAccessDeniedError,
    StorageInternalCode,
    StorageUploadError,
)
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.service import (
    create_authorized_presigned_get_url,
    delete_available_object,
    ingest_sanitized_image,
    prepare_sanitized_image_upload,
    reconcile_stale_object_uploads,
)
from app.telegram.client_ip import ResolvedClientIp
from tests.storage_fake import (
    FakeObjectStorageService,
    FakeStorageOperation,
    FakeStorageOutcome,
)

BASE = datetime(2026, 7, 30, 6, 0, tzinfo=UTC)
ACTOR_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CLIENT_IP = ResolvedClientIp("203.0.113.90")
RAW_ENDPOINT = "https://workflow-matrix-private.invalid"
RAW_ACCESS_KEY = "workflow-matrix-access-private"
RAW_SECRET_KEY = "workflow-matrix-secret-private"
RAW_BUCKET = "workflow-matrix-private"
RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-storage-workflow-matrix"
PRIVATE_PHONE = "+998900008631"


class AsyncBytesSource:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._position = 0

    async def seek(self, offset: int) -> None:
        self._position = offset

    async def read(self, size: int) -> bytes:
        chunk = self._payload[self._position : self._position + size]
        self._position += len(chunk)
        return chunk


class AllowSameParentAuthorizer:
    def authorize(
        self,
        request: ObjectReadAuthorizationRequest,
    ) -> ObjectReadAuthorizationResult:
        if (
            request.actor_user_id == ACTOR_ID
            and request.domain_parent_reference == request.object_file_id
        ):
            return ObjectReadAuthorizationResult.ALLOWED
        return ObjectReadAuthorizationResult.DENIED


assert isinstance(AllowSameParentAuthorizer(), ObjectFileAccessAuthorizer)


def _png_bytes() -> bytes:
    output = BytesIO()
    with Image.new("RGBA", (4, 3), (40, 50, 60, 180)) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_LIMIT_KEY,
        object_storage_endpoint_url=RAW_ENDPOINT,
        object_storage_region="region-1",
        object_storage_bucket=RAW_BUCKET,
        object_storage_access_key=RAW_ACCESS_KEY,
        object_storage_secret_key=RAW_SECRET_KEY,
        object_storage_use_ssl=True,
    )


def _seed_actor(engine: Engine) -> None:
    with create_database_session_factory(engine).begin() as session:
        session.add(
            User(
                id=ACTOR_ID,
                phone=PRIVATE_PHONE,
                created_at=BASE,
                updated_at=BASE,
            )
        )


@pytest.mark.integration
def test_tx_s2_failure_then_reconcile_uses_same_row_and_never_repeats_put(
    m2_test_database: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_actor(m2_test_database)

    class FailingResultSession(Session):
        pass

    commit_count = 0
    sensitive_detail = f"{RAW_ENDPOINT} {RAW_SECRET_KEY} tx-s2-private-detail"

    def fail_third_commit(_session: Session) -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 3:
            raise OperationalError(
                sensitive_detail,
                {"credential": RAW_ACCESS_KEY},
                RuntimeError(sensitive_detail),
            )

    event.listen(FailingResultSession, "before_commit", fail_third_commit)
    failing_factory = sessionmaker(
        bind=m2_test_database,
        class_=FailingResultSession,
    )
    storage = FakeObjectStorageService()
    try:
        with pytest.raises(StorageUploadError) as exc_info:
            asyncio.run(
                ingest_sanitized_image(
                    failing_factory,
                    source=AsyncBytesSource(_png_bytes()),
                    actor_user_id=ACTOR_ID,
                    client_ip=CLIENT_IP,
                    now=BASE,
                    settings=_settings(m2_test_database),
                    storage=storage,
                )
            )
    finally:
        event.remove(FailingResultSession, "before_commit", fail_third_commit)

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory() as session:
        pending = session.scalar(select(ObjectFile))
        row_count = session.scalar(select(func.count()).select_from(ObjectFile))
    assert pending is not None
    assert pending.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert row_count == 1

    reconcile_result = reconcile_stale_object_uploads(
        session_factory,
        storage=storage,
        now=BASE + timedelta(seconds=61),
        stale_seconds=60,
        batch_size=1,
    )

    with session_factory() as session:
        available = session.get(ObjectFile, pending.id)
        final_row_count = session.scalar(select(func.count()).select_from(ObjectFile))
    error_text = f"{exc_info.value!s} {exc_info.value!r} {caplog.text}"
    assert reconcile_result.available_count == 1
    assert available is not None
    assert available.status == ObjectFileStatus.AVAILABLE.value
    assert final_row_count == 1
    assert storage.object_count == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.HEAD,
    ]
    for hidden in (
        sensitive_detail,
        pending.object_key,
        PRIVATE_PHONE,
        RAW_ENDPOINT,
        RAW_ACCESS_KEY,
        RAW_SECRET_KEY,
    ):
        assert hidden not in f"{error_text} {reconcile_result!r}"


@pytest.mark.integration
def test_crash_after_put_is_reconciled_once_across_repeated_invocations(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    prepared = asyncio.run(
        prepare_sanitized_image_upload(
            session_factory,
            source=AsyncBytesSource(_png_bytes()),
            actor_user_id=ACTOR_ID,
            client_ip=CLIENT_IP,
            now=BASE,
            settings=_settings(m2_test_database),
        )
    )
    storage = FakeObjectStorageService()
    assert (
        storage.put_object(
            bucket=prepared.bucket,
            key=prepared.object_key,
            image=prepared.image,
        )
        is StorageProviderOperationResult.SUCCESS
    )

    first = reconcile_stale_object_uploads(
        session_factory,
        storage=storage,
        now=BASE + timedelta(seconds=61),
        stale_seconds=60,
        batch_size=1,
    )
    second = reconcile_stale_object_uploads(
        session_factory,
        storage=storage,
        now=BASE + timedelta(seconds=61),
        stale_seconds=60,
        batch_size=1,
    )

    with session_factory() as session:
        stored = session.get(ObjectFile, prepared.object_file_id)
        row_count = session.scalar(select(func.count()).select_from(ObjectFile))
    assert first.available_count == 1
    assert second.claimed_count == 0
    assert stored is not None
    assert stored.status == ObjectFileStatus.AVAILABLE.value
    assert row_count == 1
    assert storage.object_count == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("head_mode", "expected_status", "expected_code", "expected_calls"),
    (
        (
            "missing",
            ObjectFileStatus.FAILED,
            StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
            (FakeStorageOperation.PUT, FakeStorageOperation.HEAD),
        ),
        (
            "mismatch",
            ObjectFileStatus.DELETED,
            StorageInternalCode.OBJECT_METADATA_MISMATCH,
            (
                FakeStorageOperation.PUT,
                FakeStorageOperation.HEAD,
                FakeStorageOperation.DELETE,
            ),
        ),
    ),
)
def test_successful_put_head_failure_matrix_has_no_orphan(
    m2_test_database: Engine,
    head_mode: str,
    expected_status: ObjectFileStatus,
    expected_code: StorageInternalCode,
    expected_calls: tuple[FakeStorageOperation, ...],
) -> None:
    _seed_actor(m2_test_database)

    class VanishingHeadStorage(FakeObjectStorageService):
        def put_object(self, **kwargs) -> StorageProviderOperationResult:
            result = super().put_object(**kwargs)
            self._objects.clear()
            return result

    storage: FakeObjectStorageService
    if head_mode == "missing":
        storage = VanishingHeadStorage()
    else:
        storage = FakeObjectStorageService()
        storage.queue_head_outcome(FakeStorageOutcome.MISMATCH)

    with pytest.raises(StorageUploadError) as exc_info:
        asyncio.run(
            ingest_sanitized_image(
                create_database_session_factory(m2_test_database),
                source=AsyncBytesSource(_png_bytes()),
                actor_user_id=ACTOR_ID,
                client_ip=CLIENT_IP,
                now=BASE,
                settings=_settings(m2_test_database),
                storage=storage,
            )
        )

    with create_database_session_factory(m2_test_database)() as session:
        stored = session.scalar(select(ObjectFile))
    assert exc_info.value.internal_code is expected_code
    assert stored is not None
    assert stored.status == expected_status.value
    assert stored.failure_code == expected_code.value
    assert storage.object_count == 0
    assert tuple(call.operation for call in storage.calls) == expected_calls


@pytest.mark.integration
def test_delete_pending_wins_before_presign_and_provider_delete_is_bounded(
    m2_test_database: Engine,
) -> None:
    _seed_actor(m2_test_database)

    class BlockingDeleteStorage(FakeObjectStorageService):
        def __init__(self) -> None:
            super().__init__()
            self.delete_entered = Event()
            self.delete_release = Event()

        def delete_object(self, **kwargs):
            self.delete_entered.set()
            assert self.delete_release.wait(timeout=5)
            return super().delete_object(**kwargs)

    storage = BlockingDeleteStorage()
    settings = _settings(m2_test_database)
    session_factory = create_database_session_factory(m2_test_database)
    ingested = asyncio.run(
        ingest_sanitized_image(
            session_factory,
            source=AsyncBytesSource(_png_bytes()),
            actor_user_id=ACTOR_ID,
            client_ip=CLIENT_IP,
            now=BASE,
            settings=settings,
            storage=storage,
        )
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        delete_future = executor.submit(
            delete_available_object,
            session_factory,
            object_file_id=ingested.object_file_id,
            storage=storage,
            now=BASE + timedelta(seconds=1),
        )
        assert storage.delete_entered.wait(timeout=5)
        try:
            with pytest.raises(StorageAccessDeniedError):
                create_authorized_presigned_get_url(
                    session_factory,
                    request=ObjectReadAuthorizationRequest(
                        actor_user_id=ACTOR_ID,
                        object_file_id=ingested.object_file_id,
                        domain_parent_reference=ingested.object_file_id,
                    ),
                    authorizer=AllowSameParentAuthorizer(),
                    storage=storage,
                    settings=settings,
                )
        finally:
            storage.delete_release.set()
        delete_result = delete_future.result(timeout=5)

    with session_factory() as session:
        stored = session.get(ObjectFile, ingested.object_file_id)
    assert delete_result.status is ObjectFileStatus.DELETED
    assert stored is not None
    assert stored.status == ObjectFileStatus.DELETED.value
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.DELETE,
    ]
