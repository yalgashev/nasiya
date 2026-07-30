"""Private S3-compatible client construction."""

from enum import StrEnum
from typing import Final, Never

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    NoCredentialsError,
    ParamValidationError,
    PartialCredentialsError,
    ProxyConnectionError,
    ReadTimeoutError,
    SSLError,
)

from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    PresignedObjectUrl,
    SanitizedImage,
    StorageConfig,
    StorageProviderError,
    StorageProviderFailureKind,
    StorageProviderOperationResult,
    StoredObjectHead,
)
from app.storage.errors import StorageInternalCode

S3_MAX_PUT_BYTES: Final = 10_485_760
S3_CONNECT_TIMEOUT_SECONDS: Final = 3
S3_READ_TIMEOUT_SECONDS: Final = 10
S3_MAX_POOL_CONNECTIONS: Final = 10
S3_USER_AGENT_EXTRA: Final = "nasiya-m8-storage/1"
S3_TOTAL_MAX_ATTEMPTS: Final = 1


class _S3Operation(StrEnum):
    PUT = "PUT"
    HEAD = "HEAD"
    DELETE = "DELETE"
    PRESIGN_GET = "PRESIGN_GET"
    ENSURE_BUCKET = "ENSURE_BUCKET"


_WRITE_OPERATIONS = frozenset(
    {
        _S3Operation.PUT,
        _S3Operation.DELETE,
        _S3Operation.ENSURE_BUCKET,
    }
)
_DEFINITE_LOCAL_FAILURES = (
    ParamValidationError,
    NoCredentialsError,
    PartialCredentialsError,
    EndpointConnectionError,
    ConnectTimeoutError,
    ProxyConnectionError,
    SSLError,
)
_AMBIGUOUS_HTTP_FAILURES = (
    ReadTimeoutError,
    ConnectionClosedError,
)
_PUBLIC_ACCESS_BLOCK = {
    "BlockPublicAcls": True,
    "IgnorePublicAcls": True,
    "BlockPublicPolicy": True,
    "RestrictPublicBuckets": True,
}
_PUBLIC_ACCESS_BLOCK_UNSUPPORTED_CODES = frozenset(
    {
        "MethodNotAllowed",
        "NotImplemented",
        "NotImplementedException",
        "XNotImplemented",
    }
)


def create_s3_client(config: StorageConfig) -> BaseClient:
    """Construct an explicit, no-retry S3 client without making a request."""
    if not isinstance(config, StorageConfig):
        raise StorageProviderError(
            kind=StorageProviderFailureKind.DEFINITE,
            code=StorageInternalCode.STORAGE_CONFIGURATION_UNAVAILABLE,
        )

    client: BaseClient | None = None
    configuration_failed = False
    try:
        client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url.get_secret_value(),
            region_name=config.region,
            aws_access_key_id=config.access_key.get_secret_value(),
            aws_secret_access_key=config.secret_key.get_secret_value(),
            use_ssl=config.use_ssl,
            config=Config(
                signature_version="s3v4",
                connect_timeout=S3_CONNECT_TIMEOUT_SECONDS,
                read_timeout=S3_READ_TIMEOUT_SECONDS,
                max_pool_connections=S3_MAX_POOL_CONNECTIONS,
                retries={
                    "total_max_attempts": S3_TOTAL_MAX_ATTEMPTS,
                    "mode": "standard",
                },
                s3={"addressing_style": config.addressing_style},
                user_agent_extra=S3_USER_AGENT_EXTRA,
            ),
        )
    except (BotoCoreError, ClientError, ValueError):
        configuration_failed = True
    if configuration_failed or client is None:
        raise StorageProviderError(
            kind=StorageProviderFailureKind.DEFINITE,
            code=StorageInternalCode.STORAGE_CONFIGURATION_UNAVAILABLE,
        ) from None
    return client


