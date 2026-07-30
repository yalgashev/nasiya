import inspect

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from pydantic import SecretStr

import app.storage.s3 as storage_s3
from app.storage.contracts import (
    BucketName,
    ObjectStorageService,
    StorageConfig,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
)
from app.storage.s3 import S3ObjectStorageService, create_s3_client
from tests.storage_fake import FakeObjectStorageService

BUCKET = BucketName("nasiya-private-test")
BUCKET_PARAM = {"Bucket": "nasiya-private-test"}
RAW_ENDPOINT = "http://127.0.0.1:19000"
RAW_ACCESS_KEY = "synthetic-app-access"
RAW_SECRET_KEY = "synthetic-app-secret"
ADMIN_OPERATIONS = (
    "create_bucket",
    "get_bucket_acl",
    "get_bucket_policy",
    "put_public_access_block",
    "get_public_access_block",
)


def _adapter_with_stubber() -> tuple[S3ObjectStorageService, Stubber]:
    config = StorageConfig(
        endpoint_url=SecretStr(RAW_ENDPOINT),
        region="us-east-1",
        bucket=BUCKET.as_internal_value(),
        access_key=SecretStr(RAW_ACCESS_KEY),
        secret_key=SecretStr(RAW_SECRET_KEY),
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
    client = create_s3_client(config)
    return S3ObjectStorageService(client), Stubber(client)


def test_bucket_access_check_uses_exactly_one_head_bucket_call() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response("head_bucket", {}, expected_params=BUCKET_PARAM)

    with stubber:
        result = adapter.check_bucket_access(bucket=BUCKET)

    assert result is StorageProviderOperationResult.SUCCESS
    assert isinstance(adapter, ObjectStorageService)
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("service_error_code", "http_status"),
    (
        ("AccessDenied", 403),
        ("NoSuchBucket", 404),
        ("InternalError", 500),
    ),
)
def test_bucket_access_failure_is_definite_sanitized_and_never_provisions(
    service_error_code: str,
    http_status: int,
) -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_client_error(
        "head_bucket",
        service_error_code=service_error_code,
        service_message="sensitive provider detail",
        http_status_code=http_status,
        expected_params=BUCKET_PARAM,
    )

    with stubber:
        with pytest.raises(StorageProviderError) as exc_info:
            adapter.check_bucket_access(bucket=BUCKET)

    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE
    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    for hidden_value in (
        "sensitive provider detail",
        service_error_code,
        RAW_ENDPOINT,
        BUCKET.as_internal_value(),
        RAW_ACCESS_KEY,
        RAW_SECRET_KEY,
    ):
        assert hidden_value not in rendered
    stubber.assert_no_pending_responses()


def test_admin_denial_does_not_break_app_bucket_access_contract() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_client_error(
        "create_bucket",
        service_error_code="AccessDenied",
        service_message="synthetic admin denial",
        http_status_code=403,
        expected_params={"Bucket": "nasiya-forbidden-admin-test"},
    )
    stubber.add_response("head_bucket", {}, expected_params=BUCKET_PARAM)

    with stubber:
        with pytest.raises(ClientError):
            adapter._client.create_bucket(Bucket="nasiya-forbidden-admin-test")
        result = adapter.check_bucket_access(bucket=BUCKET)

    assert result is StorageProviderOperationResult.SUCCESS
    stubber.assert_no_pending_responses()


def test_runtime_adapter_and_protocol_exclude_provisioning_operations() -> None:
    adapter_source = inspect.getsource(storage_s3.S3ObjectStorageService)
    protocol_source = inspect.getsource(ObjectStorageService)

    for administrative_operation in ADMIN_OPERATIONS:
        assert f"{administrative_operation}(" not in adapter_source
        assert administrative_operation not in protocol_source
    assert "ensure_private_bucket" not in adapter_source
    assert "ensure_private_bucket" not in protocol_source
    assert "MINIO_ROOT" not in adapter_source
    assert "root_password" not in adapter_source
    assert "logger" not in adapter_source

    fake = FakeObjectStorageService()
    assert (
        fake.check_bucket_access(bucket=BUCKET)
        is StorageProviderOperationResult.SUCCESS
    )
    assert isinstance(fake, ObjectStorageService)
