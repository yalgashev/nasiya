import asyncio
import logging
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.datastructures import Headers, UploadFile

from app.auth.deps import CurrentSessionContext, CurrentSessionStatus
from app.auth.error_codes import ErrorCode
from app.settings import Settings
from app.storage.body_guard import StorageBodyLimitMiddleware
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    PresignedObjectUrl,
)
from app.storage.multipart import (
    BoundedMultipartUpload,
    StorageMultipartError,
    bounded_multipart_upload,
)

SENSITIVE_ENDPOINT = "https://private-m8-storage.example.invalid:9443"
SENSITIVE_ACCESS_KEY = "m8-synthetic-access-do-not-render"
SENSITIVE_SECRET_KEY = "m8-synthetic-secret-do-not-render"
SENSITIVE_BUCKET = "m8-private-fixture-bucket"
SENSITIVE_OBJECT_KEY = "v1/objects/0123456789abcdef0123456789abcdef.jpg"
SENSITIVE_PRESIGNED_URL = (
    f"{SENSITIVE_ENDPOINT}/{SENSITIVE_BUCKET}/{SENSITIVE_OBJECT_KEY}"
    f"?X-Amz-Credential={SENSITIVE_ACCESS_KEY}"
    f"&X-Amz-Signature={SENSITIVE_SECRET_KEY}"
)
SENSITIVE_TEMP_PATH = "/tmp/m8-private-upload-991-secret"
SENSITIVE_FILENAME = "customer-991-private-original.jpg"
SENSITIVE_CLAIMED_MIME = "application/x-m8-private-claimed"
SENSITIVE_BODY = b"m8-private-raw-body-991"
SENSITIVE_SESSION_ID = "88888888-8888-4888-8888-888888888888"
SAFE_DATABASE_URL = "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"
SAFE_RATE_LIMIT_KEY = "test-rate-limit-hmac-key-for-storage-leakage"