class S3ObjectStorageService:
    """Narrow injected adapter for private object operations."""

    def __init__(self, client: BaseClient) -> None:
        self._client = client

    def __repr__(self) -> str:
        return "S3ObjectStorageService(client=<redacted>)"

    def put_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
        image: SanitizedImage,
    ) -> StorageProviderOperationResult:
        if (
            not isinstance(image, SanitizedImage)
            or image.metadata.size_bytes < 1
            or image.metadata.size_bytes > S3_MAX_PUT_BYTES
        ):
            _raise_provider_error(StorageProviderFailureKind.DEFINITE)
        failure_kind: StorageProviderFailureKind | None = None
        try:
            self._client.put_object(
                Bucket=bucket.as_internal_value(),
                Key=key.as_internal_value(),
                Body=image.sanitized_bytes.as_internal_bytes(),
                ContentLength=image.metadata.size_bytes,
                ContentType=image.metadata.content_type,
                Metadata={
                    "checksum-sha256": (
                        image.metadata.checksum_sha256.as_internal_value()
                    )
                },
            )
        except (ClientError, BotoCoreError) as exc:
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.PUT)
        if failure_kind is not None:
            _raise_provider_error(failure_kind)
        return StorageProviderOperationResult.SUCCESS

    def head_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
    ) -> StoredObjectHead | None:
        response: dict[str, object] | None = None
        failure_kind: StorageProviderFailureKind | None = None
        try:
            response = self._client.head_object(
                Bucket=bucket.as_internal_value(),
                Key=key.as_internal_value(),
            )
        except ClientError as exc:
            if _client_error_status(exc) == 404:
                return None
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        except BotoCoreError as exc:
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        if failure_kind is not None or response is None:
            _raise_provider_error(failure_kind or StorageProviderFailureKind.DEFINITE)

        try:
            size_bytes = response["ContentLength"]
            content_type = response["ContentType"]
            metadata = response["Metadata"]
            checksum = metadata["checksum-sha256"]
            if (
                isinstance(size_bytes, bool)
                or not isinstance(size_bytes, int)
                or not isinstance(content_type, str)
                or not isinstance(metadata, dict)
                or set(metadata) != {"checksum-sha256"}
                or not isinstance(checksum, str)
            ):
                raise ValueError
            return StoredObjectHead(
                size_bytes=size_bytes,
                content_type=content_type,
                checksum_sha256=ObjectChecksumSha256(checksum),
            )
        except (KeyError, TypeError, ValueError):
            _raise_provider_error(StorageProviderFailureKind.DEFINITE)

    def delete_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
    ) -> StorageProviderOperationResult:
        failure_kind: StorageProviderFailureKind | None = None
        try:
            self._client.delete_object(
                Bucket=bucket.as_internal_value(),
                Key=key.as_internal_value(),
            )
        except ClientError as exc:
            if _client_error_status(exc) == 404:
                return StorageProviderOperationResult.SUCCESS
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.DELETE)
        except BotoCoreError as exc:
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.DELETE)
        if failure_kind is not None:
            _raise_provider_error(failure_kind)
        return StorageProviderOperationResult.SUCCESS

    def create_presigned_get_url(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
        ttl_seconds: int,
    ) -> PresignedObjectUrl:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or ttl_seconds < 60
            or ttl_seconds > 900
        ):
            raise ValueError("Presigned URL TTL must be between 60 and 900 seconds")
        raw_url: object = None
        failure_kind: StorageProviderFailureKind | None = None
        try:
            raw_url = self._client.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": bucket.as_internal_value(),
                    "Key": key.as_internal_value(),
                },
                ExpiresIn=ttl_seconds,
                HttpMethod="GET",
            )
        except (ClientError, BotoCoreError) as exc:
            failure_kind = _sdk_failure_kind(
                exc,
                operation=_S3Operation.PRESIGN_GET,
            )
        if failure_kind is not None:
            _raise_provider_error(failure_kind)
        if not isinstance(raw_url, str):
            _raise_provider_error(StorageProviderFailureKind.DEFINITE)
        try:
            return PresignedObjectUrl(raw_url)
        except ValueError:
            pass
        _raise_provider_error(StorageProviderFailureKind.DEFINITE)

    def ensure_private_bucket(
        self,
        *,
        bucket: BucketName,
    ) -> StorageProviderOperationResult:
        bucket_name = bucket.as_internal_value()
        if not self._bucket_exists(bucket_name):
            self._create_bucket(bucket_name)

        self._verify_private_acl(bucket_name)
        self._verify_no_bucket_policy(bucket_name)
        if self._put_public_access_block(bucket_name):
            self._verify_public_access_block(bucket_name)
        return StorageProviderOperationResult.SUCCESS

    def _bucket_exists(self, bucket_name: str) -> bool:
        failure_kind: StorageProviderFailureKind | None = None
        try:
            self._client.head_bucket(Bucket=bucket_name)
        except ClientError as exc:
            if _client_error_status(exc) == 404:
                return False
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        except BotoCoreError as exc:
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        if failure_kind is not None:
            _raise_provider_error(failure_kind)
        return True

    def _create_bucket(self, bucket_name: str) -> None:
        region_name = self._client.meta.region_name
        if not isinstance(region_name, str) or not region_name:
            _raise_provider_error(StorageProviderFailureKind.DEFINITE)
        params: dict[str, object] = {"Bucket": bucket_name}
        if region_name != "us-east-1":
            params["CreateBucketConfiguration"] = {
                "LocationConstraint": region_name,
            }
        failure_kind: StorageProviderFailureKind | None = None
        try:
            self._client.create_bucket(**params)
        except ClientError as exc:
            if _client_error_code(exc) == "BucketAlreadyOwnedByYou":
                return
            failure_kind = _sdk_failure_kind(
                exc,
                operation=_S3Operation.ENSURE_BUCKET,
            )
        except BotoCoreError as exc:
            failure_kind = _sdk_failure_kind(
                exc,
                operation=_S3Operation.ENSURE_BUCKET,
            )
        if failure_kind is not None:
            _raise_provider_error(failure_kind)

    def _verify_private_acl(self, bucket_name: str) -> None:
        response: dict[str, object] | None = None
        failure_kind: StorageProviderFailureKind | None = None
        try:
            response = self._client.get_bucket_acl(Bucket=bucket_name)
        except (ClientError, BotoCoreError) as exc:
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        if failure_kind is not None or response is None:
            _raise_provider_error(failure_kind or StorageProviderFailureKind.DEFINITE)
        if not _is_private_owner_only_acl(response):
            _raise_provider_error(StorageProviderFailureKind.DEFINITE)

    def _verify_no_bucket_policy(self, bucket_name: str) -> None:
        failure_kind: StorageProviderFailureKind | None = None
        try:
            self._client.get_bucket_policy(Bucket=bucket_name)
        except ClientError as exc:
            if (
                _client_error_status(exc) == 404
                and _client_error_code(exc) == "NoSuchBucketPolicy"
            ):
                return
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        except BotoCoreError as exc:
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        _raise_provider_error(failure_kind or StorageProviderFailureKind.DEFINITE)

    def _put_public_access_block(self, bucket_name: str) -> bool:
        failure_kind: StorageProviderFailureKind | None = None
        try:
            self._client.put_public_access_block(
                Bucket=bucket_name,
                PublicAccessBlockConfiguration=_PUBLIC_ACCESS_BLOCK,
            )
        except ClientError as exc:
            if _public_access_block_is_unsupported(exc):
                return False
            failure_kind = _sdk_failure_kind(
                exc,
                operation=_S3Operation.ENSURE_BUCKET,
            )
        except BotoCoreError as exc:
            failure_kind = _sdk_failure_kind(
                exc,
                operation=_S3Operation.ENSURE_BUCKET,
            )
        if failure_kind is not None:
            _raise_provider_error(failure_kind)
        return True

    def _verify_public_access_block(self, bucket_name: str) -> None:
        response: dict[str, object] | None = None
        failure_kind: StorageProviderFailureKind | None = None
        try:
            response = self._client.get_public_access_block(Bucket=bucket_name)
        except (ClientError, BotoCoreError) as exc:
            failure_kind = _sdk_failure_kind(exc, operation=_S3Operation.HEAD)
        if failure_kind is not None or response is None:
            _raise_provider_error(failure_kind or StorageProviderFailureKind.DEFINITE)
        if response.get("PublicAccessBlockConfiguration") != _PUBLIC_ACCESS_BLOCK:
            _raise_provider_error(StorageProviderFailureKind.DEFINITE)


