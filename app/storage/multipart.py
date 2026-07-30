from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Final

from fastapi import HTTPException, Request
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth.deps import (
    CSRF_FORM_FIELD_NAME,
    CurrentSessionContext,
    validate_csrf,
)
from app.auth.error_codes import (
    ErrorCode,
    get_error_http_status,
    get_public_error_body,
)

MAX_MULTIPART_FILES: Final = 1
MAX_MULTIPART_FIELDS: Final = 8
MAX_FILE_PART_BYTES: Final = 10_485_760
MAX_AUXILIARY_FIELD_BYTES: Final = 4_096
MULTIPART_CONTENT_TYPE: Final = "multipart/form-data"


class StorageMultipartError(HTTPException):
    def __init__(self, code: ErrorCode) -> None:
        if code not in {
            ErrorCode.FILE_STORAGE_ERROR,
            ErrorCode.FILE_TOO_LARGE,
            ErrorCode.UNSUPPORTED_FILE_TYPE,
        }:
            raise ValueError("Unsupported multipart public error code")
        self.error_code = code
        super().__init__(
            status_code=get_error_http_status(code),
            detail=get_public_error_body(code),
            headers={
                "Cache-Control": "no-store",
                "X-Error-Code": code.value,
            },
        )

    def __repr__(self) -> str:
        return f"StorageMultipartError(error_code={self.error_code.value!r})"


@dataclass(frozen=True, repr=False)
class BoundedMultipartUpload:
    size_bytes: int
    auxiliary_fields: Mapping[str, str]
    _upload_file: UploadFile = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.size_bytes < 0 or self.size_bytes > MAX_FILE_PART_BYTES:
            raise ValueError("Invalid bounded upload size")
        object.__setattr__(
            self,
            "auxiliary_fields",
            MappingProxyType(dict(self.auxiliary_fields)),
        )

    def __repr__(self) -> str:
        return (
            "BoundedMultipartUpload("
            f"size_bytes={self.size_bytes!r}, "
            f"auxiliary_field_count={len(self.auxiliary_fields)!r}, "
            "upload_file=<redacted>"
            ")"
        )

    def as_upload_file(self) -> UploadFile:
        return self._upload_file


@asynccontextmanager
async def bounded_multipart_upload(
    request: Request,
    *,
    file_field_name: str,
    session_context: CurrentSessionContext,
    now: datetime,
) -> AsyncIterator[BoundedMultipartUpload]:
    """Parse one bounded file and preserve the existing CSRF dependency semantics."""

    if not file_field_name or not file_field_name.isascii():
        raise ValueError("file_field_name must be a non-empty ASCII value")
    if _request_content_type(request) != MULTIPART_CONTENT_TYPE:
        raise StorageMultipartError(ErrorCode.UNSUPPORTED_FILE_TYPE)

    form: FormData | None = None
    try:
        try:
            form = await request.form(
                max_files=MAX_MULTIPART_FILES,
                max_fields=MAX_MULTIPART_FIELDS,
                max_part_size=MAX_FILE_PART_BYTES,
            )
        except StarletteHTTPException as exc:
            raise _safe_parser_error(exc) from None
        except OSError:
            raise StorageMultipartError(ErrorCode.FILE_STORAGE_ERROR) from None

        upload_file, auxiliary_fields = _validate_form_parts(
            form,
            file_field_name=file_field_name,
        )

        # Request.form() is cached on the Request, so this reuses the existing
        # header/form token agreement and session-bound CSRF validation without
        # triggering a second or unbounded parse.
        await validate_csrf(request, session_context, now)

        size_bytes = upload_file.size
        if size_bytes is None:
            raise StorageMultipartError(ErrorCode.UNSUPPORTED_FILE_TYPE)
        if size_bytes > MAX_FILE_PART_BYTES:
            raise StorageMultipartError(ErrorCode.FILE_TOO_LARGE)

        yield BoundedMultipartUpload(
            size_bytes=size_bytes,
            auxiliary_fields=auxiliary_fields,
            _upload_file=upload_file,
        )
    finally:
        if form is not None:
            await form.close()


def _validate_form_parts(
    form: FormData,
    *,
    file_field_name: str,
) -> tuple[UploadFile, dict[str, str]]:
    matching_files: list[UploadFile] = []
    auxiliary_fields: dict[str, str] = {}

    for field_name, value in form.multi_items():
        if isinstance(value, UploadFile):
            if field_name == file_field_name:
                matching_files.append(value)
            continue

        if field_name in auxiliary_fields:
            raise StorageMultipartError(ErrorCode.UNSUPPORTED_FILE_TYPE)
        if len(value.encode("utf-8")) > MAX_AUXILIARY_FIELD_BYTES:
            raise StorageMultipartError(ErrorCode.FILE_TOO_LARGE)
        auxiliary_fields[field_name] = value

    if len(matching_files) != 1:
        raise StorageMultipartError(ErrorCode.UNSUPPORTED_FILE_TYPE)

    auxiliary_fields.pop(CSRF_FORM_FIELD_NAME, None)
    return matching_files[0], auxiliary_fields


def _safe_parser_error(exc: StarletteHTTPException) -> StorageMultipartError:
    if isinstance(exc.detail, str) and exc.detail.startswith(
        "Part exceeded maximum size"
    ):
        return StorageMultipartError(ErrorCode.FILE_TOO_LARGE)
    return StorageMultipartError(ErrorCode.UNSUPPORTED_FILE_TYPE)


def _request_content_type(request: Request) -> str:
    content_type = request.headers.get("content-type", "")
    return content_type.split(";", 1)[0].strip().casefold()