RAW_TEXT_VALUES = (
    SENSITIVE_ENDPOINT,
    SENSITIVE_ACCESS_KEY,
    SENSITIVE_SECRET_KEY,
    SENSITIVE_BUCKET,
    SENSITIVE_OBJECT_KEY,
    SENSITIVE_PRESIGNED_URL,
    SENSITIVE_TEMP_PATH,
    SENSITIVE_FILENAME,
    SENSITIVE_CLAIMED_MIME,
    SENSITIVE_BODY.decode("ascii"),
    SENSITIVE_SESSION_ID,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "debug": False,
        "database_url": SAFE_DATABASE_URL,
        "session_cookie_secure": False,
        "rate_limit_hmac_key": SAFE_RATE_LIMIT_KEY,
        "object_storage_endpoint_url": SENSITIVE_ENDPOINT,
        "object_storage_region": "region-1",
        "object_storage_bucket": SENSITIVE_BUCKET,
        "object_storage_access_key": SENSITIVE_ACCESS_KEY,
        "object_storage_secret_key": SENSITIVE_SECRET_KEY,
        "object_storage_use_ssl": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _assert_absent(rendered: str, values: tuple[str, ...]) -> None:
    for value in values:
        assert value not in rendered


def test_settings_repr_dump_config_and_validation_error_hide_credentials() -> None:
    settings = _settings()
    config = settings.require_object_storage_config()

    rendered = " ".join(
        (
            repr(settings),
            str(settings),
            repr(settings.model_dump()),
            settings.model_dump_json(),
            repr(config),
            str(config),
        )
    )
    _assert_absent(
        rendered,
        (
            SENSITIVE_ENDPOINT,
            SENSITIVE_ACCESS_KEY,
            SENSITIVE_SECRET_KEY,
        ),
    )

    invalid_endpoint = f"{SENSITIVE_ENDPOINT}/private?credential={SENSITIVE_SECRET_KEY}"
    with pytest.raises(ValidationError) as exc_info:
        _settings(object_storage_endpoint_url=invalid_endpoint)
    _assert_absent(
        f"{exc_info.value!s} {exc_info.value!r}",
        (SENSITIVE_ENDPOINT, SENSITIVE_SECRET_KEY, invalid_endpoint),
    )


def test_typed_wrappers_and_boundary_errors_hide_all_raw_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    wrappers = (
        BucketName(SENSITIVE_BUCKET),
        ObjectKey(SENSITIVE_OBJECT_KEY),
        ObjectChecksumSha256("f" * 64),
        PresignedObjectUrl(SENSITIVE_PRESIGNED_URL),
    )
    upload_file = UploadFile(
        file=BytesIO(SENSITIVE_BODY),
        size=len(SENSITIVE_BODY),
        filename=SENSITIVE_FILENAME,
        headers=Headers({"content-type": SENSITIVE_CLAIMED_MIME}),
    )
    bounded = BoundedMultipartUpload(
        size_bytes=len(SENSITIVE_BODY),
        auxiliary_fields={"caption": "safe"},
        _upload_file=upload_file,
    )
    boundary_error = StorageMultipartError(ErrorCode.UNSUPPORTED_FILE_TYPE)

    with caplog.at_level(logging.INFO, logger="tests.storage.boundary"):
        logging.getLogger("tests.storage.boundary").info(
            "storage boundary objects: %r %r %r",
            wrappers,
            bounded,
            boundary_error,
        )

    rendered = " ".join(
        (
            repr(wrappers),
            str(wrappers),
            repr(bounded),
            str(boundary_error),
            repr(boundary_error),
            caplog.text,
        )
    )
    _assert_absent(rendered, RAW_TEXT_VALUES)
    asyncio.run(upload_file.close())


def test_body_guard_response_and_logs_never_dump_forged_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    application = FastAPI()
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={"/future-upload"},
        max_body_bytes=4,
    )

    @application.post("/future-upload")
    async def future_upload(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    with caplog.at_level(logging.DEBUG):
        response = TestClient(application).post(
            "/future-upload",
            content=SENSITIVE_BODY,
            headers={"Content-Length": "1"},
        )

    assert response.status_code == 413
    assert response.headers["x-error-code"] == ErrorCode.FILE_TOO_LARGE.value
    _assert_absent(f"{response.text} {caplog.text}", RAW_TEXT_VALUES)


def test_multipart_os_error_hides_temp_path_and_closes_over_context() -> None:
    class FailingRequest:
        headers = {"content-type": "multipart/form-data; boundary=fake"}

        async def form(self, **_kwargs: object) -> None:
            raise OSError(SENSITIVE_TEMP_PATH)

    context = CurrentSessionContext(
        status=CurrentSessionStatus.ANONYMOUS,
        session_id=UUID(SENSITIVE_SESSION_ID),
    )

    async def parse() -> None:
        with pytest.raises(StorageMultipartError) as exc_info:
            async with bounded_multipart_upload(
                FailingRequest(),  # type: ignore[arg-type]
                file_field_name="image",
                session_context=context,
                now=datetime.now(UTC),
            ):
                raise AssertionError("failing parser unexpectedly yielded")

        assert exc_info.value.error_code is ErrorCode.FILE_STORAGE_ERROR
        _assert_absent(
            f"{exc_info.value!s} {exc_info.value!r}",
            (SENSITIVE_TEMP_PATH,),
        )

    asyncio.run(parse())


def test_boundary_sources_have_no_filename_access_or_logging_sink() -> None:
    project_root = Path(__file__).resolve().parents[1]
    exact_boundary_files = (
        project_root / "app/storage/body_guard.py",
        project_root / "app/storage/multipart.py",
        project_root / "app/storage/contracts.py",
        project_root / "app/storage/errors.py",
    )

    sources = {
        path.name: path.read_text(encoding="utf-8") for path in exact_boundary_files
    }
    assert ".filename" not in sources["multipart.py"]
    assert "request.body(" not in sources["multipart.py"]
    for source in sources.values():
        assert "logging." not in source
        assert "logger." not in source
        _assert_absent(source, RAW_TEXT_VALUES)
