import hashlib
import inspect
from collections.abc import Callable

import pytest
from botocore.exceptions import (
    BotoCoreError,
    ConnectionClosedError,
    EndpointConnectionError,
    HTTPClientError,
    ParamValidationError,
    ReadTimeoutError,
)
from botocore.stub import Stubber
from pydantic import SecretStr

import app.storage.s3 as storage_s3
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    SanitizedImage,
    SanitizedImageBytes,
    SanitizedImageMetadata,
    StorageConfig,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
)
from app.storage.s3 import (
    S3_MAX_PUT_BYTES,
    S3ObjectStorageService,
    create_s3_client,
)

BUCKET = BucketName("nasiya-private-test")
KEY = ObjectKey("v1/objects/0123456789abcdef0123456789abcdef.png")
PAYLOAD = b"synthetic-s3-image"
CHECKSUM = hashlib.sha256(PAYLOAD).hexdigest()
EXPECTED_IDENTITY = {
    "Bucket": "nasiya-private-test",
    "Key": "v1/objects/0123456789abcdef0123456789abcdef.png",
}
EXPECTED_PUT = {
    **EXPECTED_IDENTITY,
    "Body": PAYLOAD,
    "ContentLength": len(PAYLOAD),
    "ContentType": "image/png",
    "Metadata": {"checksum-sha256": CHECKSUM},
}


def _config() -> StorageConfig:
    return StorageConfig(
        endpoint_url=SecretStr("http://127.0.0.1:19000"),
        region="us-east-1",
        bucket="nasiya-private-test",
        access_key=SecretStr("synthetic-access"),
        secret_key=SecretStr("synthetic-secret"),
        use_ssl=False,
        addressing_style="path",
        presigned_ttl_seconds=300,
        max_upload_bytes=S3_MAX_PUT_BYTES,
        max_multipart_bytes=11_010_048,
        max_image_pixels=40_000_000,
        max_image_dimension=16_384,
        upload_rate_limit_window_seconds=900,
        upload_rate_limit_user_attempts=5,
        upload_rate_limit_ip_attempts=20,
        reconcile_stale_seconds=60,
    )


def _image(
    *,
    payload: bytes = PAYLOAD,
    size_bytes: int | None = None,
) -> SanitizedImage:
    resolved_size = len(payload) if size_bytes is None else size_bytes
    return SanitizedImage(
        metadata=SanitizedImageMetadata(
            content_type="image/png",
            canonical_extension="png",
            size_bytes=resolved_size,
            width_px=4,
            height_px=3,
            checksum_sha256=ObjectChecksumSha256(hashlib.sha256(payload).hexdigest()),
        ),
        sanitized_bytes=SanitizedImageBytes(payload),
    )


def _adapter_with_stubber() -> tuple[S3ObjectStorageService, Stubber]:
    client = create_s3_client(_config())
    return S3ObjectStorageService(client), Stubber(client)


def _assert_failure(
    operation: Callable[[], object],
    *,
    kind: StorageProviderFailureKind,
) -> StorageProviderError:
    with pytest.raises(StorageProviderError) as exc_info:
        operation()
    assert exc_info.value.kind is kind
    return exc_info.value


def test_put_object_sends_exact_sanitized_payload_and_metadata_once() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response(
        "put_object",
        {"ETag": '"provider-etag-is-not-a-checksum"'},
        expected_params=EXPECTED_PUT,
    )

    with stubber:
        result = adapter.put_object(bucket=BUCKET, key=KEY, image=_image())

    assert result is StorageProviderOperationResult.SUCCESS
    stubber.assert_no_pending_responses()


def test_put_rejects_payload_over_ten_mib_before_sdk_call() -> None:
    adapter, stubber = _adapter_with_stubber()
    payload = b"x" * (S3_MAX_PUT_BYTES + 1)

    with stubber:
        _assert_failure(
            lambda: adapter.put_object(
                bucket=BUCKET,
                key=KEY,
                image=_image(payload=payload),
            ),
            kind=StorageProviderFailureKind.DEFINITE,
        )
    stubber.assert_no_pending_responses()


