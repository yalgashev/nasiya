import inspect

import pytest
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
PUBLIC_ACCESS_BLOCK = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}
PRIVATE_ACL = {
    "Owner": {"ID": "synthetic-owner"},
    "Grants": [
        {
            "Grantee": {
                "Type": "CanonicalUser",
                "ID": "synthetic-owner",
            },
            "Permission": "FULL_CONTROL",
        }
    ],
}


def _adapter_with_stubber(
    *,
    region: str = "us-east-1",
) -> tuple[S3ObjectStorageService, Stubber]:
    config = StorageConfig(
        endpoint_url=SecretStr("http://127.0.0.1:19000"),
        region=region,
        bucket="nasiya-private-test",
        access_key=SecretStr("synthetic-provision-access"),
        secret_key=SecretStr("synthetic-provision-secret"),
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


def _add_private_verification(
    stubber: Stubber,
    *,
    public_access_block_supported: bool = True,
) -> None:
    stubber.add_response(
        "get_bucket_acl",
        PRIVATE_ACL,
        expected_params=BUCKET_PARAM,
    )
    stubber.add_client_error(
        "get_bucket_policy",
        service_error_code="NoSuchBucketPolicy",
        service_message="synthetic absent policy",
        http_status_code=404,
        expected_params=BUCKET_PARAM,
    )
    if public_access_block_supported:
        stubber.add_response(
            "put_public_access_block",
            {},
            expected_params={
                **BUCKET_PARAM,
                "PublicAccessBlockConfiguration": PUBLIC_ACCESS_BLOCK,
            },
        )
        stubber.add_response(
            "get_public_access_block",
            {"PublicAccessBlockConfiguration": PUBLIC_ACCESS_BLOCK},
            expected_params=BUCKET_PARAM,
        )
    else:
        stubber.add_client_error(
            "put_public_access_block",
            service_error_code="NotImplemented",
            service_message="synthetic unsupported capability",
            http_status_code=501,
            expected_params={
                **BUCKET_PARAM,
                "PublicAccessBlockConfiguration": PUBLIC_ACCESS_BLOCK,
            },
        )


def test_existing_private_bucket_is_idempotently_verified() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response("head_bucket", {}, expected_params=BUCKET_PARAM)
    _add_private_verification(stubber)

    with stubber:
        result = adapter.ensure_private_bucket(bucket=BUCKET)

    assert result is StorageProviderOperationResult.SUCCESS
    assert isinstance(adapter, ObjectStorageService)
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("region", "create_params"),
    (
        ("us-east-1", BUCKET_PARAM),
        (
            "eu-west-1",
            {
                **BUCKET_PARAM,
                "CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"},
            },
        ),
    ),
)
def test_missing_bucket_is_created_once_then_verified(
    region: str,
    create_params: dict[str, object],
) -> None:
    adapter, stubber = _adapter_with_stubber(region=region)
    stubber.add_client_error(
        "head_bucket",
        service_error_code="404",
        service_message="synthetic missing",
        http_status_code=404,
        expected_params=BUCKET_PARAM,
    )
    stubber.add_response(
        "create_bucket",
        {"Location": "/nasiya-private-test"},
        expected_params=create_params,
    )
    _add_private_verification(stubber)

    with stubber:
        assert (
            adapter.ensure_private_bucket(bucket=BUCKET)
            is StorageProviderOperationResult.SUCCESS
        )
    stubber.assert_no_pending_responses()


def test_provider_without_public_block_uses_private_acl_policy_fallback() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response("head_bucket", {}, expected_params=BUCKET_PARAM)
    _add_private_verification(
        stubber,
        public_access_block_supported=False,
    )

    with stubber:
        assert (
            adapter.ensure_private_bucket(bucket=BUCKET)
            is StorageProviderOperationResult.SUCCESS
        )
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    "acl",
    (
        {
            "Owner": {"ID": "synthetic-owner"},
            "Grants": [
                {
                    "Grantee": {
                        "Type": "Group",
                        "URI": "http://acs.amazonaws.com/groups/global/AllUsers",
                    },
                    "Permission": "READ",
                }
            ],
        },
        {
            "Owner": {"ID": "synthetic-owner"},
            "Grants": [
                {
                    "Grantee": {
                        "Type": "CanonicalUser",
                        "ID": "foreign-owner",
                    },
                    "Permission": "FULL_CONTROL",
                }
            ],
        },
        {"Owner": {"ID": "synthetic-owner"}, "Grants": []},
    ),
)
def test_public_or_ownership_mismatched_acl_fails_closed(
    acl: dict[str, object],
) -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response("head_bucket", {}, expected_params=BUCKET_PARAM)
    stubber.add_response("get_bucket_acl", acl, expected_params=BUCKET_PARAM)

    with stubber:
        with pytest.raises(StorageProviderError) as exc_info:
            adapter.ensure_private_bucket(bucket=BUCKET)

    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE
    stubber.assert_no_pending_responses()


