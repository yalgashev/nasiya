import logging
import subprocess
import traceback
from pathlib import Path

import pytest
from botocore.stub import Stubber
from pydantic import SecretStr

from app.settings import Settings
from app.storage.contracts import (
    BucketName,
    ObjectKey,
    PresignedObjectUrl,
    StorageConfig,
    StorageProviderError,
)
from app.storage.errors import StorageInternalCode
from app.storage.s3 import S3ObjectStorageService, create_s3_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://m8-private-storage.invalid:9443"
ACCESS_KEY = "m8-access-never-render"
SECRET_KEY = "m8-secret-never-render"
BUCKET_VALUE = "m8-private-bucket"
OBJECT_KEY_VALUE = "v1/objects/0123456789abcdef0123456789abcdef.webp"
PRESIGNED_URL_VALUE = (
    f"{ENDPOINT}/{BUCKET_VALUE}/{OBJECT_KEY_VALUE}"
    f"?X-Amz-Credential={ACCESS_KEY}&X-Amz-Signature={SECRET_KEY}"
)
SENSITIVE_VALUES = (
    ENDPOINT,
    ACCESS_KEY,
    SECRET_KEY,
    BUCKET_VALUE,
    OBJECT_KEY_VALUE,
    PRESIGNED_URL_VALUE,
    "X-Amz-Signature",
)


def _storage_config() -> StorageConfig:
    return StorageConfig(
        endpoint_url=SecretStr(ENDPOINT),
        region="region-1",
        bucket=BUCKET_VALUE,
        access_key=SecretStr(ACCESS_KEY),
        secret_key=SecretStr(SECRET_KEY),
        use_ssl=True,
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


def _assert_sensitive_values_absent(rendered: str) -> None:
    for value in SENSITIVE_VALUES:
        assert value not in rendered


def test_settings_client_and_typed_value_repr_are_redacted() -> None:
    settings = Settings(
        _env_file=None,
        debug=False,
        database_url="postgresql+psycopg://nasiya:pass@127.0.0.1/nasiya_test",
        session_cookie_secure=False,
        rate_limit_hmac_key="m8-rate-limit-key-at-least-32-characters",
        object_storage_endpoint_url=ENDPOINT,
        object_storage_region="region-1",
        object_storage_bucket=BUCKET_VALUE,
        object_storage_access_key=ACCESS_KEY,
        object_storage_secret_key=SECRET_KEY,
        object_storage_use_ssl=True,
    )
    config = settings.require_object_storage_config()
    client = create_s3_client(config)
    adapter = S3ObjectStorageService(client)
    values = (
        BucketName(BUCKET_VALUE),
        ObjectKey(OBJECT_KEY_VALUE),
        PresignedObjectUrl(PRESIGNED_URL_VALUE),
    )

    rendered = " ".join(
        (
            repr(settings),
            repr(config),
            repr(client),
            repr(adapter),
            repr(values),
            str(values),
        )
    )

    _assert_sensitive_values_absent(rendered)


def test_sdk_provider_detail_has_no_exception_chain_or_log_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = create_s3_client(_storage_config())
    adapter = S3ObjectStorageService(client)
    stubber = Stubber(client)
    stubber.add_client_error(
        "head_object",
        service_error_code=ACCESS_KEY,
        service_message=(
            f"{ENDPOINT} {SECRET_KEY} {OBJECT_KEY_VALUE} {PRESIGNED_URL_VALUE}"
        ),
        http_status_code=403,
        expected_params={
            "Bucket": BUCKET_VALUE,
            "Key": OBJECT_KEY_VALUE,
        },
    )

    with stubber:
        with pytest.raises(StorageProviderError) as exc_info:
            adapter.head_object(
                bucket=BucketName(BUCKET_VALUE),
                key=ObjectKey(OBJECT_KEY_VALUE),
            )

    error = exc_info.value
    with caplog.at_level(logging.ERROR, logger="tests.storage.s3.audit"):
        logging.getLogger("tests.storage.s3.audit").error(
            "storage provider failure: %r",
            error,
        )

    assert error.code is StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = " ".join(
        (
            str(error),
            repr(error),
            "".join(traceback.format_exception(error)),
            caplog.text,
        )
    )
    _assert_sensitive_values_absent(rendered)


@pytest.mark.parametrize(
    ("anonymous_status", "expected_return_code", "expected_stderr"),
    (
        ("private", 0, ""),
        ("public", 1, "minio-init failed\n"),
    ),
)
def test_minio_init_discards_command_detail_and_emits_only_safe_failure(
    tmp_path: Path,
    anonymous_status: str,
    expected_return_code: int,
    expected_stderr: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mc = fake_bin / "mc"
    fake_mc.write_text(
        (
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\"\n"
            "printf '%s\\n' \"$*\" >&2\n"
            'case "$*" in\n'
            '  "anonymous get "*) '
            "printf '%s\\n' "
            f"'Access permission is `{anonymous_status}`' ;;\n"
            "esac\n"
        ),
        encoding="utf-8",
    )
    fake_mc.chmod(0o700)
    result = subprocess.run(
        ["/bin/sh", str(PROJECT_ROOT / "deploy" / "minio-init.sh")],
        capture_output=True,
        check=False,
        env={
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "MINIO_ENDPOINT": ENDPOINT,
            "MINIO_ROOT_USER": "m8-root-never-render",
            "MINIO_ROOT_PASSWORD": "m8-root-secret-never-render",
            "MINIO_BUCKET": BUCKET_VALUE,
            "MINIO_APP_ACCESS_KEY": ACCESS_KEY,
            "MINIO_APP_SECRET_KEY": SECRET_KEY,
            "MINIO_APP_POLICY_NAME": "m8-private-policy",
        },
        text=True,
    )

    assert result.returncode == expected_return_code
    assert result.stdout == ""
    assert result.stderr == expected_stderr
    _assert_sensitive_values_absent(f"{result.stdout} {result.stderr}")


def test_compose_init_and_ci_have_no_sensitive_output_mode() -> None:
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")
    init = (PROJECT_ROOT / "deploy" / "minio-init.sh").read_text(encoding="utf-8")
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "docker compose config" not in workflow
    assert '"$@" >/dev/null 2>&1' in init
    assert "set -x" not in f"{compose}\n{init}\n{workflow}"
    assert "printenv" not in f"{compose}\n{init}\n{workflow}"
    assert "docker logs" not in workflow
    assert "curl -v" not in workflow
    assert "pytest -s" not in workflow
    assert 'echo "::add-mask::$masked_value"' in workflow
    assert workflow.index('echo "::add-mask::$masked_value"') < workflow.index(
        '} >>"$GITHUB_ENV"'
    )
