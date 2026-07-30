import inspect
import os
from collections.abc import Generator
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from PIL import Image
from pydantic import SecretStr

import app.storage.s3 as storage_s3
from app.storage.contracts import (
    BucketName,
    ObjectKey,
    SanitizedImage,
    StorageConfig,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
)
from app.storage.image import (
    BoundedImageBytes,
    generate_object_key,
    sanitize_bounded_image,
)
from app.storage.s3 import S3ObjectStorageService, create_s3_client

MINIO_ENDPOINT = os.environ.get(
    "M8_MINIO_TEST_ENDPOINT",
    "http://127.0.0.1:9000",
)
MINIO_BUCKET = BucketName(os.environ.get("M8_MINIO_TEST_BUCKET", "nasiya-private"))
MINIO_APP_ACCESS_KEY = os.environ.get(
    "M8_MINIO_TEST_ACCESS_KEY",
    "local-nasiya-storage-app",
)
MINIO_APP_SECRET_KEY = os.environ.get(
    "M8_MINIO_TEST_SECRET_KEY",
    "change-me-local-nasiya-storage-app-secret-at-least-32-chars",
)


@dataclass(frozen=True, repr=False)
class StoredSyntheticImage:
    adapter: S3ObjectStorageService
    client: BaseClient
    key: ObjectKey
    image: SanitizedImage

    def __repr__(self) -> str:
        return (
            "StoredSyntheticImage("
            "adapter=<redacted>, client=<redacted>, key=<redacted>, "
            "image=<redacted>"
            ")"
        )