def test_any_existing_bucket_policy_fails_closed_without_mutation() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response("head_bucket", {}, expected_params=BUCKET_PARAM)
    stubber.add_response(
        "get_bucket_acl",
        PRIVATE_ACL,
        expected_params=BUCKET_PARAM,
    )
    stubber.add_response(
        "get_bucket_policy",
        {"Policy": '{"Statement":"synthetic-public-or-unknown"}'},
        expected_params=BUCKET_PARAM,
    )

    with stubber:
        with pytest.raises(StorageProviderError) as exc_info:
            adapter.ensure_private_bucket(bucket=BUCKET)

    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE
    stubber.assert_no_pending_responses()


def test_unverified_public_access_block_fails_closed() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_response("head_bucket", {}, expected_params=BUCKET_PARAM)
    stubber.add_response(
        "get_bucket_acl",
        PRIVATE_ACL,
        expected_params=BUCKET_PARAM,
    )
    stubber.add_client_error(
        "get_bucket_policy",
        service_error_code="NoSuchBucketPolicy",
        service_message="synthetic absent policy",
        http_status_code=404,
        expected_params=BUCKET_PARAM,
    )
    stubber.add_response(
        "put_public_access_block",
        {},
        expected_params={
            **BUCKET_PARAM,
            "PublicAccessBlockConfiguration": PUBLIC_ACCESS_BLOCK,
        },
    )
    stubber.add_response(
        "get_public_access_block",
        {
            "PublicAccessBlockConfiguration": {
                **PUBLIC_ACCESS_BLOCK,
                "RestrictPublicBuckets": False,
            }
        },
        expected_params=BUCKET_PARAM,
    )

    with stubber:
        with pytest.raises(StorageProviderError) as exc_info:
            adapter.ensure_private_bucket(bucket=BUCKET)

    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE


@pytest.mark.parametrize(
    ("service_error_code", "http_status"),
    (
        ("AccessDenied", 403),
        ("BucketAlreadyExists", 409),
    ),
)
def test_foreign_or_unowned_bucket_outcomes_fail_closed_and_redacted(
    service_error_code: str,
    http_status: int,
) -> None:
    adapter, stubber = _adapter_with_stubber()
    if service_error_code == "AccessDenied":
        stubber.add_client_error(
            "head_bucket",
            service_error_code=service_error_code,
            service_message="sensitive foreign owner detail",
            http_status_code=http_status,
            expected_params=BUCKET_PARAM,
        )
    else:
        stubber.add_client_error(
            "head_bucket",
            service_error_code="404",
            service_message="synthetic missing",
            http_status_code=404,
            expected_params=BUCKET_PARAM,
        )
        stubber.add_client_error(
            "create_bucket",
            service_error_code=service_error_code,
            service_message="sensitive foreign owner detail",
            http_status_code=http_status,
            expected_params=BUCKET_PARAM,
        )

    with stubber:
        with pytest.raises(StorageProviderError) as exc_info:
            adapter.ensure_private_bucket(bucket=BUCKET)

    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert "sensitive" not in rendered
    assert BUCKET.as_internal_value() not in rendered


def test_bucket_already_owned_race_continues_with_private_verification() -> None:
    adapter, stubber = _adapter_with_stubber()
    stubber.add_client_error(
        "head_bucket",
        service_error_code="404",
        service_message="synthetic missing",
        http_status_code=404,
        expected_params=BUCKET_PARAM,
    )
    stubber.add_client_error(
        "create_bucket",
        service_error_code="BucketAlreadyOwnedByYou",
        service_message="synthetic race",
        http_status_code=409,
        expected_params=BUCKET_PARAM,
    )
    _add_private_verification(stubber)

    with stubber:
        assert (
            adapter.ensure_private_bucket(bucket=BUCKET)
            is StorageProviderOperationResult.SUCCESS
        )


def test_fake_private_bucket_contract_and_production_containment() -> None:
    fake = FakeObjectStorageService()

    assert (
        fake.ensure_private_bucket(bucket=BUCKET)
        is StorageProviderOperationResult.SUCCESS
    )
    assert isinstance(fake, ObjectStorageService)

    source = inspect.getsource(storage_s3.S3ObjectStorageService)
    assert "delete_bucket(" not in source
    assert "delete_bucket_policy(" not in source
    assert "put_bucket_acl(" not in source
    assert "ACL=" not in source
    assert "MINIO_ROOT" not in source
    assert "root_password" not in source
    assert "logger" not in source
