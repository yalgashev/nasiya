import hashlib
import inspect
from collections.abc import Callable

import pytest

from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    ObjectStorageService,
    SanitizedImage,
    SanitizedImageBytes,
    SanitizedImageMetadata,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
)
from tests.storage_fake import (
    FakeObjectStorageService,
    FakeStorageCall,
    FakeStorageOperation,
    FakeStorageOutcome,
)

BUCKET = BucketName("nasiya-private-test")
KEY = ObjectKey("v1/objects/0123456789abcdef0123456789abcdef.png")
OTHER_KEY = ObjectKey("v1/objects/fedcba9876543210fedcba9876543210.png")
PAYLOAD = b"synthetic-image-payload"
CHECKSUM = hashlib.sha256(PAYLOAD).hexdigest()


def _image() -> SanitizedImage:
    return SanitizedImage(
        metadata=SanitizedImageMetadata(
            content_type="image/png",
            canonical_extension="png",
            size_bytes=len(PAYLOAD),
            width_px=3,
            height_px=2,
            checksum_sha256=ObjectChecksumSha256(CHECKSUM),
        ),
        sanitized_bytes=SanitizedImageBytes(PAYLOAD),
    )


def _assert_provider_failure(
    operation: Callable[[], object],
    *,
    kind: StorageProviderFailureKind,
) -> None:
    with pytest.raises(StorageProviderError) as exc_info:
        operation()
    assert exc_info.value.kind is kind


def test_fake_implements_protocol_and_successful_crud_contract() -> None:
    fake = FakeObjectStorageService()

    assert isinstance(fake, ObjectStorageService)
    assert (
        fake.put_object(
            bucket=BUCKET,
            key=KEY,
            image=_image(),
        )
        is StorageProviderOperationResult.SUCCESS
    )
    head = fake.head_object(bucket=BUCKET, key=KEY)
    assert head is not None
    assert head.size_bytes == len(PAYLOAD)
    assert head.content_type == "image/png"
    assert head.checksum_sha256 == ObjectChecksumSha256(CHECKSUM)
    assert fake.object_count == 1
    assert (
        fake.delete_object(
            bucket=BUCKET,
            key=KEY,
        )
        is StorageProviderOperationResult.SUCCESS
    )
    assert (
        fake.delete_object(
            bucket=BUCKET,
            key=KEY,
        )
        is StorageProviderOperationResult.SUCCESS
    )
    assert fake.head_object(bucket=BUCKET, key=KEY) is None
    assert [call.operation for call in fake.calls] == [
        FakeStorageOperation.PUT,
        FakeStorageOperation.HEAD,
        FakeStorageOperation.DELETE,
        FakeStorageOperation.DELETE,
        FakeStorageOperation.HEAD,
    ]


def test_put_programs_definite_timeout_accepted_timeout_and_mismatch() -> None:
    definite = FakeObjectStorageService()
    definite.queue_put_outcome(FakeStorageOutcome.DEFINITE_FAILURE)
    _assert_provider_failure(
        lambda: definite.put_object(bucket=BUCKET, key=KEY, image=_image()),
        kind=StorageProviderFailureKind.DEFINITE,
    )
    assert definite.object_count == 0

    timeout = FakeObjectStorageService()
    timeout.queue_put_outcome(FakeStorageOutcome.TIMEOUT)
    _assert_provider_failure(
        lambda: timeout.put_object(bucket=BUCKET, key=KEY, image=_image()),
        kind=StorageProviderFailureKind.AMBIGUOUS,
    )
    assert timeout.head_object(bucket=BUCKET, key=KEY) is None

    accepted = FakeObjectStorageService()
    accepted.queue_put_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)
    _assert_provider_failure(
        lambda: accepted.put_object(bucket=BUCKET, key=KEY, image=_image()),
        kind=StorageProviderFailureKind.AMBIGUOUS,
    )
    assert accepted.head_object(bucket=BUCKET, key=KEY).size_bytes == len(PAYLOAD)

    mismatch = FakeObjectStorageService()
    mismatch.queue_put_outcome(FakeStorageOutcome.MISMATCH)
    assert (
        mismatch.put_object(
            bucket=BUCKET,
            key=KEY,
            image=_image(),
        )
        is StorageProviderOperationResult.SUCCESS
    )
    mismatched_head = mismatch.head_object(bucket=BUCKET, key=KEY)
    assert mismatched_head.checksum_sha256 != ObjectChecksumSha256(CHECKSUM)


