import asyncio
from io import BytesIO

import pytest
from PIL import Image, ImageFile
from starlette.datastructures import Headers, UploadFile

from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    CANONICAL_IMAGE_FORMATS,
    BoundedImageBytes,
    CanonicalImageFormat,
    DecodedImage,
    ImageSanitizationError,
    decode_bounded_image,
    read_bounded_image,
)

TRAILING_MARKER = b"m8-private-trailing-payload"
RAW_FAILURE_MARKER = "m8-private-corrupt-source"


def _encoded_image(
    image_format: str,
    *,
    mode: str = "RGB",
    color: tuple[int, ...] = (20, 40, 60),
) -> bytes:
    output = BytesIO()
    Image.new(mode, (3, 2), color).save(output, format=image_format)
    return output.getvalue()


def _decode(payload: bytes) -> DecodedImage:
    return decode_bounded_image(BoundedImageBytes(payload))


def test_canonical_actual_format_mapping_is_exact_and_closed() -> None:
    assert set(CANONICAL_IMAGE_FORMATS) == {"JPEG", "PNG", "WEBP"}
    assert {
        details.image_format: (
            details.content_type,
            details.canonical_extension,
        )
        for details in CANONICAL_IMAGE_FORMATS.values()
    } == {
        CanonicalImageFormat.JPEG: ("image/jpeg", "jpg"),
        CanonicalImageFormat.PNG: ("image/png", "png"),
        CanonicalImageFormat.WEBP: ("image/webp", "webp"),
    }


@pytest.mark.parametrize(
    ("image_format", "expected_format"),
    [
        ("JPEG", CanonicalImageFormat.JPEG),
        ("PNG", CanonicalImageFormat.PNG),
        ("WEBP", CanonicalImageFormat.WEBP),
    ],
)
def test_allowlisted_actual_format_is_verified_reopened_and_fully_loaded(
    image_format: str,
    expected_format: CanonicalImageFormat,
) -> None:
    decoded = _decode(_encoded_image(image_format))
    try:
        image = decoded.as_internal_image()
        assert decoded.format_details.image_format is expected_format
        assert (decoded.width_px, decoded.height_px) == (3, 2)
        assert image.size == (3, 2)
        assert image.getpixel((0, 0)) is not None
        assert image.fp is None
    finally:
        decoded.close()


def test_wrong_filename_extension_and_claimed_mime_are_ignored() -> None:
    payload = _encoded_image("PNG")
    upload = UploadFile(
        file=BytesIO(payload),
        size=len(payload),
        filename="private-name.exe",
        headers=Headers({"content-type": "text/plain"}),
    )

    bounded = asyncio.run(read_bounded_image(upload))
    decoded = decode_bounded_image(bounded)
    try:
        assert decoded.format_details.image_format is CanonicalImageFormat.PNG
        assert decoded.format_details.content_type == "image/png"
        assert decoded.format_details.canonical_extension == "png"
    finally:
        decoded.close()
        asyncio.run(upload.close())


@pytest.mark.parametrize(
    "payload",
    [
        b"not-an-image",
        RAW_FAILURE_MARKER.encode("ascii"),
        _encoded_image("JPEG")[:20],
        _encoded_image("PNG")[:-12],
    ],
)
def test_wrong_corrupt_or_truncated_bytes_fail_with_safe_mapping(
    payload: bytes,
) -> None:
    with pytest.raises(ImageSanitizationError) as exc_info:
        _decode(payload)

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert exc_info.value.internal_code in {
        StorageInternalCode.IMAGE_CORRUPT,
        StorageInternalCode.IMAGE_TRUNCATED,
    }
    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert RAW_FAILURE_MARKER not in rendered
    assert payload.hex() not in rendered


@pytest.mark.parametrize("image_format", ["BMP", "GIF", "TIFF"])
def test_non_allowlisted_decodable_format_is_rejected(image_format: str) -> None:
    with pytest.raises(ImageSanitizationError) as exc_info:
        _decode(_encoded_image(image_format))

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert exc_info.value.internal_code is StorageInternalCode.IMAGE_CORRUPT


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_valid_trailing_bytes_are_decoded_but_never_exposed(
    image_format: str,
) -> None:
    decoded = _decode(_encoded_image(image_format) + TRAILING_MARKER)
    try:
        rendered = f"{decoded!s} {decoded!r}"
        assert decoded.format_details.image_format.value == image_format
        assert TRAILING_MARKER.decode("ascii") not in rendered
        assert decoded.as_internal_image().getpixel((0, 0)) is not None
    finally:
        decoded.close()


def test_decode_stage_does_not_enable_truncated_image_loading() -> None:
    assert ImageFile.LOAD_TRUNCATED_IMAGES is False


def test_decoded_image_validates_dimensions_and_redacts_internal_image() -> None:
    image = Image.new("RGB", (2, 2), (1, 2, 3))
    with pytest.raises(ValueError):
        DecodedImage(
            format_details=CANONICAL_IMAGE_FORMATS["JPEG"],
            width_px=3,
            height_px=2,
            _image=image,
        )
    image.close()

    decoded = _decode(_encoded_image("JPEG"))
    try:
        assert "image=<redacted>" in repr(decoded)
        assert "PIL." not in repr(decoded)
    finally:
        decoded.close()
