import inspect
from uuid import UUID

import pytest
from pydantic import SecretStr

from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectFileAccessAuthorizer,
    ObjectKey,
    ObjectReadAuthorizationRequest,
    ObjectReadAuthorizationResult,
    ObjectStorageService,
    PresignedObjectUrl,
    SanitizedImage,
    SanitizedImageBytes,
    SanitizedImageMetadata,
    StorageConfig,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
    StoredObjectHead,
)
from app.storage.errors import StorageInternalCode

RAW_BUCKET = "private-bucket"
RAW_KEY = "v1/objects/0123456789abcdef0123456789abcdef.jpg"
RAW_CHECKSUM = "a" * 64
RAW_URL = (
    "http://storage.invalid/private-bucket/object?"
    "X-Amz-Credential=synthetic&X-Amz-Signature=sensitive"
)
RAW_BYTES = b"synthetic-sanitized-image-bytes"


def make_metadata() -> SanitizedImageMetadata:
    return SanitizedImageMetadata(
        content_type="image/jpeg",
        canonical_extension="jpg",
        size_bytes=len(RAW_BYTES),
        width_px=2,
        height_px=3,
        checksum_sha256=ObjectChecksumSha256(RAW_CHECKSUM),
    )


def test_sensitive_value_objects_are_validated_and_redacted() -> None:
    bucket = BucketName(RAW_BUCKET)
    key = ObjectKey(RAW_KEY)
    checksum = ObjectChecksumSha256(RAW_CHECKSUM)
    url = PresignedObjectUrl(RAW_URL)
    image_bytes = SanitizedImageBytes(RAW_BYTES)

    rendered = " ".join(
        (
            repr(bucket),
            str(bucket),
            repr(key),
            str(key),
            repr(checksum),
            str(checksum),
            repr(url),
            str(url),
            repr(image_bytes),
            str(image_bytes),
        )
    )
    for sensitive_value in (
        RAW_BUCKET,
        RAW_KEY,
        RAW_CHECKSUM,
        RAW_URL,
        RAW_BYTES.decode(),
        "X-Amz-Signature",
    ):
        assert sensitive_value not in rendered

    assert bucket.as_internal_value() == RAW_BUCKET
    assert key.as_internal_value() == RAW_KEY
    assert checksum.as_internal_value() == RAW_CHECKSUM
    assert url.as_response_value() == RAW_URL
    assert image_bytes.as_internal_bytes() == RAW_BYTES


@pytest.mark.parametrize(
    ("factory", "bad_value"),
    [
        (BucketName, "Invalid_Bucket"),
        (BucketName, "192.0.2.10"),
        (ObjectKey, "customer/passport.jpg"),
        (ObjectKey, "v1/objects/ABC.jpg"),
        (ObjectChecksumSha256, "A" * 64),
        (ObjectChecksumSha256, "a" * 63),
        (PresignedObjectUrl, "javascript:alert(1)"),
        (SanitizedImageBytes, b""),
    ],
)
def test_sensitive_value_objects_reject_invalid_values(factory, bad_value) -> None:
    with pytest.raises(ValueError):
        factory(bad_value)


def test_sanitized_image_and_head_hide_payload_and_full_checksum() -> None:
    metadata = make_metadata()
    image = SanitizedImage(
        metadata=metadata,
        sanitized_bytes=SanitizedImageBytes(RAW_BYTES),
    )
    head = StoredObjectHead(
        size_bytes=len(RAW_BYTES),
        content_type="image/jpeg",
        checksum_sha256=ObjectChecksumSha256(RAW_CHECKSUM),
    )

    rendered = f"{metadata!r} {image!r} {head!r}"
    assert RAW_BYTES.decode() not in rendered
    assert RAW_CHECKSUM not in rendered
    assert image.metadata == metadata


def test_storage_config_hides_endpoint_bucket_and_credentials() -> None:
    endpoint = "http://storage.invalid:9000"
    access_key = "synthetic-access-key"
    secret_key = "synthetic-secret-key"
    config = StorageConfig(
        endpoint_url=SecretStr(endpoint),
        region="us-east-1",
        bucket=RAW_BUCKET,
        access_key=SecretStr(access_key),
        secret_key=SecretStr(secret_key),
        use_ssl=False,
        addressing_style="path",
        presigned_ttl_seconds=300,
        max_upload_bytes=10_485_760,
        max_multipart_bytes=11_010_048,
        max_image_pixels=40_000_000,
        max_image_dimension=16_384,
        upload_rate_limit_window_seconds=900,
        upload_rate_limit_user_attempts=5,
        upload_rate_limit_ip_attempts=20,
        reconcile_stale_seconds=60,
    )

    rendered = f"{config!r} {config!s}"
    for sensitive_value in (endpoint, RAW_BUCKET, access_key, secret_key):
        assert sensitive_value not in rendered


def test_storage_provider_error_contains_safe_codes_only() -> None:
    error = StorageProviderError(
        kind=StorageProviderFailureKind.AMBIGUOUS,
        code=StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN,
    )

    assert error.kind is StorageProviderFailureKind.AMBIGUOUS
    assert error.code is StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN
    assert str(error) == "UPLOAD_OUTCOME_UNKNOWN"
    assert "AMBIGUOUS" in repr(error)
    assert RAW_KEY not in repr(error)


def test_storage_protocol_has_only_narrow_get_presign_contract() -> None:
    assert ObjectStorageService._is_runtime_protocol
    expected_methods = {
        "put_object",
        "head_object",
        "delete_object",
        "create_presigned_get_url",
        "ensure_private_bucket",
    }
    assert expected_methods <= set(ObjectStorageService.__dict__)
    assert "presigned_put" not in inspect.getsource(ObjectStorageService).casefold()
    assert StorageProviderOperationResult.SUCCESS.value == "SUCCESS"
    assert StorageProviderOperationResult.MISSING.value == "MISSING"


def test_authorization_request_redacts_domain_parent_and_protocol_is_narrow() -> None:
    class AllowAuthorizer:
        def authorize(
            self,
            request: ObjectReadAuthorizationRequest,
        ) -> ObjectReadAuthorizationResult:
            _ = request
            return ObjectReadAuthorizationResult.ALLOWED

    parent_reference = "synthetic-private-domain-parent-reference"
    request = ObjectReadAuthorizationRequest(
        actor_user_id=UUID("11111111-1111-1111-1111-111111111111"),
        object_file_id=UUID("22222222-2222-2222-2222-222222222222"),
        domain_parent_reference=parent_reference,
    )
    authorizer = AllowAuthorizer()

    assert isinstance(authorizer, ObjectFileAccessAuthorizer)
    assert authorizer.authorize(request) is ObjectReadAuthorizationResult.ALLOWED
    assert parent_reference not in repr(request)
