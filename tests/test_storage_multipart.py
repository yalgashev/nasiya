from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from app.auth.csrf import get_csrf_token
from app.auth.deps import (
    CsrfFailed,
    CurrentSessionContext,
    CurrentSessionStatus,
    csrf_failed_exception_handler,
)
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.storage.body_guard import (
    DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES,
    StorageBodyLimitMiddleware,
)
from app.storage.multipart import (
    MAX_AUXILIARY_FIELD_BYTES,
    MAX_FILE_PART_BYTES,
    MAX_MULTIPART_FIELDS,
    MAX_MULTIPART_FILES,
    BoundedMultipartUpload,
    StorageMultipartError,
    bounded_multipart_upload,
)

UPLOAD_PATH = "/future-image"
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
SENSITIVE_FILENAME = "private-customer-991-secret.jpg"


def _session_context(
    status: CurrentSessionStatus = CurrentSessionStatus.ANONYMOUS,
) -> tuple[CurrentSessionContext, AuthSession, str]:
    session_id = uuid4()
    user_id = uuid4() if status is CurrentSessionStatus.AUTHENTICATED else None
    session = AuthSession(
        id=session_id,
        user_id=user_id,
        active_shop_id=None,
        token_hash="a" * 64,
        csrf_secret="csrf-secret-only-for-bounded-multipart-tests",
        user_agent="pytest",
        created_at=NOW,
        last_seen_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
        revoked_at=None,
    )
    context = CurrentSessionContext(
        status=status,
        session_id=session_id,
        user_id=user_id,
        _session=session,
    )
    return context, session, get_csrf_token(session).as_form_value()


def _tiny_client(
    status: CurrentSessionStatus = CurrentSessionStatus.ANONYMOUS,
) -> tuple[TestClient, str, list[UploadFile], list[str]]:
    context, _session, csrf_token = _session_context(status)
    observed_files: list[UploadFile] = []
    observed_reprs: list[str] = []
    application = FastAPI()
    application.add_exception_handler(CsrfFailed, csrf_failed_exception_handler)
    application.add_middleware(
        StorageBodyLimitMiddleware,
        protected_paths={UPLOAD_PATH},
        max_body_bytes=DEFAULT_STORAGE_MULTIPART_BODY_LIMIT_BYTES,
    )

    @application.post(UPLOAD_PATH)
    async def future_image(request: Request) -> dict[str, object]:
        async with bounded_multipart_upload(
            request,
            file_field_name="image",
            session_context=context,
            now=NOW,
        ) as bounded:
            upload_file = bounded.as_upload_file()
            observed_files.append(upload_file)
            observed_reprs.append(repr(bounded))
            return {
                "size_bytes": bounded.size_bytes,
                "auxiliary_fields": dict(bounded.auxiliary_fields),
                "closed_during_handler": upload_file.file.closed,
            }

    return TestClient(application), csrf_token, observed_files, observed_reprs


def _assert_storage_error(response, code: ErrorCode) -> None:
    assert response.status_code in {413, 415, 503}
    assert response.headers["x-error-code"] == code.value
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"]["code"] == code.value


def test_frozen_multipart_parser_bounds() -> None:
    assert MAX_MULTIPART_FILES == 1
    assert MAX_MULTIPART_FIELDS == 8
    assert MAX_FILE_PART_BYTES == 10_485_760
    assert MAX_AUXILIARY_FIELD_BYTES == 4_096


