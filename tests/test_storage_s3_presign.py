import inspect

import pytest
from botocore.exceptions import ParamValidationError
from pydantic import SecretStr

import app.storage.s3 as storage_s3
from app.storage.contracts import (
    BucketName,
    ObjectKey,
    StorageConfig,
    StorageProviderError,
    StorageProviderFailureKind,
)
from app.storage.s3 import S3ObjectStorageService, create_s3_client

BUCKET = BucketName("nasiya-private-test")
KEY = ObjectKey("v1/objects/0123456789abcdef0123456789abcdef.png")


def _adapter() -> S3ObjectStorageService:
    config = StorageConfig(
        endpoint_url=SecretStr("http://127.0.0.1:19000"),
        region="us-east-1",
        bucket="nasiya-private-test",
        access_key=SecretStr("synthetic-access"),
        secret_key=SecretStr("synthetic-secret"),
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
    return S3ObjectStorageService(create_s3_client(config))


def test_presign_calls_exact_get_only_api(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    captured: dict[str, object] = {}

    def fake_generate_presigned_url(**kwargs: object) -> str:
        captured.update(kwargs)
        return "https://storage.invalid/synthetic-presigned"

    monkeypatch.setattr(
        adapter._client,
        "generate_presigned_url",
        fake_generate_presigned_url,
    )

    url = adapter.create_presigned_get_url(
        bucket=BUCKET,
        key=KEY,
        ttl_seconds=300,
    )

    assert captured == {
        "ClientMethod": "get_object",
        "Params": {
            "Bucket": BUCKET.as_internal_value(),
            "Key": KEY.as_internal_value(),
        },
        "ExpiresIn": 300,
        "HttpMethod": "GET",
    }
    assert "synthetic-presigned" not in repr(url)


@pytest.mark.parametrize("ttl_seconds", (True, 0, 59, 901))
def test_presign_rejects_ttl_outside_exact_boundary(ttl_seconds: int) -> None:
    adapter = _adapter()

    with pytest.raises(ValueError, match="between 60 and 900"):
        adapter.create_presigned_get_url(
            bucket=BUCKET,
            key=KEY,
            ttl_seconds=ttl_seconds,
        )


def test_presign_sdk_failure_is_definite_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()

    def fail_presign(**_kwargs: object) -> str:
        raise ParamValidationError(report="sensitive endpoint key credential URL")

    monkeypatch.setattr(
        adapter._client,
        "generate_presigned_url",
        fail_presign,
    )

    with pytest.raises(StorageProviderError) as exc_info:
        adapter.create_presigned_get_url(
            bucket=BUCKET,
            key=KEY,
            ttl_seconds=300,
        )

    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE
    assert "sensitive" not in str(exc_info.value)
    assert "sensitive" not in repr(exc_info.value)


def test_s3_adapter_has_no_presigned_put_surface() -> None:
    source = inspect.getsource(storage_s3.S3ObjectStorageService)
    public_methods = {
        name
        for name, value in inspect.getmembers(
            storage_s3.S3ObjectStorageService,
            inspect.isfunction,
        )
        if not name.startswith("_")
    }

    assert "create_presigned_put_url" not in public_methods
    assert "presigned_post" not in source
    assert '"put_object"' not in inspect.getsource(
        storage_s3.S3ObjectStorageService.create_presigned_get_url
    )
