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
from app.storage.errors import StorageInternalCode, StorageUploadError
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.repository import (
    create_pending_object_file,
    mark_object_file_available,
    mark_object_file_delete_pending,
)
from app.storage.service import (
    StorageDeleteBatchResult,
    StorageDeleteResult,
    delete_available_object,
    reconcile_stale_object_deletes,
)
from tests.storage_fake import (
    FakeObjectStorageService,
    FakeStorageOperation,
    FakeStorageOutcome,
)

BASE = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
DELETE_NOW = BASE + timedelta(minutes=10)
STALE_SECONDS = 60
BUCKET = BucketName("nasiya-delete-private")


def _image() -> SanitizedImage:
    payload = b"delete-sanitized-image"
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
    with session_factory.begin() as session:
        user = User(
            phone="+998900008611",
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


def _stored_object(engine: Engine, object_file_id: UUID) -> ObjectFile:
    with create_database_session_factory(engine)() as session:
        stored = session.get(ObjectFile, object_file_id)
        assert stored is not None
        session.expunge(stored)
        return stored


@pytest.mark.integration
def test_internal_delete_commits_pending_before_one_external_delete(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.AVAILABLE,
    )
    active_sessions = 0

    class TrackingSession(Session):
        def __init__(self, **kwargs: object) -> None:
            nonlocal active_sessions
            super().__init__(**kwargs)
            self._tracking_closed = False
            active_sessions += 1

        def close(self) -> None:
            nonlocal active_sessions
            if not self._tracking_closed:
                active_sessions -= 1
                self._tracking_closed = True
            super().close()

    class SessionCheckingStorage(FakeObjectStorageService):
        def delete_object(self, *, bucket: BucketName, key: ObjectKey):
            assert active_sessions == 0
            return super().delete_object(bucket=bucket, key=key)

    storage = SessionCheckingStorage()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    tracking_factory = sessionmaker(
        bind=m2_test_database,
        class_=TrackingSession,
    )

    result = delete_available_object(
        tracking_factory,
        object_file_id=object_file_id,
        storage=storage,
        now=DELETE_NOW,
    )

    stored = _stored_object(m2_test_database, object_file_id)
    assert result == StorageDeleteResult(
        status=ObjectFileStatus.DELETED,
        safe_code=None,
    )
    assert stored.status == ObjectFileStatus.DELETED.value
    assert stored.available_at == BASE
    assert stored.terminal_at == DELETE_NOW
    assert stored.deleted_at == DELETE_NOW
    assert active_sessions == 0
    assert storage.object_count == 0
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.DELETE,
    ]


@pytest.mark.integration
def test_internal_delete_of_absent_object_counts_as_deleted(
    m2_test_database: Engine,
) -> None:
    object_file_id, _object_key = _seed_object(
        m2_test_database,
        image=_image(),
        status=ObjectFileStatus.AVAILABLE,
    )
    storage = FakeObjectStorageService()

    result = delete_available_object(
        create_database_session_factory(m2_test_database),
        object_file_id=object_file_id,
        storage=storage,
        now=DELETE_NOW,
    )

    assert result.status is ObjectFileStatus.DELETED
    assert (
        _stored_object(
            m2_test_database,
            object_file_id,
        ).status
        == ObjectFileStatus.DELETED.value
    )
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.DELETE,
    ]


@pytest.mark.integration
def test_accepted_delete_timeout_heads_missing_and_marks_deleted(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.AVAILABLE,
    )
    storage = FakeObjectStorageService()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    storage.queue_delete_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)

    result = delete_available_object(
        create_database_session_factory(m2_test_database),
        object_file_id=object_file_id,
        storage=storage,
        now=DELETE_NOW,
    )

    assert result.status is ObjectFileStatus.DELETED
    assert (
        _stored_object(
            m2_test_database,
            object_file_id,
        ).status
        == ObjectFileStatus.DELETED.value
    )
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.DELETE,
        FakeStorageOperation.HEAD,
    ]


@pytest.mark.integration
def test_delete_timeout_with_present_object_stays_pending_unknown(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.AVAILABLE,
    )
    storage = FakeObjectStorageService()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    storage.queue_delete_outcome(FakeStorageOutcome.TIMEOUT)

    result = delete_available_object(
        create_database_session_factory(m2_test_database),
        object_file_id=object_file_id,
        storage=storage,
        now=DELETE_NOW,
    )

    stored = _stored_object(m2_test_database, object_file_id)
    assert result == StorageDeleteResult(
        status=ObjectFileStatus.DELETE_PENDING,
        safe_code=StorageInternalCode.DELETE_OUTCOME_UNKNOWN,
    )
    assert stored.status == ObjectFileStatus.DELETE_PENDING.value
    assert stored.failure_code == StorageInternalCode.DELETE_OUTCOME_UNKNOWN.value
    assert stored.terminal_at is None
    assert storage.object_count == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.DELETE,
        FakeStorageOperation.HEAD,
    ]


