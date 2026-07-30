"""Instance-local programmable storage fake for M8 tests."""

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    ObjectStorageService,
    PresignedObjectUrl,
    SanitizedImage,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
    StoredObjectHead,
)
from app.storage.errors import StorageInternalCode


class FakeStorageOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    DEFINITE_FAILURE = "DEFINITE_FAILURE"
    TIMEOUT = "TIMEOUT"
    ACCEPTED_THEN_TIMEOUT = "ACCEPTED_THEN_TIMEOUT"
    MISMATCH = "MISMATCH"


class FakeStorageOperation(StrEnum):
    PUT = "PUT"
    HEAD = "HEAD"
    DELETE = "DELETE"
    PRESIGN_GET = "PRESIGN_GET"
    ENSURE_PRIVATE_BUCKET = "ENSURE_PRIVATE_BUCKET"


@dataclass(frozen=True)
class FakeStorageCall:
    operation: FakeStorageOperation
    ttl_seconds: int | None = None


@dataclass(frozen=True, repr=False)
class _StoredSyntheticObject:
    payload: bytes = field(repr=False)
    content_type: str
    checksum_sha256: str = field(repr=False)


class FakeObjectStorageService:
    def __init__(self) -> None:
        self._objects: dict[
            tuple[str, str],
            _StoredSyntheticObject,
        ] = {}
        self._calls: list[FakeStorageCall] = []
        self._put_outcomes: deque[FakeStorageOutcome] = deque()
        self._head_outcomes: deque[FakeStorageOutcome] = deque()
        self._delete_outcomes: deque[FakeStorageOutcome] = deque()
        self._presign_outcomes: deque[FakeStorageOutcome] = deque()
        self._ensure_outcomes: deque[FakeStorageOutcome] = deque()

    def __repr__(self) -> str:
        return (
            "FakeObjectStorageService("
            f"object_count={len(self._objects)}, "
            f"call_count={len(self._calls)}, "
            "objects=<redacted>, outcomes=<redacted>"
            ")"
        )

    @property
    def calls(self) -> tuple[FakeStorageCall, ...]:
        return tuple(self._calls)

    @property
    def object_count(self) -> int:
        return len(self._objects)

    def queue_put_outcome(self, outcome: FakeStorageOutcome) -> None:
        self._put_outcomes.append(
            _validate_outcome(
                outcome,
                allowed={
                    FakeStorageOutcome.SUCCESS,
                    FakeStorageOutcome.DEFINITE_FAILURE,
                    FakeStorageOutcome.TIMEOUT,
                    FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT,
                    FakeStorageOutcome.MISMATCH,
                },
            )
        )

    def queue_head_outcome(self, outcome: FakeStorageOutcome) -> None:
        self._head_outcomes.append(
            _validate_outcome(
                outcome,
                allowed={
                    FakeStorageOutcome.SUCCESS,
                    FakeStorageOutcome.DEFINITE_FAILURE,
                    FakeStorageOutcome.TIMEOUT,
                    FakeStorageOutcome.MISMATCH,
                },
            )
        )

    def queue_delete_outcome(self, outcome: FakeStorageOutcome) -> None:
        self._delete_outcomes.append(
            _validate_outcome(
                outcome,
                allowed={
                    FakeStorageOutcome.SUCCESS,
                    FakeStorageOutcome.DEFINITE_FAILURE,
                    FakeStorageOutcome.TIMEOUT,
                    FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT,
                },
            )
        )

    def queue_presign_outcome(self, outcome: FakeStorageOutcome) -> None:
        self._presign_outcomes.append(
            _validate_outcome(
                outcome,
                allowed={
                    FakeStorageOutcome.SUCCESS,
                    FakeStorageOutcome.DEFINITE_FAILURE,
                    FakeStorageOutcome.TIMEOUT,
                },
            )
        )

    def queue_ensure_private_bucket_outcome(
        self,
        outcome: FakeStorageOutcome,
    ) -> None:
        self._ensure_outcomes.append(
            _validate_outcome(
                outcome,
                allowed={
                    FakeStorageOutcome.SUCCESS,
                    FakeStorageOutcome.DEFINITE_FAILURE,
                    FakeStorageOutcome.TIMEOUT,
                },
            )
        )

    def put_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
        image: SanitizedImage,
    ) -> StorageProviderOperationResult:
        self._calls.append(FakeStorageCall(FakeStorageOperation.PUT))
        outcome = _next_outcome(self._put_outcomes)
        if outcome is FakeStorageOutcome.DEFINITE_FAILURE:
            _raise_provider_failure(StorageProviderFailureKind.DEFINITE)
        if outcome is FakeStorageOutcome.TIMEOUT:
            _raise_provider_failure(StorageProviderFailureKind.AMBIGUOUS)

        object_identity = _object_identity(bucket, key)
        checksum = image.metadata.checksum_sha256.as_internal_value()
        if outcome is FakeStorageOutcome.MISMATCH:
            checksum = "0" * 64 if checksum != "0" * 64 else "1" * 64
        self._objects[object_identity] = _StoredSyntheticObject(
            payload=image.sanitized_bytes.as_internal_bytes(),
            content_type=image.metadata.content_type,
            checksum_sha256=checksum,
        )
        if outcome is FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT:
            _raise_provider_failure(StorageProviderFailureKind.AMBIGUOUS)
        return StorageProviderOperationResult.SUCCESS

    def head_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
    ) -> StoredObjectHead | None:
        self._calls.append(FakeStorageCall(FakeStorageOperation.HEAD))
        outcome = _next_outcome(self._head_outcomes)
        if outcome in {
            FakeStorageOutcome.DEFINITE_FAILURE,
            FakeStorageOutcome.TIMEOUT,
        }:
            _raise_provider_failure(StorageProviderFailureKind.DEFINITE)

        stored = self._objects.get(_object_identity(bucket, key))
        if stored is None:
            return None
        checksum = stored.checksum_sha256
        size_bytes = len(stored.payload)
        if outcome is FakeStorageOutcome.MISMATCH:
            size_bytes += 1
            checksum = "0" * 64 if checksum != "0" * 64 else "1" * 64
        return StoredObjectHead(
            size_bytes=size_bytes,
            content_type=stored.content_type,
            checksum_sha256=ObjectChecksumSha256(checksum),
        )

    def delete_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
    ) -> StorageProviderOperationResult:
        self._calls.append(FakeStorageCall(FakeStorageOperation.DELETE))
        outcome = _next_outcome(self._delete_outcomes)
        if outcome is FakeStorageOutcome.DEFINITE_FAILURE:
            _raise_provider_failure(StorageProviderFailureKind.DEFINITE)
        if outcome is FakeStorageOutcome.TIMEOUT:
            _raise_provider_failure(StorageProviderFailureKind.AMBIGUOUS)

        self._objects.pop(_object_identity(bucket, key), None)
        if outcome is FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT:
            _raise_provider_failure(StorageProviderFailureKind.AMBIGUOUS)
        return StorageProviderOperationResult.SUCCESS

    def create_presigned_get_url(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
        ttl_seconds: int,
    ) -> PresignedObjectUrl:
        if ttl_seconds < 60 or ttl_seconds > 900:
            raise ValueError("Presigned URL TTL must be between 60 and 900 seconds")
        self._calls.append(
            FakeStorageCall(
                FakeStorageOperation.PRESIGN_GET,
                ttl_seconds=ttl_seconds,
            )
        )
        outcome = _next_outcome(self._presign_outcomes)
        if outcome in {
            FakeStorageOutcome.DEFINITE_FAILURE,
            FakeStorageOutcome.TIMEOUT,
        }:
            _raise_provider_failure(StorageProviderFailureKind.DEFINITE)
        _object_identity(bucket, key)
        return PresignedObjectUrl("https://storage.invalid/presigned-test")

    def ensure_private_bucket(
        self,
        *,
        bucket: BucketName,
    ) -> StorageProviderOperationResult:
        self._calls.append(FakeStorageCall(FakeStorageOperation.ENSURE_PRIVATE_BUCKET))
        outcome = _next_outcome(self._ensure_outcomes)
        if outcome in {
            FakeStorageOutcome.DEFINITE_FAILURE,
            FakeStorageOutcome.TIMEOUT,
        }:
            _raise_provider_failure(StorageProviderFailureKind.DEFINITE)
        bucket.as_internal_value()
        return StorageProviderOperationResult.SUCCESS


assert isinstance(FakeObjectStorageService(), ObjectStorageService)


def _next_outcome(
    outcomes: deque[FakeStorageOutcome],
) -> FakeStorageOutcome:
    if not outcomes:
        return FakeStorageOutcome.SUCCESS
    return outcomes.popleft()


def _validate_outcome(
    outcome: FakeStorageOutcome,
    *,
    allowed: set[FakeStorageOutcome],
) -> FakeStorageOutcome:
    if not isinstance(outcome, FakeStorageOutcome) or outcome not in allowed:
        raise ValueError("Unsupported fake storage outcome")
    return outcome


def _object_identity(
    bucket: BucketName,
    key: ObjectKey,
) -> tuple[str, str]:
    if not isinstance(bucket, BucketName) or not isinstance(key, ObjectKey):
        raise TypeError("Fake storage identity requires validated wrappers")
    return (bucket.as_internal_value(), key.as_internal_value())


def _raise_provider_failure(kind: StorageProviderFailureKind) -> None:
    raise StorageProviderError(
        kind=kind,
        code=StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
    )