def test_head_programs_timeout_and_mismatch_without_mutation() -> None:
    fake = FakeObjectStorageService()
    fake.put_object(bucket=BUCKET, key=KEY, image=_image())
    fake.queue_head_outcome(FakeStorageOutcome.TIMEOUT)
    _assert_provider_failure(
        lambda: fake.head_object(bucket=BUCKET, key=KEY),
        kind=StorageProviderFailureKind.DEFINITE,
    )
    fake.queue_head_outcome(FakeStorageOutcome.MISMATCH)
    mismatch = fake.head_object(bucket=BUCKET, key=KEY)
    assert mismatch.size_bytes == len(PAYLOAD) + 1
    assert fake.object_count == 1


def test_delete_programs_timeout_and_accepted_then_timeout() -> None:
    timeout = FakeObjectStorageService()
    timeout.put_object(bucket=BUCKET, key=KEY, image=_image())
    timeout.queue_delete_outcome(FakeStorageOutcome.TIMEOUT)
    _assert_provider_failure(
        lambda: timeout.delete_object(bucket=BUCKET, key=KEY),
        kind=StorageProviderFailureKind.AMBIGUOUS,
    )
    assert timeout.head_object(bucket=BUCKET, key=KEY) is not None

    accepted = FakeObjectStorageService()
    accepted.put_object(bucket=BUCKET, key=KEY, image=_image())
    accepted.queue_delete_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)
    _assert_provider_failure(
        lambda: accepted.delete_object(bucket=BUCKET, key=KEY),
        kind=StorageProviderFailureKind.AMBIGUOUS,
    )
    assert accepted.head_object(bucket=BUCKET, key=KEY) is None


def test_presign_and_private_bucket_outcomes_are_typed_and_bounded() -> None:
    fake = FakeObjectStorageService()

    url = fake.create_presigned_get_url(
        bucket=BUCKET,
        key=OTHER_KEY,
        ttl_seconds=300,
    )
    assert url.as_response_value() == "https://storage.invalid/presigned-test"
    assert (
        fake.ensure_private_bucket(
            bucket=BUCKET,
        )
        is StorageProviderOperationResult.SUCCESS
    )
    assert fake.calls == (
        FakeStorageCall(FakeStorageOperation.PRESIGN_GET, ttl_seconds=300),
        FakeStorageCall(FakeStorageOperation.ENSURE_PRIVATE_BUCKET),
    )

    for invalid_ttl in (59, 901):
        with pytest.raises(ValueError, match="between 60 and 900"):
            fake.create_presigned_get_url(
                bucket=BUCKET,
                key=KEY,
                ttl_seconds=invalid_ttl,
            )

    fake.queue_presign_outcome(FakeStorageOutcome.TIMEOUT)
    _assert_provider_failure(
        lambda: fake.create_presigned_get_url(
            bucket=BUCKET,
            key=KEY,
            ttl_seconds=300,
        ),
        kind=StorageProviderFailureKind.DEFINITE,
    )
    fake.queue_ensure_private_bucket_outcome(FakeStorageOutcome.DEFINITE_FAILURE)
    _assert_provider_failure(
        lambda: fake.ensure_private_bucket(bucket=BUCKET),
        kind=StorageProviderFailureKind.DEFINITE,
    )


def test_fake_rejects_operation_incompatible_programming() -> None:
    fake = FakeObjectStorageService()

    with pytest.raises(ValueError, match="Unsupported"):
        fake.queue_delete_outcome(FakeStorageOutcome.MISMATCH)
    with pytest.raises(ValueError, match="Unsupported"):
        fake.queue_head_outcome(FakeStorageOutcome.ACCEPTED_THEN_TIMEOUT)
    with pytest.raises(ValueError, match="Unsupported"):
        fake.queue_presign_outcome(FakeStorageOutcome.MISMATCH)


def test_fake_state_calls_and_repr_do_not_expose_sensitive_values(caplog) -> None:
    fake = FakeObjectStorageService()
    fake.put_object(bucket=BUCKET, key=KEY, image=_image())
    url = fake.create_presigned_get_url(
        bucket=BUCKET,
        key=KEY,
        ttl_seconds=300,
    )

    rendered = f"{fake!r} {fake.calls!r} {url!r}"
    for forbidden in (
        BUCKET.as_internal_value(),
        KEY.as_internal_value(),
        PAYLOAD.decode(),
        CHECKSUM,
        url.as_response_value(),
    ):
        assert forbidden not in rendered
        assert forbidden not in caplog.text


def test_fake_is_test_only_and_has_no_module_global_mutable_state() -> None:
    import app.storage
    import tests.storage_fake as storage_fake

    production_source = inspect.getsource(app.storage)
    fake_source = inspect.getsource(storage_fake)
    module_values = vars(storage_fake).values()

    assert "tests.storage_fake" not in production_source
    assert "FakeObjectStorageService" not in production_source
    assert not any(
        isinstance(value, FakeObjectStorageService) for value in module_values
    )
    assert "logger" not in fake_source
    assert "logging" not in fake_source
    assert "print(" not in fake_source