@pytest.mark.integration
def test_pending_upload_is_not_an_internal_delete_capability(
    m2_test_database: Engine,
) -> None:
    object_file_id, _object_key = _seed_object(
        m2_test_database,
        image=_image(),
        status=ObjectFileStatus.PENDING_UPLOAD,
    )
    storage = FakeObjectStorageService()

    with pytest.raises(StorageUploadError) as exc_info:
        delete_available_object(
            create_database_session_factory(m2_test_database),
            object_file_id=object_file_id,
            storage=storage,
            now=DELETE_NOW,
        )

    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert (
        _stored_object(
            m2_test_database,
            object_file_id,
        ).status
        == ObjectFileStatus.PENDING_UPLOAD.value
    )
    assert storage.calls == ()


@pytest.mark.integration
def test_concurrent_internal_delete_makes_one_provider_call(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.AVAILABLE,
    )

    class BlockingDeleteStorage(FakeObjectStorageService):
        def __init__(self) -> None:
            super().__init__()
            self.delete_entered = Event()
            self.delete_release = Event()

        def delete_object(self, *, bucket: BucketName, key: ObjectKey):
            self.delete_entered.set()
            assert self.delete_release.wait(timeout=5)
            return super().delete_object(bucket=bucket, key=key)

    storage = BlockingDeleteStorage()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    session_factory = create_database_session_factory(m2_test_database)

    def delete() -> StorageDeleteResult:
        return delete_available_object(
            session_factory,
            object_file_id=object_file_id,
            storage=storage,
            now=DELETE_NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(delete)
        assert storage.delete_entered.wait(timeout=5)
        second_result = delete()
        storage.delete_release.set()
        first_result = first_future.result(timeout=5)

    assert first_result.status is ObjectFileStatus.DELETED
    assert second_result.status is ObjectFileStatus.DELETE_PENDING
    assert (
        _stored_object(
            m2_test_database,
            object_file_id,
        ).status
        == ObjectFileStatus.DELETED.value
    )
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.DELETE,
    ]


@pytest.mark.integration
def test_stale_delete_reconcile_heads_present_deletes_and_finishes(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.DELETE_PENDING,
    )
    storage = FakeObjectStorageService()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)

    result = reconcile_stale_object_deletes(
        create_database_session_factory(m2_test_database),
        storage=storage,
        now=DELETE_NOW,
        stale_seconds=STALE_SECONDS,
        batch_size=1,
    )

    assert result == StorageDeleteBatchResult(
        claimed_count=1,
        deleted_count=1,
        pending_count=0,
        safe_codes=(),
    )
    assert (
        _stored_object(
            m2_test_database,
            object_file_id,
        ).status
        == ObjectFileStatus.DELETED.value
    )
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.DELETE,
    ]


@pytest.mark.integration
def test_stale_delete_reconcile_heads_missing_and_marks_deleted(
    m2_test_database: Engine,
) -> None:
    object_file_id, _object_key = _seed_object(
        m2_test_database,
        image=_image(),
        status=ObjectFileStatus.DELETE_PENDING,
    )
    storage = FakeObjectStorageService()

    result = reconcile_stale_object_deletes(
        create_database_session_factory(m2_test_database),
        storage=storage,
        now=DELETE_NOW,
        stale_seconds=STALE_SECONDS,
        batch_size=1,
    )

    assert result.deleted_count == 1
    assert (
        _stored_object(
            m2_test_database,
            object_file_id,
        ).status
        == ObjectFileStatus.DELETED.value
    )
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.HEAD,
    ]


@pytest.mark.integration
def test_stale_delete_timeout_remains_pending_for_later_reconcile(
    m2_test_database: Engine,
) -> None:
    image = _image()
    object_file_id, object_key = _seed_object(
        m2_test_database,
        image=image,
        status=ObjectFileStatus.DELETE_PENDING,
    )
    storage = FakeObjectStorageService()
    storage.put_object(bucket=BUCKET, key=object_key, image=image)
    storage.queue_delete_outcome(FakeStorageOutcome.TIMEOUT)

    result = reconcile_stale_object_deletes(
        create_database_session_factory(m2_test_database),
        storage=storage,
        now=DELETE_NOW,
        stale_seconds=STALE_SECONDS,
        batch_size=1,
    )

    stored = _stored_object(m2_test_database, object_file_id)
    assert result == StorageDeleteBatchResult(
        claimed_count=1,
        deleted_count=0,
        pending_count=1,
        safe_codes=(StorageInternalCode.DELETE_OUTCOME_UNKNOWN,),
    )
    assert stored.status == ObjectFileStatus.DELETE_PENDING.value
    assert stored.failure_code == StorageInternalCode.DELETE_OUTCOME_UNKNOWN.value
    assert storage.object_count == 1
    assert [call.operation for call in storage.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.DELETE,
        FakeStorageOperation.HEAD,
    ]


def test_delete_surfaces_expose_no_key_or_public_route() -> None:
    module_source = inspect.getsource(storage_service)
    delete_source = inspect.getsource(delete_available_object)
    reconcile_source = inspect.getsource(reconcile_stale_object_deletes)
    result_fields = {field.name for field in fields(StorageDeleteResult)}
    batch_fields = {field.name for field in fields(StorageDeleteBatchResult)}

    assert result_fields == {"status", "safe_code"}
    assert batch_fields == {
        "claimed_count",
        "deleted_count",
        "pending_count",
        "safe_codes",
    }
    assert "APIRouter" not in module_source
    assert "put_object" not in delete_source
    assert "put_object" not in reconcile_source
    assert "object_key" not in repr(StorageDeleteResult(ObjectFileStatus.DELETED, None))