def _config(
    *,
    access_key: str = MINIO_APP_ACCESS_KEY,
    secret_key: str = MINIO_APP_SECRET_KEY,
) -> StorageConfig:
    return StorageConfig(
        endpoint_url=SecretStr(MINIO_ENDPOINT),
        region="us-east-1",
        bucket=MINIO_BUCKET.as_internal_value(),
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


def _synthetic_image() -> SanitizedImage:
    source = BytesIO()
    with Image.new("RGBA", (3, 2), (21, 89, 144, 173)) as image:
        image.save(source, format="PNG", pnginfo=None, optimize=False)
    return sanitize_bounded_image(BoundedImageBytes(source.getvalue()))


@pytest.fixture
def minio_adapter() -> S3ObjectStorageService:
    return S3ObjectStorageService(create_s3_client(_config()))


@pytest.fixture
def stored_image(
    minio_adapter: S3ObjectStorageService,
) -> Generator[StoredSyntheticImage, None, None]:
    image = _synthetic_image()
    key = generate_object_key(image.metadata.canonical_extension)
    minio_adapter.put_object(
        bucket=MINIO_BUCKET,
        key=key,
        image=image,
    )
    context = StoredSyntheticImage(
        adapter=minio_adapter,
        client=minio_adapter._client,
        key=key,
        image=image,
    )
    try:
        yield context
    finally:
        minio_adapter.delete_object(bucket=MINIO_BUCKET, key=key)


def _anonymous_url(key: ObjectKey) -> str:
    encoded_key = quote(key.as_internal_value(), safe="/")
    return f"{MINIO_ENDPOINT}/{MINIO_BUCKET.as_internal_value()}/{encoded_key}"


def _assert_access_denied(operation) -> None:
    with pytest.raises(ClientError) as exc_info:
        operation()
    status = exc_info.value.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    assert status == 403


@pytest.mark.integration
def test_minio_01_app_credentials_connect_to_scoped_bucket(
    minio_adapter: S3ObjectStorageService,
) -> None:
    response = minio_adapter._client.head_bucket(
        Bucket=MINIO_BUCKET.as_internal_value()
    )
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200


@pytest.mark.integration
def test_minio_02_put_accepts_exact_synthetic_image(
    stored_image: StoredSyntheticImage,
) -> None:
    assert stored_image.image.metadata.size_bytes > 0
    assert stored_image.image.metadata.content_type == "image/png"


@pytest.mark.integration
def test_minio_03_head_returns_exact_content_metadata(
    stored_image: StoredSyntheticImage,
) -> None:
    head = stored_image.adapter.head_object(
        bucket=MINIO_BUCKET,
        key=stored_image.key,
    )
    assert head is not None
    assert head.size_bytes == stored_image.image.metadata.size_bytes
    assert head.content_type == stored_image.image.metadata.content_type


@pytest.mark.integration
def test_minio_04_head_returns_exact_checksum_metadata(
    stored_image: StoredSyntheticImage,
) -> None:
    head = stored_image.adapter.head_object(
        bucket=MINIO_BUCKET,
        key=stored_image.key,
    )
    assert head is not None
    assert head.checksum_sha256 == stored_image.image.metadata.checksum_sha256


@pytest.mark.integration
def test_minio_05_anonymous_get_is_denied(
    stored_image: StoredSyntheticImage,
) -> None:
    with httpx.Client(timeout=5.0) as client:
        response = client.get(_anonymous_url(stored_image.key))
    assert response.status_code == 403


@pytest.mark.integration
def test_minio_06_anonymous_head_is_denied(
    stored_image: StoredSyntheticImage,
) -> None:
    with httpx.Client(timeout=5.0) as client:
        response = client.head(_anonymous_url(stored_image.key))
    assert response.status_code == 403


@pytest.mark.integration
def test_minio_07_presigned_get_returns_exact_sanitized_bytes(
    stored_image: StoredSyntheticImage,
) -> None:
    url = stored_image.adapter.create_presigned_get_url(
        bucket=MINIO_BUCKET,
        key=stored_image.key,
        ttl_seconds=60,
    )
    with httpx.Client(timeout=5.0) as client:
        response = client.get(url.as_response_value())
    assert response.status_code == 200
    assert response.content == (stored_image.image.sanitized_bytes.as_internal_bytes())


@pytest.mark.integration
def test_minio_08_presigned_get_uses_exact_ttl(
    stored_image: StoredSyntheticImage,
) -> None:
    url = stored_image.adapter.create_presigned_get_url(
        bucket=MINIO_BUCKET,
        key=stored_image.key,
        ttl_seconds=60,
    )
    query = parse_qs(urlsplit(url.as_response_value()).query)
    assert query.get("X-Amz-Expires") == ["60"]


@pytest.mark.integration
def test_minio_09_has_no_presigned_put_path() -> None:
    methods = {
        name
        for name, value in inspect.getmembers(
            storage_s3.S3ObjectStorageService,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }
    assert "create_presigned_put_url" not in methods
    assert "create_presigned_post" not in methods


@pytest.mark.integration
def test_minio_10_delete_removes_present_object(
    stored_image: StoredSyntheticImage,
) -> None:
    assert (
        stored_image.adapter.delete_object(
            bucket=MINIO_BUCKET,
            key=stored_image.key,
        )
        is StorageProviderOperationResult.SUCCESS
    )
    assert (
        stored_image.adapter.head_object(
            bucket=MINIO_BUCKET,
            key=stored_image.key,
        )
        is None
    )


@pytest.mark.integration
def test_minio_11_delete_is_idempotent_for_missing_object(
    minio_adapter: S3ObjectStorageService,
) -> None:
    missing_key = generate_object_key("png")
    assert (
        minio_adapter.delete_object(
            bucket=MINIO_BUCKET,
            key=missing_key,
        )
        is StorageProviderOperationResult.SUCCESS
    )
    assert (
        minio_adapter.head_object(
            bucket=MINIO_BUCKET,
            key=missing_key,
        )
        is None
    )


@pytest.mark.integration
def test_minio_12_wrong_secret_is_sanitized() -> None:
    adapter = S3ObjectStorageService(
        create_s3_client(_config(secret_key="synthetic-wrong-secret"))
    )
    with pytest.raises(StorageProviderError) as exc_info:
        adapter.head_object(
            bucket=MINIO_BUCKET,
            key=generate_object_key("png"),
        )
    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE
    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert "synthetic-wrong-secret" not in rendered
    assert MINIO_ENDPOINT not in rendered


@pytest.mark.integration
def test_minio_13_wrong_access_key_is_sanitized() -> None:
    adapter = S3ObjectStorageService(
        create_s3_client(_config(access_key="synthetic-wrong-access"))
    )
    with pytest.raises(StorageProviderError) as exc_info:
        adapter.head_object(
            bucket=MINIO_BUCKET,
            key=generate_object_key("png"),
        )
    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE
    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert "synthetic-wrong-access" not in rendered
    assert MINIO_ENDPOINT not in rendered


@pytest.mark.integration
def test_minio_14_app_user_cannot_read_bucket_policy(
    minio_adapter: S3ObjectStorageService,
) -> None:
    _assert_access_denied(
        lambda: minio_adapter._client.get_bucket_policy(
            Bucket=MINIO_BUCKET.as_internal_value()
        )
    )


@pytest.mark.integration
def test_minio_15_app_user_cannot_create_bucket(
    minio_adapter: S3ObjectStorageService,
) -> None:
    _assert_access_denied(
        lambda: minio_adapter._client.create_bucket(
            Bucket="nasiya-forbidden-admin-test"
        )
    )


@pytest.mark.integration
def test_minio_16_cleanup_preserves_private_bucket_and_volume(
    minio_adapter: S3ObjectStorageService,
) -> None:
    image = _synthetic_image()
    key = generate_object_key("png")
    minio_adapter.put_object(bucket=MINIO_BUCKET, key=key, image=image)
    minio_adapter.delete_object(bucket=MINIO_BUCKET, key=key)

    assert minio_adapter.head_object(bucket=MINIO_BUCKET, key=key) is None
    response = minio_adapter._client.head_bucket(
        Bucket=MINIO_BUCKET.as_internal_value()
    )
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
