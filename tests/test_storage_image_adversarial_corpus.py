import struct
import zlib
from collections.abc import Callable
from io import BytesIO

import pytest
from PIL import ExifTags, Image, PngImagePlugin

from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    MAX_IMAGE_DIMENSION,
    BoundedImageBytes,
    ImageSanitizationError,
    sanitize_bounded_image,
    verify_reopened_metadata_absence,
)

TRAILING_MARKER = b"synthetic-m8-trailing-payload"
METADATA_MARKER = b"synthetic-m8-metadata"
DECODER_ERROR_MARKER = "synthetic-m8-decoder-detail"


def _encoded(
    image_format: str,
    *,
    mode: str = "RGB",
    color: int | tuple[int, ...] = (10, 20, 30),
    save_options: dict[str, object] | None = None,
) -> bytes:
    image = Image.new(mode, (4, 3), color)
    output = BytesIO()
    image.save(output, format=image_format, **(save_options or {}))
    image.close()
    return output.getvalue()


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload)
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", checksum)
    )


def _tiny_png_with_declared_dimensions(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) + _png_chunk(b"IEND", b"")


def _animated(image_format: str) -> bytes:
    first = Image.new("RGBA", (3, 2), (255, 0, 0, 255))
    second = Image.new("RGBA", (3, 2), (0, 0, 255, 255))
    output = BytesIO()
    options = {"lossless": True} if image_format == "WEBP" else {}
    first.save(
        output,
        format=image_format,
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
        **options,
    )
    first.close()
    second.close()
    return output.getvalue()


def _metadata_rich(image_format: str) -> bytes:
    image = (
        Image.new("RGB", (4, 3), (10, 20, 30))
        if image_format == "JPEG"
        else Image.new("RGBA", (4, 3), (10, 20, 30, 77))
    )
    exif = Image.Exif()
    exif[ExifTags.Base.ImageDescription] = METADATA_MARKER.decode("ascii")
    output = BytesIO()
    options: dict[str, object] = {
        "exif": exif,
        "icc_profile": METADATA_MARKER,
    }
    if image_format == "PNG":
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Comment", METADATA_MARKER.decode("ascii"))
        png_info.add_itxt(
            "XML:com.adobe.xmp",
            METADATA_MARKER.decode("ascii"),
        )
        options["pnginfo"] = png_info
    elif image_format == "WEBP":
        options.update({"lossless": True, "xmp": METADATA_MARKER})
    else:
        options.update(
            {
                "comment": METADATA_MARKER,
                "xmp": METADATA_MARKER,
            }
        )
    image.save(output, format=image_format, **options)
    image.close()
    return output.getvalue()


def _palette_png(*, transparent: bool) -> bytes:
    image = Image.new("P", (2, 1))
    image.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    image.putdata([0, 1])
    if transparent:
        image.info["transparency"] = 0
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue()


def _assert_safe_image_error(
    exc: ImageSanitizationError,
    *,
    expected_internal: set[StorageInternalCode] | None = None,
) -> None:
    assert exc.public_code in {
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.UNSUPPORTED_FILE_TYPE,
    }
    if expected_internal is not None:
        assert exc.internal_code in expected_internal
    assert DECODER_ERROR_MARKER not in f"{exc!s} {exc!r}"


def test_malformed_headers_and_truncated_chunks_fail_closed() -> None:
    png = _encoded("PNG")
    jpeg = _encoded("JPEG")
    webp = _encoded("WEBP", save_options={"lossless": True})
    corpus = (
        b"\x89PNG\r\n\x1a\n",
        png[:20],
        png[:31],
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00",
        jpeg[:24],
        b"RIFF\x10\x00\x00\x00WEBPVP8 ",
        webp[:20],
    )

    for payload in corpus:
        with pytest.raises(ImageSanitizationError) as exc_info:
            sanitize_bounded_image(BoundedImageBytes(payload))
        _assert_safe_image_error(
            exc_info.value,
            expected_internal={
                StorageInternalCode.IMAGE_CORRUPT,
                StorageInternalCode.IMAGE_TRUNCATED,
            },
        )


def test_huge_declared_dimension_is_rejected_from_tiny_header() -> None:
    payload = _tiny_png_with_declared_dimensions(MAX_IMAGE_DIMENSION + 1, 1)
    assert len(payload) < 64

    with pytest.raises(ImageSanitizationError) as exc_info:
        sanitize_bounded_image(BoundedImageBytes(payload))

    _assert_safe_image_error(
        exc_info.value,
        expected_internal={StorageInternalCode.IMAGE_DIMENSION_LIMIT_EXCEEDED},
    )


