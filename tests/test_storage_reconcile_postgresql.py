import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.storage.service as storage_service
from app.auth.models import User
from app.db import create_database_session_factory
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
from app.storage.repository import create_pending_object_file
from app.storage.service import (
    StorageReconcileResult,
    reconcile_stale_object_uploads,
)
from tests.storage_fake import (
    FakeObjectStorageService,
    FakeStorageOperation,
    FakeStorageOutcome,
)

BASE = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
RECONCILE_NOW = BASE + timedelta(minutes=10)
STALE_SECONDS = 60
BUCKET = BucketName("nasiya-reconcile-private")


def _image() -> SanitizedImage:
    payload = b"canonical-sanitized-image"
    checksum = ObjectChecksumSha256(sha256(payload).hexdigest())
    return SanitizedImage(
        metadata=SanitizedImageMetadata(
            content_type="image/png",
            canonical_extension="png",
            size_bytes=len(payload),
            width_px=4,
            height_px=3,
            checksum_sha256=checksum,
        ),
        sanitized_bytes=SanitizedImageBytes(payload),
    )


def _seed_pending(
    engine: Engine,
    *,
    image: SanitizedImage,
) -> tuple[UUID, ObjectKey]:
    session_factory = create_database_session_factory(engine)
    object_file_id = uuid4()
    object_key = ObjectKey(f"v1/objects/{uuid4().hex}.png")
    with session_factory.begin() as session:
        user = User(
            phone="+998900008601",
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
    return object_file_id, object_key


def _stored_object(engine: Engine, object_file_id: UUID) -> ObjectFile:
    with create_database_session_factory(engine)() as session:
        stored = session.get(ObjectFile, object_file_id)
        assert stored is not None
        session.expunge(stored)
        return stored


@pytest.mark.integration
def test_crash_after_put_reconcile_closes_claim_session_before_exact_head(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_pending(
        m2_test_database,
        image=image,
    )
    active_sessions = 0
    session_events: list[str] = []

    class TrackingSession(Session):
        def __init__(self, **kwargs: object) -> None:
            nonlocal active_sessions
            super().__init__(**kwargs)
            self._tracking_closed = False
            active_sessions += 1
            session_events.append("open")

        def close(self) -> None:
            nonlocal active_sessions
            if not self._tracking_closed:
                active_sessions -= 1
                self._tracking_closed = True
                session_events.append("close")
            super().close()

    class SessionCheckingStorage(FakeObjectStorageService):
        def head_object(self, *, bucket: BucketName, key: ObjectKey):
            assert active_sessions == 0
            session_events.append("head")
            return super().head_object(bucket=bucket, key=key)

    storage = SessionCheckingStorage()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    tracking_factory = sessionmaker(
        bind=m2_test_database,
        class_=TrackingSession,
    )

    result = reconcile_stale_object_uploads(
        tracking_factory,
        storage=storage,
        now=RECONCILE_NOW,
        stale_seconds=STALE_SECONDS,
        batch_size=1,
    )

    stored = _stored_object(m2_test_database, object_file_id)
    assert result == StorageReconcileResult(
        claimed_count=1,
        available_count=1,
        failed_count=0,
        deleted_count=0,
        pending_count=0,
        delete_pending_count=0,
        safe_codes=(),
    )
    assert stored.status == ObjectFileStatus.AVAILABLE.value
    assert stored.available_at == RECONCILE_NOW
    assert active_sessions == 0
    assert session_events == ["open", "close", "head", "open", "close"]
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]


@pytest.mark.integration
def test_reconcile_missing_object_marks_failed_without_put(
    m2_test_database: Engine,
) -> None:
    object_file_id, _object_key = _seed_pending(
        m2_test_database,
        image=_image(),
    )
    storage = FakeObjectStorageService()

    result = reconcile_stale_object_uploads(
        create_database_session_factory(m2_test_database),
        storage=storage,
        now=RECONCILE_NOW,
        stale_seconds=STALE_SECONDS,
        batch_size=1,
    )

    stored = _stored_object(m2_test_database, object_file_id)
    assert result.claimed_count == 1
    assert result.failed_count == 1
    assert result.safe_codes == (StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,)
    assert stored.status == ObjectFileStatus.FAILED.value
    assert stored.failure_code == (
        StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD.value
    )
    assert stored.terminal_at == RECONCILE_NOW
    assert [call.operation for call in storage.calls] == [FakeStorageOperation.HEAD]


@pytest.mark.integration
def test_reconcile_mismatch_deletes_and_marks_deleted_without_put_retry(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_pending(
        m2_test_database,
        image=image,
    )
    storage = FakeObjectStorageService()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    storage.queue_head_outcome(FakeStorageOutcome.MISMATCH)

    result = reconcile_stale_object_uploads(
        create_database_session_factory(m2_test_database),
        storage=storage,
        now=RECONCILE_NOW,
        stale_seconds=STALE_SECONDS,
        batch_size=1,
    )

    stored = _stored_object(m2_test_database, object_file_id)
    assert result.deleted_count == 1
    assert result.safe_codes == (StorageInternalCode.OBJECT_METADATA_MISMATCH,)
    assert stored.status == ObjectFileStatus.DELETED.value
    assert stored.failure_code == StorageInternalCode.OBJECT_METADATA_MISMATCH.value
    assert storage.object_count == 0
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.DELETE,
    ]


@pytest.mark.integration
def test_reconcile_head_failure_stays_pending_with_safe_unknown_code(
    m2_test_database: Engine,
) -> None:
    object_file_id, _object_key = _seed_pending(
        m2_test_database,
        image=_image(),
    )
    storage = FakeObjectStorageService()
    storage.queue_head_outcome(FakeStorageOutcome.TIMEOUT)

    result = reconcile_stale_object_uploads(
        create_database_session_factory(m2_test_database),
        storage=storage,
        now=RECONCILE_NOW,
        stale_seconds=STALE_SECONDS,
        batch_size=1,
    )

    stored = _stored_object(m2_test_database, object_file_id)
    assert result.pending_count == 1
    assert result.safe_codes == (StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN,)
    assert stored.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert stored.failure_code == StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN.value
    assert [call.operation for call in storage.calls] == [FakeStorageOperation.HEAD]


@pytest.mark.integration
def test_two_reconcilers_claim_once_and_make_one_final_transition(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_pending(
        m2_test_database,
        image=image,
    )

    class BlockingHeadStorage(FakeObjectStorageService):
        def __init__(self) -> None:
            super().__init__()
            self.head_entered = Event()
            self.head_release = Event()

        def head_object(self, *, bucket: BucketName, key: ObjectKey):
            self.head_entered.set()
            assert self.head_release.wait(timeout=5)
            return super().head_object(bucket=bucket, key=key)

    storage = BlockingHeadStorage()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    session_factory = create_database_session_factory(m2_test_database)

    def reconcile() -> StorageReconcileResult:
        return reconcile_stale_object_uploads(
            session_factory,
            storage=storage,
            now=RECONCILE_NOW,
            stale_seconds=STALE_SECONDS,
            batch_size=1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(reconcile)
        assert storage.head_entered.wait(timeout=5)
        second_result = reconcile()
        storage.head_release.set()
        first_result = first_future.result(timeout=5)

    stored = _stored_object(m2_test_database, object_file_id)
    assert sorted((first_result.claimed_count, second_result.claimed_count)) == [0, 1]
    assert stored.status == ObjectFileStatus.AVAILABLE.value
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
    ]


@pytest.mark.parametrize("batch_size", [False, 0, 5001])
def test_reconcile_rejects_unbounded_batch_without_provider_call(
    batch_size: int,
) -> None:
    storage = FakeObjectStorageService()

    with pytest.raises(ValueError, match="between 1 and 5000"):
        reconcile_stale_object_uploads(
            sessionmaker(),
            storage=storage,
            now=RECONCILE_NOW,
            stale_seconds=STALE_SECONDS,
            batch_size=batch_size,
        )

    assert storage.calls == ()


def test_reconcile_surface_and_result_expose_safe_counts_and_codes_only() -> None:
    function_source = inspect.getsource(reconcile_stale_object_uploads)
    module_source = inspect.getsource(storage_service)
    result_fields = {field.name for field in fields(StorageReconcileResult)}
    result = StorageReconcileResult(
        claimed_count=1,
        available_count=0,
        failed_count=1,
        deleted_count=0,
        pending_count=0,
        delete_pending_count=0,
        safe_codes=(StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,),
    )

    assert result_fields == {
        "claimed_count",
        "available_count",
        "failed_count",
        "deleted_count",
        "pending_count",
        "delete_pending_count",
        "safe_codes",
    }
    assert "put_object" not in function_source
    assert "APIRouter" not in module_source
    assert "scheduler" not in function_source.casefold()
    assert "http" not in repr(result).casefold()
    assert "v1/objects/" not in repr(result)
