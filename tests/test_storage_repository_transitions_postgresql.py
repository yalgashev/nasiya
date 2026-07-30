import inspect
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.storage.repository as storage_repository
from app.auth.models import User
from app.db import create_database_session_factory
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    SanitizedImageMetadata,
)
from app.storage.errors import StorageInternalCode
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.repository import (
    ObjectFileStateError,
    create_pending_object_file,
    mark_object_file_available,
    mark_object_file_delete_pending,
    mark_object_file_deleted,
    mark_object_file_failed,
    mark_pending_upload_outcome_unknown,
)

NOW = datetime(2026, 7, 30, 19, 0, tzinfo=UTC)
LATER = NOW + timedelta(seconds=1)
LATEST = NOW + timedelta(seconds=2)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _add_user(session: Session) -> User:
    user = User(phone="+998900008331", created_at=NOW, updated_at=NOW)
    session.add(user)
    session.flush()
    return user


def _create_pending(session: Session, *, user_id: UUID) -> ObjectFile:
    object_file_id = uuid4()
    return create_pending_object_file(
        session,
        object_file_id=object_file_id,
        bucket=BucketName("nasiya-private-test"),
        object_key=ObjectKey(f"v1/objects/{object_file_id.hex}.webp"),
        metadata=SanitizedImageMetadata(
            content_type="image/webp",
            canonical_extension="webp",
            size_bytes=96,
            width_px=8,
            height_px=6,
            checksum_sha256=ObjectChecksumSha256("b" * 64),
        ),
        created_by_user_id=user_id,
        now=NOW,
    )


def test_transition_api_uses_row_lock_and_caller_owned_transaction() -> None:
    source = inspect.getsource(storage_repository)
    lock_source = inspect.getsource(storage_repository.load_object_file_for_update)

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "with_for_update" in lock_source
    for function in (
        mark_object_file_available,
        mark_object_file_failed,
        mark_object_file_delete_pending,
        mark_object_file_deleted,
        mark_pending_upload_outcome_unknown,
    ):
        parameters = list(inspect.signature(function).parameters.values())
        assert parameters[0].name == "session"
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in parameters[1:]
        )
        assert "_lock_transition_row" in inspect.getsource(function)


@pytest.mark.integration
def test_available_delete_pending_deleted_lifecycle_is_valid_and_idempotent(
    db_session: Session,
) -> None:
    object_file = _create_pending(db_session, user_id=_add_user(db_session).id)

    available = mark_object_file_available(
        db_session,
        object_file_id=object_file.id,
        now=LATER,
    )
    assert available is object_file
    assert available.status == ObjectFileStatus.AVAILABLE.value
    assert available.available_at == LATER
    assert available.updated_at == LATER
    assert available.failure_code is None
    assert (
        mark_object_file_available(
            db_session,
            object_file_id=object_file.id,
            now=LATEST,
        ).available_at
        == LATER
    )

    delete_pending = mark_object_file_delete_pending(
        db_session,
        object_file_id=object_file.id,
        failure_code=None,
        now=LATEST,
    )
    assert delete_pending.status == ObjectFileStatus.DELETE_PENDING.value
    assert delete_pending.available_at == LATER
    assert delete_pending.terminal_at is None
    assert delete_pending.deleted_at is None
    assert (
        mark_object_file_delete_pending(
            db_session,
            object_file_id=object_file.id,
            failure_code=None,
            now=LATEST,
        )
        is object_file
    )

    deleted_at = LATEST + timedelta(seconds=1)
    deleted = mark_object_file_deleted(
        db_session,
        object_file_id=object_file.id,
        now=deleted_at,
    )
    assert deleted.status == ObjectFileStatus.DELETED.value
    assert deleted.terminal_at == deleted_at
    assert deleted.deleted_at == deleted_at
    assert (
        mark_object_file_deleted(
            db_session,
            object_file_id=object_file.id,
            now=deleted_at + timedelta(seconds=1),
        ).deleted_at
        == deleted_at
    )
    db_session.flush()


@pytest.mark.integration
def test_pending_unknown_can_recover_to_available(db_session: Session) -> None:
    object_file = _create_pending(db_session, user_id=_add_user(db_session).id)

    unknown = mark_pending_upload_outcome_unknown(
        db_session,
        object_file_id=object_file.id,
        now=LATER,
    )
    assert unknown.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert unknown.failure_code == StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN.value
    assert unknown.updated_at == LATER
    assert (
        mark_pending_upload_outcome_unknown(
            db_session,
            object_file_id=object_file.id,
            now=LATEST,
        ).updated_at
        == LATER
    )

    recovered = mark_object_file_available(
        db_session,
        object_file_id=object_file.id,
        now=LATEST,
    )
    assert recovered.status == ObjectFileStatus.AVAILABLE.value
    assert recovered.failure_code is None
    assert recovered.available_at == LATEST
    db_session.flush()