@pytest.mark.parametrize(
    "status",
    [
        CurrentSessionStatus.ANONYMOUS,
        CurrentSessionStatus.AUTHENTICATED,
    ],
)
def test_valid_exact_file_preserves_session_csrf_and_always_closes(
    status: CurrentSessionStatus,
) -> None:
    client, csrf_token, observed_files, observed_reprs = _tiny_client(status)

    response = client.post(
        UPLOAD_PATH,
        data={"csrf_token": csrf_token, "caption": "bounded"},
        files={
            "image": (
                SENSITIVE_FILENAME,
                b"valid-image-placeholder",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "size_bytes": len(b"valid-image-placeholder"),
        "auxiliary_fields": {"caption": "bounded"},
        "closed_during_handler": False,
    }
    assert observed_files[0].file.closed is True
    assert SENSITIVE_FILENAME not in observed_reprs[0]
    assert csrf_token not in observed_reprs[0]


def test_duplicate_exact_file_field_is_rejected_without_filename_leak() -> None:
    client, csrf_token, observed_files, _ = _tiny_client()

    response = client.post(
        UPLOAD_PATH,
        data={"csrf_token": csrf_token},
        files=[
            ("image", (SENSITIVE_FILENAME, b"first", "image/jpeg")),
            ("image", ("second-secret.png", b"second", "image/png")),
        ],
    )

    _assert_storage_error(response, ErrorCode.UNSUPPORTED_FILE_TYPE)
    assert SENSITIVE_FILENAME not in response.text
    assert "second-secret.png" not in response.text
    assert observed_files == []


def test_two_different_file_fields_are_rejected() -> None:
    client, csrf_token, _, _ = _tiny_client()

    response = client.post(
        UPLOAD_PATH,
        data={"csrf_token": csrf_token},
        files=[
            ("image", ("one.jpg", b"first", "image/jpeg")),
            ("other", ("two.jpg", b"second", "image/jpeg")),
        ],
    )

    _assert_storage_error(response, ErrorCode.UNSUPPORTED_FILE_TYPE)


def test_more_than_eight_string_fields_are_rejected() -> None:
    client, csrf_token, _, _ = _tiny_client()
    fields = {"csrf_token": csrf_token}
    fields.update({f"field_{index}": "x" for index in range(MAX_MULTIPART_FIELDS)})

    response = client.post(
        UPLOAD_PATH,
        data=fields,
        files={"image": ("one.jpg", b"first", "image/jpeg")},
    )

    _assert_storage_error(response, ErrorCode.UNSUPPORTED_FILE_TYPE)


@pytest.mark.parametrize(
    ("form_token", "header_token"),
    [
        ("wrong-token", None),
        ("valid", "different-token"),
    ],
)
def test_invalid_or_disagreeing_csrf_reuses_existing_failure(
    form_token: str,
    header_token: str | None,
) -> None:
    client, csrf_token, observed_files, _ = _tiny_client()
    submitted_form_token = csrf_token if form_token == "valid" else form_token
    headers = {"X-CSRF-Token": header_token} if header_token else {}

    response = client.post(
        UPLOAD_PATH,
        data={"csrf_token": submitted_form_token},
        files={"image": ("one.jpg", b"first", "image/jpeg")},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert response.json()["detail"]["code"] == ErrorCode.CSRF_FAILED.value
    assert observed_files == []


def test_file_part_at_exact_maximum_is_allowed_then_closed() -> None:
    client, csrf_token, observed_files, _ = _tiny_client()

    response = client.post(
        UPLOAD_PATH,
        data={"csrf_token": csrf_token},
        files={
            "image": (
                "exact.bin",
                b"x" * MAX_FILE_PART_BYTES,
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["size_bytes"] == MAX_FILE_PART_BYTES
    assert observed_files[0].file.closed is True


def test_file_part_at_maximum_plus_one_is_stable_file_too_large() -> None:
    client, csrf_token, _, _ = _tiny_client()

    response = client.post(
        UPLOAD_PATH,
        data={"csrf_token": csrf_token},
        files={
            "image": (
                "too-large.bin",
                b"x" * (MAX_FILE_PART_BYTES + 1),
                "application/octet-stream",
            )
        },
    )

    _assert_storage_error(response, ErrorCode.FILE_TOO_LARGE)
    assert "too-large.bin" not in response.text


def test_oversized_auxiliary_string_is_rejected() -> None:
    client, csrf_token, _, _ = _tiny_client()

    response = client.post(
        UPLOAD_PATH,
        data={
            "csrf_token": csrf_token,
            "caption": "x" * (MAX_AUXILIARY_FIELD_BYTES + 1),
        },
        files={"image": ("one.jpg", b"first", "image/jpeg")},
    )

    _assert_storage_error(response, ErrorCode.FILE_TOO_LARGE)


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"data": {"csrf_token": "not-relevant"}},
        {"content": b"raw-body", "headers": {"Content-Type": "application/json"}},
    ],
)
def test_non_multipart_or_missing_file_is_rejected(
    request_kwargs: dict[str, object],
) -> None:
    client, _, _, _ = _tiny_client()

    response = client.post(UPLOAD_PATH, **request_kwargs)

    _assert_storage_error(response, ErrorCode.UNSUPPORTED_FILE_TYPE)


def test_bounded_upload_and_exception_repr_are_redacted() -> None:
    class FakeUpload:
        filename = SENSITIVE_FILENAME

    with pytest.raises(ValueError):
        BoundedMultipartUpload(
            size_bytes=MAX_FILE_PART_BYTES + 1,
            auxiliary_fields={"caption": "private-caption"},
            _upload_file=FakeUpload(),  # type: ignore[arg-type]
        )

    exception = StorageMultipartError(ErrorCode.UNSUPPORTED_FILE_TYPE)
    rendered = repr(exception)
    assert rendered == ("StorageMultipartError(error_code='UNSUPPORTED_FILE_TYPE')")
    assert SENSITIVE_FILENAME not in rendered