def test_head_returns_only_typed_metadata_and_ignores_etag() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response(
        "head_object",
        {
            "ContentLength": len(PAYLOAD),
            "ContentType": "image/png",
            "Metadata": {"checksum-sha256": CHECKSUM},
            "ETag": f'"{hashlib.md5(PAYLOAD, usedforsecurity=False).hexdigest()}"',
        },
        expected_params=EXPECTED_IDENTITY,
    )

    with stubber:
        result = adapter.head_object(bucket=BUCKET, key=KEY)

    assert result is not None
    assert result.size_bytes == len(PAYLOAD)
    assert result.content_type == "image/png"
    assert result.checksum_sha256 == ObjectChecksumSha256(CHECKSUM)
    assert (
        result.checksum_sha256.as_internal_value()
        != hashlib.md5(
            PAYLOAD,
            usedforsecurity=False,
        ).hexdigest()
    )


def test_head_404_is_typed_missing_but_403_is_not_hidden() -> None:
    missing_adapter, missing_stubber = _adapter_with_stubber()
    missing_stubber.add_client_error(
        "head_object",
        service_error_code="404",
        service_message="synthetic missing",
        http_status_code=404,
        expected_params=EXPECTED_IDENTITY,
    )
    with missing_stubber:
        assert missing_adapter.head_object(bucket=BUCKET, key=KEY) is None

    denied_adapter, denied_stubber = _adapter_with_stubber()
    denied_stubber.add_client_error(
        "head_object",
        service_error_code="AccessDenied",
        service_message="synthetic denied",
        http_status_code=403,
        expected_params=EXPECTED_IDENTITY,
    )
    with denied_stubber:
        _assert_failure(
            lambda: denied_adapter.head_object(bucket=BUCKET, key=KEY),
            kind=StorageProviderFailureKind.DEFINITE,
        )


@pytest.mark.parametrize(
    "response",
    (
        {"ContentLength": len(PAYLOAD), "ContentType": "image/png"},
        {
            "ContentLength": len(PAYLOAD),
            "ContentType": "image/png",
            "Metadata": {},
        },
        {
            "ContentLength": len(PAYLOAD),
            "ContentType": "image/png",
            "Metadata": {"checksum-sha256": "UPPERCASE"},
        },
        {
            "ContentLength": len(PAYLOAD),
            "ContentType": "image/png",
            "Metadata": {
                "checksum-sha256": CHECKSUM,
                "provider-detail": "must-not-be-accepted",
            },
        },
        {
            "ContentLength": len(PAYLOAD),
            "ContentType": "text/plain",
            "Metadata": {"checksum-sha256": CHECKSUM},
        },
    ),
)
def test_head_rejects_missing_or_malformed_metadata(
    response: dict[str, object],
) -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response(
        "head_object",
        response,
        expected_params=EXPECTED_IDENTITY,
    )

    with stubber:
        _assert_failure(
            lambda: adapter.head_object(bucket=BUCKET, key=KEY),
            kind=StorageProviderFailureKind.DEFINITE,
        )


def test_delete_is_idempotent_for_success_and_provider_404() -> None:
    success_adapter, success_stubber = _adapter_with_stubber()
    success_stubber.add_response(
        "delete_object",
        {},
        expected_params=EXPECTED_IDENTITY,
    )
    with success_stubber:
        assert (
            success_adapter.delete_object(
                bucket=BUCKET,
                key=KEY,
            )
            is StorageProviderOperationResult.SUCCESS
        )

    missing_adapter, missing_stubber = _adapter_with_stubber()
    missing_stubber.add_client_error(
        "delete_object",
        service_error_code="NoSuchKey",
        service_message="synthetic missing",
        http_status_code=404,
        expected_params=EXPECTED_IDENTITY,
    )
    with missing_stubber:
        assert (
            missing_adapter.delete_object(
                bucket=BUCKET,
                key=KEY,
            )
            is StorageProviderOperationResult.SUCCESS
        )