def _sdk_failure_kind(
    exc: ClientError | BotoCoreError,
    *,
    operation: _S3Operation,
) -> StorageProviderFailureKind:
    if isinstance(exc, ClientError):
        status = _client_error_status(exc)
        ambiguous = status in {408, 429} or (
            status is not None and 500 <= status <= 599
        )
        return (
            StorageProviderFailureKind.AMBIGUOUS
            if operation in _WRITE_OPERATIONS and ambiguous
            else StorageProviderFailureKind.DEFINITE
        )

    if isinstance(exc, _DEFINITE_LOCAL_FAILURES):
        return StorageProviderFailureKind.DEFINITE
    if isinstance(exc, _AMBIGUOUS_HTTP_FAILURES):
        return (
            StorageProviderFailureKind.AMBIGUOUS
            if operation in _WRITE_OPERATIONS
            else StorageProviderFailureKind.DEFINITE
        )
    if operation in _WRITE_OPERATIONS and isinstance(
        exc,
        (HTTPClientError, BotoCoreError),
    ):
        return StorageProviderFailureKind.AMBIGUOUS
    return StorageProviderFailureKind.DEFINITE


def _client_error_status(exc: ClientError) -> int | None:
    response = exc.response
    if not isinstance(response, dict):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    status = metadata.get("HTTPStatusCode")
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def _client_error_code(exc: ClientError) -> str | None:
    response = exc.response
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    if not isinstance(error, dict):
        return None
    code = error.get("Code")
    if not isinstance(code, str):
        return None
    return code


def _public_access_block_is_unsupported(exc: ClientError) -> bool:
    status = _client_error_status(exc)
    code = _client_error_code(exc)
    return status in {405, 501} and code in _PUBLIC_ACCESS_BLOCK_UNSUPPORTED_CODES


def _is_private_owner_only_acl(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    owner = response.get("Owner")
    grants = response.get("Grants")
    if not isinstance(owner, dict) or not isinstance(grants, list):
        return False
    owner_id = owner.get("ID")
    if not isinstance(owner_id, str) or not owner_id or not grants:
        return False
    for grant in grants:
        if not isinstance(grant, dict):
            return False
        grantee = grant.get("Grantee")
        if (
            not isinstance(grantee, dict)
            or grantee.get("Type") != "CanonicalUser"
            or grantee.get("ID") != owner_id
            or grant.get("Permission") != "FULL_CONTROL"
        ):
            return False
    return True


def _raise_provider_error(kind: StorageProviderFailureKind) -> Never:
    raise StorageProviderError(
        kind=kind,
        code=StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
    ) from None
