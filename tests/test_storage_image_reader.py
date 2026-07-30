import asyncio
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    MAX_SOURCE_IMAGE_BYTES,
    SOURCE_READ_CHUNK_BYTES,
    AsyncImageSource,
    BoundedImageBytes,
    ImageSanitizationError,
    read_bounded_image,
)

SENSITIVE_FILENAME = "private-customer-441-original.png"
SENSITIVE_READ_ERROR = "read failed at /tmp/private-customer-441-spool"


class RecordingAsyncSource:
    def __init__(
        self,
        payload: bytes,
        *,
        returned_chunk_bytes: int | None = None,
        fail_seek: bool = False,
        fail_after_reads: int | None = None,
    ) -> None:
        self.payload = payload
        self.returned_chunk_bytes = returned_chunk_bytes
        self.fail_seek = fail_seek
        self.fail_after_reads = fail_after_reads
        self.position = 0
        self.read_requests: list[int] = []
        self.seek_requests: list[int] = []
        self.closed = False

    async def seek(self, offset: int) -> None:
        self.seek_requests.append(offset)
        if self.fail_seek:
            raise OSError(SENSITIVE_READ_ERROR)
        self.position = offset

    async def read(self, size: int) -> bytes:
        self.read_requests.append(size)
        if (
            self.fail_after_reads is not None
            and len(self.read_requests) > self.fail_after_reads
        ):
            raise OSError(SENSITIVE_READ_ERROR)
        returned_size = min(size, self.returned_chunk_bytes or size)
        chunk = self.payload[self.position : self.position + returned_size]
        self.position += len(chunk)
        return chunk

    async def close(self) -> None:
        self.closed = True


def _read(
    source: AsyncImageSource,
    *,
    max_bytes: int = MAX_SOURCE_IMAGE_BYTES,
) -> BoundedImageBytes:
    return asyncio.run(read_bounded_image(source, max_bytes=max_bytes))


def test_frozen_reader_limits() -> None:
    assert MAX_SOURCE_IMAGE_BYTES == 10_485_760
    assert SOURCE_READ_CHUNK_BYTES == 65_536


def test_exact_frozen_maximum_reads_only_one_extra_eof_probe() -> None:
    source = RecordingAsyncSource(b"x" * MAX_SOURCE_IMAGE_BYTES)

    result = _read(source)

    assert len(result) == MAX_SOURCE_IMAGE_BYTES
    assert result.as_internal_bytes() == source.payload
    assert source.seek_requests == [0]
    assert max(source.read_requests) == SOURCE_READ_CHUNK_BYTES
    assert source.read_requests[-1] == 1
    assert sum(source.read_requests) == MAX_SOURCE_IMAGE_BYTES + 1


def test_frozen_maximum_plus_one_is_stable_file_too_large() -> None:
    source = RecordingAsyncSource(b"x" * (MAX_SOURCE_IMAGE_BYTES + 1))

    with pytest.raises(ImageSanitizationError) as exc_info:
        _read(source)

    assert exc_info.value.public_code is ErrorCode.FILE_TOO_LARGE
    assert exc_info.value.internal_code is None
    assert source.position == MAX_SOURCE_IMAGE_BYTES + 1
    assert sum(source.read_requests) == MAX_SOURCE_IMAGE_BYTES + 1


def test_chunk_boundaries_and_short_reads_continue_until_eof() -> None:
    payload = b"0123456789abcdef"
    source = RecordingAsyncSource(payload, returned_chunk_bytes=3)

    result = _read(source, max_bytes=len(payload))

    assert result.as_internal_bytes() == payload
    assert source.read_requests == [17, 14, 11, 8, 5, 2, 1]
    assert source.position == len(payload)


def test_empty_input_is_safe_unsupported_type() -> None:
    source = RecordingAsyncSource(b"")

    with pytest.raises(ImageSanitizationError) as exc_info:
        _read(source)

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert exc_info.value.internal_code is StorageInternalCode.IMAGE_CORRUPT
    assert str(exc_info.value) == ErrorCode.UNSUPPORTED_FILE_TYPE.value


@pytest.mark.parametrize(
    "source",
    [
        RecordingAsyncSource(b"payload", fail_seek=True),
        RecordingAsyncSource(b"payload", fail_after_reads=0),
    ],
)
def test_seek_or_read_error_is_safe_and_does_not_close_borrowed_source(
    source: RecordingAsyncSource,
) -> None:
    with pytest.raises(ImageSanitizationError) as exc_info:
        _read(source)

    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert exc_info.value.public_code is ErrorCode.FILE_STORAGE_ERROR
    assert SENSITIVE_READ_ERROR not in rendered
    assert source.closed is False

    asyncio.run(source.close())
    assert source.closed is True


def test_non_bytes_or_oversized_source_chunk_fails_closed() -> None:
    class InvalidSource:
        async def seek(self, _offset: int) -> None:
            return None

        async def read(self, size: int) -> bytes:
            return b"x" * (size + 1)

    with pytest.raises(ImageSanitizationError) as exc_info:
        _read(InvalidSource(), max_bytes=4)

    assert exc_info.value.public_code is ErrorCode.FILE_STORAGE_ERROR


def test_real_upload_file_is_borrowed_and_raw_values_are_redacted() -> None:
    payload = b"private-image-source-bytes"
    upload = UploadFile(
        file=BytesIO(payload),
        size=len(payload),
        filename=SENSITIVE_FILENAME,
        headers=None,
    )

    result = _read(upload)

    rendered = f"{result!s} {result!r}"
    assert result.as_internal_bytes() == payload
    assert payload.decode("ascii") not in rendered
    assert SENSITIVE_FILENAME not in rendered
    assert upload.file.closed is False

    asyncio.run(upload.close())
    assert upload.file.closed is True


def test_error_constructor_rejects_mismatched_internal_mapping() -> None:
    with pytest.raises(ValueError):
        ImageSanitizationError(
            public_code=ErrorCode.FILE_TOO_LARGE,
            internal_code=StorageInternalCode.IMAGE_CORRUPT,
        )
