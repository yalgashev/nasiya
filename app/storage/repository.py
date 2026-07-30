"""Caller-transaction-owned persistence primitives for object files."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    SanitizedImageMetadata,
)
from app.storage.errors import StorageInternalCode
from app.storage.models import ObjectFile, ObjectFileStatus

__all__ = (
    "ClaimedObjectFile",
    "ObjectFileStateError",
    "claim_stale_delete_pending",
    "claim_stale_pending_uploads",
    "create_pending_object_file",
    "load_available_object_file",
    "load_object_file",
    "load_object_file_for_update",
    "mark_delete_outcome_unknown",
    "mark_object_file_available",
    "mark_object_file_delete_pending",
    "mark_object_file_deleted",
    "mark_object_file_failed",
    "mark_pending_upload_outcome_unknown",
)

OBJECT_FILE_CLAIM_MAX_BATCH_SIZE = 5000
_FAILED_CODES = frozenset(
    {
        StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
        StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
    }
)


@dataclass(frozen=True, repr=False)
class ClaimedObjectFile:
    object_file_id: UUID
    bucket: BucketName
    object_key: ObjectKey
    content_type: str
    size_bytes: int
    checksum_sha256: ObjectChecksumSha256
    width_px: int
    height_px: int
    status: ObjectFileStatus
    failure_code: str | None
    claimed_at: datetime

    def __repr__(self) -> str:
        return (
            "ClaimedObjectFile("
            "object_file_id=<redacted>, "
            "bucket=<redacted>, object_key=<redacted>, "
            f"content_type={self.content_type!r}, "
            f"size_bytes={self.size_bytes!r}, "
            "checksum_sha256=<redacted>, "
            f"width_px={self.width_px!r}, "
            f"height_px={self.height_px!r}, "
            f"status={self.status.value!r}, "
            f"failure_code={self.failure_code!r}, "
            f"claimed_at={self.claimed_at!r}"
            ")"
        )


class ObjectFileStateError(RuntimeError):
    """A requested object-file lifecycle transition is not valid."""

    def __init__(self) -> None:
        super().__init__("Object file lifecycle transition is not valid")


def create_pending_object_file(
    session: Session,
    *,
    object_file_id: UUID,
    bucket: BucketName,
    object_key: ObjectKey,
    metadata: SanitizedImageMetadata,
    created_by_user_id: UUID,
    now: datetime,
) -> ObjectFile:
    current_time = _as_utc(now)
    object_file = ObjectFile(
        id=_validate_uuid(object_file_id, "Object file id"),
        bucket=bucket.as_internal_value(),
        object_key=object_key.as_internal_value(),
        content_type=metadata.content_type,
        size_bytes=metadata.size_bytes,
        checksum_sha256=metadata.checksum_sha256.as_internal_value(),
        width_px=metadata.width_px,
        height_px=metadata.height_px,
        status=ObjectFileStatus.PENDING_UPLOAD.value,
        created_by_user_id=_validate_uuid(
            created_by_user_id,
            "Creator user id",
        ),
        failure_code=None,
        created_at=current_time,
        updated_at=current_time,
        available_at=None,
        terminal_at=None,
        deleted_at=None,
    )
    session.add(object_file)
    session.flush()
    return object_file


def load_object_file(
    session: Session,
    *,
    object_file_id: UUID,
) -> ObjectFile | None:
    return session.get(
        ObjectFile,
        _validate_uuid(object_file_id, "Object file id"),
    )


def load_object_file_for_update(
    session: Session,
    *,
    object_file_id: UUID,
) -> ObjectFile | None:
    statement = (
        select(ObjectFile)
        .where(
            ObjectFile.id == _validate_uuid(object_file_id, "Object file id"),
        )
        .with_for_update()
    )
    return session.scalar(statement)


def load_available_object_file(
    session: Session,
    *,
    object_file_id: UUID,
) -> ObjectFile | None:
    statement = select(ObjectFile).where(
        ObjectFile.id == _validate_uuid(object_file_id, "Object file id"),
        ObjectFile.status == ObjectFileStatus.AVAILABLE.value,
    )
    return session.scalar(statement)


def mark_object_file_available(
    session: Session,
    *,
    object_file_id: UUID,
    now: datetime,
) -> ObjectFile | None:
    object_file = _lock_transition_row(
        session,
        object_file_id=object_file_id,
    )
    if object_file is None:
        return None
    transition_time = _transition_time(object_file, now)
    if object_file.status == ObjectFileStatus.AVAILABLE.value:
        return object_file
    _require_status(object_file, ObjectFileStatus.PENDING_UPLOAD)

    object_file.status = ObjectFileStatus.AVAILABLE.value
    object_file.failure_code = None
    object_file.available_at = transition_time
    object_file.updated_at = transition_time
    session.flush()
    return object_file


def mark_object_file_failed(
    session: Session,
    *,
    object_file_id: UUID,
    failure_code: StorageInternalCode,
    now: datetime,
) -> ObjectFile | None:
    if failure_code not in _FAILED_CODES:
        raise ObjectFileStateError
    object_file = _lock_transition_row(
        session,
        object_file_id=object_file_id,
    )
    if object_file is None:
        return None
    transition_time = _transition_time(object_file, now)
    if object_file.status == ObjectFileStatus.FAILED.value:
        if object_file.failure_code != failure_code.value:
            raise ObjectFileStateError
        return object_file
    _require_status(object_file, ObjectFileStatus.PENDING_UPLOAD)

    object_file.status = ObjectFileStatus.FAILED.value
    object_file.failure_code = failure_code.value
    object_file.terminal_at = transition_time
    object_file.updated_at = transition_time
    session.flush()
    return object_file


def mark_object_file_delete_pending(
    session: Session,
    *,
    object_file_id: UUID,
    failure_code: StorageInternalCode | None,
    now: datetime,
) -> ObjectFile | None:
    if failure_code not in {
        None,
        StorageInternalCode.OBJECT_METADATA_MISMATCH,
    }:
        raise ObjectFileStateError
    object_file = _lock_transition_row(
        session,
        object_file_id=object_file_id,
    )
    if object_file is None:
        return None
    transition_time = _transition_time(object_file, now)
    if object_file.status == ObjectFileStatus.DELETE_PENDING.value:
        expected_code = failure_code.value if failure_code is not None else None
        if object_file.failure_code != expected_code:
            raise ObjectFileStateError
        return object_file
    if object_file.status == ObjectFileStatus.PENDING_UPLOAD.value:
        if failure_code is not StorageInternalCode.OBJECT_METADATA_MISMATCH:
            raise ObjectFileStateError
    elif object_file.status == ObjectFileStatus.AVAILABLE.value:
        if failure_code is not None:
            raise ObjectFileStateError
    else:
        raise ObjectFileStateError

    object_file.status = ObjectFileStatus.DELETE_PENDING.value
    object_file.failure_code = failure_code.value if failure_code is not None else None
    object_file.updated_at = transition_time
    session.flush()
    return object_file


def mark_object_file_deleted(
    session: Session,
    *,
    object_file_id: UUID,
    now: datetime,
) -> ObjectFile | None:
    object_file = _lock_transition_row(
        session,
        object_file_id=object_file_id,
    )
    if object_file is None:
        return None
    transition_time = _transition_time(object_file, now)
    if object_file.status == ObjectFileStatus.DELETED.value:
        return object_file
    _require_status(object_file, ObjectFileStatus.DELETE_PENDING)

    object_file.status = ObjectFileStatus.DELETED.value
    object_file.terminal_at = transition_time
    object_file.deleted_at = transition_time
    object_file.updated_at = transition_time
    session.flush()
    return object_file


def mark_delete_outcome_unknown(
    session: Session,
    *,
    object_file_id: UUID,
    now: datetime,
) -> ObjectFile | None:
    object_file = _lock_transition_row(
        session,
        object_file_id=object_file_id,
    )
    if object_file is None:
        return None
    transition_time = _transition_time(object_file, now)
    _require_status(object_file, ObjectFileStatus.DELETE_PENDING)
    if object_file.failure_code == StorageInternalCode.DELETE_OUTCOME_UNKNOWN.value:
        return object_file

    object_file.failure_code = StorageInternalCode.DELETE_OUTCOME_UNKNOWN.value
    object_file.updated_at = transition_time
    session.flush()
    return object_file


def mark_pending_upload_outcome_unknown(
    session: Session,
    *,
    object_file_id: UUID,
    now: datetime,
) -> ObjectFile | None:
    object_file = _lock_transition_row(
        session,
        object_file_id=object_file_id,
    )
    if object_file is None:
        return None
    transition_time = _transition_time(object_file, now)
    _require_status(object_file, ObjectFileStatus.PENDING_UPLOAD)
    if object_file.failure_code == StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN.value:
        return object_file

    object_file.failure_code = StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN.value
    object_file.updated_at = transition_time
    session.flush()
    return object_file


def claim_stale_pending_uploads(
    session: Session,
    *,
    now: datetime,
    stale_seconds: int,
    batch_size: int,
) -> list[ClaimedObjectFile]:
    return _claim_stale_object_files(
        session,
        status=ObjectFileStatus.PENDING_UPLOAD,
        now=now,
        stale_seconds=stale_seconds,
        batch_size=batch_size,
    )


def claim_stale_delete_pending(
    session: Session,
    *,
    now: datetime,
    stale_seconds: int,
    batch_size: int,
) -> list[ClaimedObjectFile]:
    return _claim_stale_object_files(
        session,
        status=ObjectFileStatus.DELETE_PENDING,
        now=now,
        stale_seconds=stale_seconds,
        batch_size=batch_size,
    )


def _claim_stale_object_files(
    session: Session,
    *,
    status: ObjectFileStatus,
    now: datetime,
    stale_seconds: int,
    batch_size: int,
) -> list[ClaimedObjectFile]:
    claim_time = _as_utc(now)
    stale_interval = _validate_stale_seconds(stale_seconds)
    batch_limit = _validate_batch_size(batch_size)
    stale_before = claim_time - timedelta(seconds=stale_interval)
    statement = (
        select(ObjectFile)
        .where(
            ObjectFile.status == status.value,
            ObjectFile.updated_at <= stale_before,
        )
        .order_by(ObjectFile.updated_at.asc(), ObjectFile.id.asc())
        .limit(batch_limit)
        .with_for_update(skip_locked=True)
    )
    object_files = list(session.scalars(statement).all())
    claims: list[ClaimedObjectFile] = []
    for object_file in object_files:
        object_file.updated_at = claim_time
        claims.append(_claim_snapshot(object_file, claimed_at=claim_time))
    session.flush()
    return claims


def _claim_snapshot(
    object_file: ObjectFile,
    *,
    claimed_at: datetime,
) -> ClaimedObjectFile:
    return ClaimedObjectFile(
        object_file_id=object_file.id,
        bucket=BucketName(object_file.bucket),
        object_key=ObjectKey(object_file.object_key),
        content_type=object_file.content_type,
        size_bytes=object_file.size_bytes,
        checksum_sha256=ObjectChecksumSha256(object_file.checksum_sha256),
        width_px=object_file.width_px,
        height_px=object_file.height_px,
        status=ObjectFileStatus(object_file.status),
        failure_code=object_file.failure_code,
        claimed_at=claimed_at,
    )


def _lock_transition_row(
    session: Session,
    *,
    object_file_id: UUID,
) -> ObjectFile | None:
    return load_object_file_for_update(
        session,
        object_file_id=object_file_id,
    )


def _require_status(
    object_file: ObjectFile,
    expected: ObjectFileStatus,
) -> None:
    if object_file.status != expected.value:
        raise ObjectFileStateError


def _transition_time(object_file: ObjectFile, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObjectFileStateError
    current_time = value.astimezone(UTC)
    if current_time < object_file.created_at or current_time < object_file.updated_at:
        raise ObjectFileStateError
    return current_time


def _validate_stale_seconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Object file stale seconds must be positive")
    return value


def _validate_batch_size(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > OBJECT_FILE_CLAIM_MAX_BATCH_SIZE
    ):
        raise ValueError("Object file claim batch size must be between 1 and 5000")
    return value


def _validate_uuid(value: UUID, label: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{label} must be a UUID")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Object file timestamps must be timezone-aware")
    return value.astimezone(UTC)
