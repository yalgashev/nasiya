import inspect
import socket

import pytest
from botocore.client import BaseClient
from botocore.exceptions import ParamValidationError
from botocore.stub import Stubber
from pydantic import SecretStr

import app.storage.s3 as storage_s3
from app.storage.contracts import (
    StorageConfig,
    StorageProviderError,
    StorageProviderFailureKind,
)
from app.storage.errors import StorageInternalCode
from app.storage.s3 import (
    S3_CONNECT_TIMEOUT_SECONDS,
    S3_MAX_POOL_CONNECTIONS,
    S3_READ_TIMEOUT_SECONDS,
    S3_TOTAL_MAX_ATTEMPTS,
    S3_USER_AGENT_EXTRA,
    create_s3_client,
)

ENDPOINT = "http://127.0.0.1:19000"
ACCESS_KEY = "synthetic-m8-access"
SECRET_KEY = "synthetic-m8-secret"


def _config() -> StorageConfig:
    return StorageConfig(
        endpoint_url=SecretStr(ENDPOINT),
        region="us-east-1",
        bucket="nasiya-private-test",
        access_key=SecretStr(ACCESS_KEY),
        secret_key=SecretStr(SECRET_KEY),
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


def test_factory_passes_exact_explicit_client_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_client(service_name: str, **kwargs: object) -> object:
        captured["service_name"] = service_name
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(storage_s3.boto3, "client", fake_client)

    assert create_s3_client(_config()) is sentinel
    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == ENDPOINT
    assert captured["region_name"] == "us-east-1"
    assert captured["aws_access_key_id"] == ACCESS_KEY
    assert captured["aws_secret_access_key"] == SECRET_KEY
    assert captured["use_ssl"] is False
    assert "aws_session_token" not in captured
    assert "profile_name" not in captured

    sdk_config = captured["config"]
    assert sdk_config.signature_version == "s3v4"
    assert sdk_config.connect_timeout == S3_CONNECT_TIMEOUT_SECONDS == 3
    assert sdk_config.read_timeout == S3_READ_TIMEOUT_SECONDS == 10
    assert sdk_config.max_pool_connections == S3_MAX_POOL_CONNECTIONS == 10
    assert sdk_config.retries == {
        "total_max_attempts": S3_TOTAL_MAX_ATTEMPTS,
        "mode": "standard",
    }
    assert S3_TOTAL_MAX_ATTEMPTS == 1
    assert sdk_config.s3 == {"addressing_style": "path"}
    assert sdk_config.user_agent_extra == S3_USER_AGENT_EXTRA
    assert S3_USER_AGENT_EXTRA == "nasiya-m8-storage/1"


def test_real_factory_construction_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_calls = 0

    def forbidden_connect(*_args: object, **_kwargs: object) -> None:
        nonlocal connect_calls
        connect_calls += 1
        raise AssertionError("S3 client construction attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)

    client = create_s3_client(_config())

    assert isinstance(client, BaseClient)
    assert connect_calls == 0
    assert client.meta.config.signature_version == "s3v4"
    assert client.meta.config.retries["total_max_attempts"] == 1
    with Stubber(client):
        pass


def test_factory_error_is_closed_and_redacts_sdk_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_report = f"{ENDPOINT} {ACCESS_KEY} {SECRET_KEY}"

    def fail_client(*_args: object, **_kwargs: object) -> object:
        raise ParamValidationError(report=sensitive_report)

    monkeypatch.setattr(storage_s3.boto3, "client", fail_client)

    with pytest.raises(StorageProviderError) as exc_info:
        create_s3_client(_config())

    error = exc_info.value
    assert error.kind is StorageProviderFailureKind.DEFINITE
    assert error.code is StorageInternalCode.STORAGE_CONFIGURATION_UNAVAILABLE
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = f"{error!s} {error!r}"
    assert ENDPOINT not in rendered
    assert ACCESS_KEY not in rendered
    assert SECRET_KEY not in rendered
    assert sensitive_report not in rendered


def test_factory_requires_typed_complete_storage_config() -> None:
    with pytest.raises(StorageProviderError) as exc_info:
        create_s3_client(None)  # type: ignore[arg-type]

    assert exc_info.value.kind is StorageProviderFailureKind.DEFINITE
    assert exc_info.value.code is StorageInternalCode.STORAGE_CONFIGURATION_UNAVAILABLE


def test_factory_source_has_no_logging_or_default_credential_fallback() -> None:
    source = inspect.getsource(storage_s3)

    assert "logger" not in source
    assert "logging" not in source
    assert "print(" not in source
    assert "Session(" not in source
    assert "profile_name" not in source
    assert "AWS_" not in source
    assert "metadata_service" not in source
    assert "get_available_regions" not in source
    assert "total_max_attempts" in source
    assert "get_secret_value()" in source