@pytest.mark.parametrize(
    ("expected_content_type", "payload_factory"),
    [
        pytest.param("image/jpeg", lambda: _encoded("JPEG"), id="jpeg"),
        pytest.param("image/png", lambda: _encoded("PNG"), id="png"),
        pytest.param(
            "image/webp",
            lambda: _encoded("WEBP", save_options={"lossless": True}),
            id="webp",
        ),
    ],
)
def test_polyglot_trailing_payload_is_never_copied_to_output(
    expected_content_type: str,
    payload_factory: Callable[[], bytes],
) -> None:
    result = sanitize_bounded_image(
        BoundedImageBytes(payload_factory() + TRAILING_MARKER)
    )
    output = result.sanitized_bytes.as_internal_bytes()

    assert result.metadata.content_type == expected_content_type
    assert TRAILING_MARKER not in output
    with Image.open(BytesIO(output)) as reopened:
        reopened.load()
        verify_reopened_metadata_absence(reopened)


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_metadata_variants_are_removed_by_fresh_pixel_reencode(
    image_format: str,
) -> None:
    source = _metadata_rich(image_format)
    assert METADATA_MARKER in source

    result = sanitize_bounded_image(BoundedImageBytes(source))
    output = result.sanitized_bytes.as_internal_bytes()

    assert METADATA_MARKER not in output
    with Image.open(BytesIO(output)) as reopened:
        reopened.load()
        verify_reopened_metadata_absence(reopened)
        assert len(reopened.getexif()) == 0


@pytest.mark.parametrize(
    ("payload_factory", "expected_mode"),
    [
        pytest.param(lambda: _palette_png(transparent=False), "RGB", id="palette"),
        pytest.param(
            lambda: _palette_png(transparent=True),
            "RGBA",
            id="palette-alpha",
        ),
        pytest.param(
            lambda: _encoded("PNG", mode="LA", color=(120, 64)),
            "RGBA",
            id="luminance-alpha",
        ),
        pytest.param(
            lambda: _encoded("JPEG", mode="CMYK", color=(1, 2, 3, 4)),
            "RGB",
            id="cmyk",
        ),
    ],
)
def test_palette_transparency_and_modes_have_closed_canonical_outputs(
    payload_factory: Callable[[], bytes],
    expected_mode: str,
) -> None:
    result = sanitize_bounded_image(BoundedImageBytes(payload_factory()))

    with Image.open(BytesIO(result.sanitized_bytes.as_internal_bytes())) as reopened:
        reopened.load()
        assert reopened.mode == expected_mode
        verify_reopened_metadata_absence(reopened)


@pytest.mark.parametrize("image_format", ["PNG", "WEBP"])
def test_animated_flags_fail_without_first_frame_fallback(
    image_format: str,
) -> None:
    with pytest.raises(ImageSanitizationError) as exc_info:
        sanitize_bounded_image(BoundedImageBytes(_animated(image_format)))

    _assert_safe_image_error(
        exc_info.value,
        expected_internal={StorageInternalCode.IMAGE_ANIMATION_UNSUPPORTED},
    )


@pytest.mark.parametrize(
    "payload_factory",
    [
        pytest.param(lambda: _encoded("JPEG"), id="jpeg"),
        pytest.param(lambda: _encoded("PNG"), id="png"),
        pytest.param(
            lambda: _encoded("WEBP", save_options={"lossless": True}),
            id="webp",
        ),
    ],
)
def test_low_injected_output_limit_is_stable(
    payload_factory: Callable[[], bytes],
) -> None:
    with pytest.raises(ImageSanitizationError) as exc_info:
        sanitize_bounded_image(
            BoundedImageBytes(payload_factory()),
            max_output_bytes=1,
        )

    _assert_safe_image_error(
        exc_info.value,
        expected_internal={StorageInternalCode.SANITIZED_OUTPUT_TOO_LARGE},
    )


@pytest.mark.parametrize(
    "decoder_error",
    [
        pytest.param(RuntimeError(DECODER_ERROR_MARKER), id="runtime"),
        pytest.param(OverflowError(DECODER_ERROR_MARKER), id="overflow"),
    ],
)
def test_decoder_load_exceptions_map_safely_without_detail(
    monkeypatch: pytest.MonkeyPatch,
    decoder_error: Exception,
) -> None:
    payload = _encoded("PNG")

    def failing_load(
        self: PngImagePlugin.PngImageFile,
        *args: object,
        **kwargs: object,
    ) -> None:
        raise decoder_error

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", failing_load)

    with pytest.raises(ImageSanitizationError) as exc_info:
        sanitize_bounded_image(BoundedImageBytes(payload))

    _assert_safe_image_error(
        exc_info.value,
        expected_internal={StorageInternalCode.IMAGE_CORRUPT},
    )


def test_generated_corpus_is_deterministic_and_has_no_random_or_waiting() -> None:
    sources = (
        _encoded("JPEG"),
        _encoded("PNG"),
        _encoded("WEBP", save_options={"lossless": True}),
    )

    for source in sources:
        first = sanitize_bounded_image(BoundedImageBytes(source))
        second = sanitize_bounded_image(BoundedImageBytes(source))
        assert (
            first.sanitized_bytes.as_internal_bytes()
            == second.sanitized_bytes.as_internal_bytes()
        )
        assert first.metadata == second.metadata