@pytest.mark.parametrize(
    ("operation_name", "http_status", "expected_kind"),
    (
        ("put_object", 400, StorageProviderFailureKind.DEFINITE),
        ("put_object", 408, StorageProviderFailureKind.AMBIGUOUS),
        ("put_object", 429, StorageProviderFailureKind.AMBIGUOUS),
        ("put_object", 503, StorageProviderFailureKind.AMBIGUOUS),
        ("delete_object", 500, StorageProviderFailureKind.AMBIGUOUS),
        ("head_object", 503, StorageProviderFailureKind.DEFINITE),
    ),
)
def test_client_error_status_classification_with_stubber(
    operation_name: str,
    http_status: int,
    expected_kind: StorageProviderFailureKind,
) -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_client_error(
        operation_name,
        service_error_code="SyntheticError",
        service_message="sensitive provider detail",
        http_status_code=http_status,
        expected_params=(
            EXPECTED_PUT if operation_name == "put_object" else EXPECTED_IDENTITY
        ),
    )

    operation = getattr(adapter, operation_name)
    kwargs = {"bucket": BUCKET, "key": KEY}
    if operation_name == "put_object":
        kwargs["image"] = _image()
    with stubber:
        error = _assert_failure(
            lambda: operation(**kwargs),
            kind=expected_kind,
        )
    assert "sensitive provider detail" not in str(error)
    assert "sensitive provider detail" not in repr(error)


@pytest.mark.parametrize(
    ("exception", "operation_name", "expected_kind"),
    (
        (
            ReadTimeoutError(endpoint_url="https://sensitive.invalid"),
            "put_object",
            StorageProviderFailureKind.AMBIGUOUS,
        ),
        (
            ConnectionClosedError(endpoint_url="https://sensitive.invalid"),
            "delete_object",
            StorageProviderFailureKind.AMBIGUOUS,
        ),
        (
            EndpointConnectionError(endpoint_url="https://sensitive.invalid"),
            "put_object",
            StorageProviderFailureKind.DEFINITE,
        ),
        (
            ReadTimeoutError(endpoint_url="https://sensitive.invalid"),
            "head_object",
            StorageProviderFailureKind.DEFINITE,
        ),
        (
            HTTPClientError(error="sensitive transport detail"),
            "put_object",
            StorageProviderFailureKind.AMBIGUOUS,
        ),
        (
            BotoCoreError(),
            "delete_object",
            StorageProviderFailureKind.AMBIGUOUS,
        ),
        (
            ParamValidationError(report="sensitive validation detail"),
            "put_object",
            StorageProviderFailureKind.DEFINITE,
        ),
    ),
)
def test_transport_and_sdk_exception_classification_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    exception: BotoCoreError,
    operation_name: str,
    expected_kind: StorageProviderFailureKind,
) -> None:
    adapter, _stubber = _adapter_with_stubber()

    def fail(**_kwargs: object) -> None:
        raise exception

    monkeypatch.setattr(adapter._client, operation_name, fail)
    operation = getattr(adapter, operation_name)
    kwargs = {"bucket": BUCKET, "key": KEY}
    if operation_name == "put_object":
        kwargs["image"] = _image()

    error = _assert_failure(
        lambda: operation(**kwargs),
        kind=expected_kind,
    )
    rendered = f"{error!s} {error!r}"
    assert "sensitive" not in rendered
    assert "0123456789abcdef" not in rendered


def test_adapter_repr_and_source_have_no_sensitive_output_or_retry() -> None:
    adapter, _stubber = _adapter_with_stubber()
    source = inspect.getsource(storage_s3.S3ObjectStorageService)
    rendered = repr(adapter)

    assert "127.0.0.1" not in rendered
    assert "synthetic-access" not in rendered
    assert "synthetic-secret" not in rendered
    assert KEY.as_internal_value() not in rendered
    assert "logger" not in source
    assert "logging" not in source
    assert "print(" not in source
    assert "ETag" not in source
    assert "ACL" not in source
    assert "create_presigned_put_url" not in source
    assert source.count(".put_object(") == 1