@pytest.mark.integration
def test_pending_can_fail_or_enter_approved_mismatch_cleanup(
    db_session: Session,
) -> None:
    user = _add_user(db_session)
    failed = _create_pending(db_session, user_id=user.id)
    mismatch = _create_pending(db_session, user_id=user.id)

    failed_result = mark_object_file_failed(
        db_session,
        object_file_id=failed.id,
        failure_code=StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
        now=LATER,
    )
    assert failed_result.status == ObjectFileStatus.FAILED.value
    assert failed_result.terminal_at == LATER
    assert (
        failed_result.failure_code
        == StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD.value
    )
    assert (
        mark_object_file_failed(
            db_session,
            object_file_id=failed.id,
            failure_code=StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
            now=LATEST,
        ).terminal_at
        == LATER
    )

    cleanup = mark_object_file_delete_pending(
        db_session,
        object_file_id=mismatch.id,
        failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
        now=LATER,
    )
    assert cleanup.status == ObjectFileStatus.DELETE_PENDING.value
    assert cleanup.available_at is None
    assert cleanup.failure_code == StorageInternalCode.OBJECT_METADATA_MISMATCH.value
    deleted = mark_object_file_deleted(
        db_session,
        object_file_id=mismatch.id,
        now=LATEST,
    )
    assert deleted.status == ObjectFileStatus.DELETED.value
    assert deleted.failure_code == StorageInternalCode.OBJECT_METADATA_MISMATCH.value
    db_session.flush()


@pytest.mark.integration
def test_invalid_transitions_and_timestamp_regression_are_rejected(
    db_session: Session,
) -> None:
    user = _add_user(db_session)
    pending = _create_pending(db_session, user_id=user.id)
    available = _create_pending(db_session, user_id=user.id)
    failed = _create_pending(db_session, user_id=user.id)
    deleted = _create_pending(db_session, user_id=user.id)

    mark_object_file_available(
        db_session,
        object_file_id=available.id,
        now=LATER,
    )
    mark_object_file_failed(
        db_session,
        object_file_id=failed.id,
        failure_code=StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
        now=LATER,
    )
    mark_object_file_delete_pending(
        db_session,
        object_file_id=deleted.id,
        failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
        now=LATER,
    )
    mark_object_file_deleted(
        db_session,
        object_file_id=deleted.id,
        now=LATEST,
    )

    invalid_calls = (
        lambda: mark_object_file_available(
            db_session,
            object_file_id=failed.id,
            now=LATEST,
        ),
        lambda: mark_object_file_available(
            db_session,
            object_file_id=deleted.id,
            now=LATEST + timedelta(seconds=1),
        ),
        lambda: mark_object_file_failed(
            db_session,
            object_file_id=available.id,
            failure_code=StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
            now=LATEST,
        ),
        lambda: mark_object_file_delete_pending(
            db_session,
            object_file_id=pending.id,
            failure_code=None,
            now=LATER,
        ),
        lambda: mark_object_file_delete_pending(
            db_session,
            object_file_id=available.id,
            failure_code=StorageInternalCode.OBJECT_METADATA_MISMATCH,
            now=LATEST,
        ),
        lambda: mark_object_file_deleted(
            db_session,
            object_file_id=pending.id,
            now=LATER,
        ),
        lambda: mark_pending_upload_outcome_unknown(
            db_session,
            object_file_id=failed.id,
            now=LATEST,
        ),
        lambda: mark_object_file_available(
            db_session,
            object_file_id=pending.id,
            now=NOW - timedelta(seconds=1),
        ),
        lambda: mark_object_file_available(
            db_session,
            object_file_id=pending.id,
            now=NOW.replace(tzinfo=None),
        ),
    )
    for invalid_call in invalid_calls:
        with pytest.raises(ObjectFileStateError):
            invalid_call()

    assert db_session.scalar(select(1)) == 1
    db_session.flush()


@pytest.mark.integration
def test_missing_transition_target_is_a_safe_noop(db_session: Session) -> None:
    assert (
        mark_object_file_available(
            db_session,
            object_file_id=uuid4(),
            now=NOW,
        )
        is None
    )
