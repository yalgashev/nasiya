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
from app import cli
from app.settings import Settings
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
    verify_reopened_metadata_absence,
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


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=(
            "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test"
        ),
        session_cookie_secure=False,
        rate_limit_hmac_key="m8-minio-integration-rate-limit-key",
        object_storage_endpoint_url=MINIO_ENDPOINT,
        object_storage_region="us-east-1",
        object_storage_bucket=MINIO_BUCKET.as_internal_value(),
        object_storage_access_key=MINIO_APP_ACCESS_KEY,
        object_storage_secret_key=MINIO_APP_SECRET_KEY,
        object_storage_use_ssl=False,
        object_storage_addressing_style="path",
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


def _safe_http_request(method: str, url: str) -> httpx.Response:
    try:
        with httpx.Client(timeout=5.0) as client:
            return client.request(method, url)
    except httpx.HTTPError:
        raise AssertionError("storage acceptance request failed") from None


def _assert_access_denied(operation) -> None:
    with pytest.raises(ClientError) as exc_info:
        operation()
    status = exc_info.value.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    assert status == 403


@pytest.mark.integration
def test_minio_01_app_credentials_connect_to_scoped_bucket(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("MINIO_ROOT_USER", raising=False)
    monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)

    exit_code = cli.main(["storage", "preflight"], settings=_settings())

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == "STORAGE_PREFLIGHT_OK\n"
    assert captured.err == ""
    for hidden_value in (
        MINIO_ENDPOINT,
        MINIO_BUCKET.as_internal_value(),
        MINIO_APP_ACCESS_KEY,
        MINIO_APP_SECRET_KEY,
        "MinIO",
        "S3",
    ):
        assert hidden_value not in f"{captured.out}{captured.err}"


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
    response = _safe_http_request("GET", _anonymous_url(stored_image.key))
    assert response.status_code == 403


@pytest.mark.integration
def test_minio_06_anonymous_head_is_denied(
    stored_image: StoredSyntheticImage,
) -> None:
    response = _safe_http_request("HEAD", _anonymous_url(stored_image.key))
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
    response = _safe_http_request("GET", url.as_response_value())
    assert response.status_code == 200
    assert response.content == (stored_image.image.sanitized_bytes.as_internal_bytes())
    with Image.open(BytesIO(response.content)) as reopened:
        reopened.load()
        verify_reopened_metadata_absence(reopened)
        assert len(reopened.getexif()) == 0


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
def test_minio_14_app_user_cannot_inspect_bucket_admin_state(
    minio_adapter: S3ObjectStorageService,
) -> None:
    _assert_access_denied(
        lambda: minio_adapter._client.get_bucket_acl(
            Bucket=MINIO_BUCKET.as_internal_value()
        )
    )
    _assert_access_denied(
        lambda: minio_adapter._client.get_bucket_policy(
            Bucket=MINIO_BUCKET.as_internal_value()
        )
    )
    assert (
        minio_adapter.check_bucket_access(bucket=MINIO_BUCKET)
        is StorageProviderOperationResult.SUCCESS
    )


@pytest.mark.integration
def test_minio_15_admin_denial_does_not_break_app_data_plane(
    minio_adapter: S3ObjectStorageService,
) -> None:
    _assert_access_denied(
        lambda: minio_adapter._client.get_bucket_policy(
            Bucket=MINIO_BUCKET.as_internal_value()
        )
    )
    image = _synthetic_image()
    key = generate_object_key("png")
    try:
        assert (
            minio_adapter.put_object(
                bucket=MINIO_BUCKET,
                key=key,
                image=image,
            )
            is StorageProviderOperationResult.SUCCESS
        )
        head = minio_adapter.head_object(bucket=MINIO_BUCKET, key=key)
        assert head is not None
        assert head.size_bytes == image.metadata.size_bytes
        assert head.content_type == image.metadata.content_type
        assert head.checksum_sha256 == image.metadata.checksum_sha256
        url = minio_adapter.create_presigned_get_url(
            bucket=MINIO_BUCKET,
            key=key,
            ttl_seconds=60,
        )
        response = _safe_http_request("GET", url.as_response_value())
        assert response.status_code == 200
        assert response.content == image.sanitized_bytes.as_internal_bytes()
        assert (
            minio_adapter.delete_object(bucket=MINIO_BUCKET, key=key)
            is StorageProviderOperationResult.SUCCESS
        )
        assert minio_adapter.head_object(bucket=MINIO_BUCKET, key=key) is None
    finally:
        minio_adapter.delete_object(bucket=MINIO_BUCKET, key=key)


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
