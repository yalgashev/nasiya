from io import BytesIO

import pytest
from PIL import Image

import app.storage.image as image_module
from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    BoundedImageBytes,
    ImageDimensionLimits,
    ImageSanitizationError,
    decode_bounded_image,
    orient_decoded_image,
)

ORIENTATION_TAG = 274
SENSITIVE_EXIF_MARKER = "private-customer-773-exif-marker"

A = (255, 0, 0)
B = (0, 255, 0)
C = (0, 0, 255)
D = (255, 255, 0)
E = (255, 0, 255)
F = (0, 255, 255)
SOURCE_PIXELS = [A, B, C, D, E, F]

EXPECTED_ORIENTATIONS = {
    1: ((3, 2), [A, B, C, D, E, F]),
    2: ((3, 2), [C, B, A, F, E, D]),
    3: ((3, 2), [F, E, D, C, B, A]),
    4: ((3, 2), [D, E, F, A, B, C]),
    5: ((2, 3), [A, D, B, E, C, F]),
    6: ((2, 3), [D, A, E, B, F, C]),
    7: ((2, 3), [F, C, E, B, D, A]),
    8: ((2, 3), [C, F, B, E, A, D]),
}


def _oriented_png(orientation: int) -> bytes:
    image = Image.new("RGB", (3, 2))
    image.putdata(SOURCE_PIXELS)
    exif = Image.Exif()
    exif[ORIENTATION_TAG] = orientation
    output = BytesIO()
    image.save(output, format="PNG", exif=exif)
    image.close()
    return output.getvalue()


@pytest.mark.parametrize("orientation", range(1, 9))
def test_all_exif_orientation_values_preserve_visual_corner_semantics(
    orientation: int,
) -> None:
    decoded = decode_bounded_image(BoundedImageBytes(_oriented_png(orientation)))
    source_image = decoded.as_internal_image()
    source_pixels_before = list(source_image.get_flattened_data())
    source_exif_before = source_image.getexif().tobytes()

    oriented = orient_decoded_image(decoded)
    try:
        expected_size, expected_pixels = EXPECTED_ORIENTATIONS[orientation]
        output_image = oriented.as_internal_image()

        assert output_image.size == expected_size
        assert (oriented.width_px, oriented.height_px) == expected_size
        assert list(output_image.get_flattened_data()) == expected_pixels
        assert output_image.getexif().get(ORIENTATION_TAG) is None

        assert source_image.size == (3, 2)
        assert list(source_image.get_flattened_data()) == source_pixels_before
        assert source_image.getexif().get(ORIENTATION_TAG) == orientation
        assert source_image.getexif().tobytes() == source_exif_before
    finally:
        oriented.close()
        decoded.close()


def test_post_transform_dimensions_are_rechecked_with_same_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded = decode_bounded_image(BoundedImageBytes(_oriented_png(6)))
    limits = ImageDimensionLimits(max_dimension=3, max_pixels=6)
    calls: list[tuple[tuple[int, int], ImageDimensionLimits]] = []
    original_validate = image_module.validate_image_dimensions

    def recording_validate(
        size: tuple[int, int],
        applied_limits: ImageDimensionLimits,
    ) -> None:
        calls.append((size, applied_limits))
        original_validate(size, applied_limits)

    monkeypatch.setattr(
        image_module,
        "validate_image_dimensions",
        recording_validate,
    )

    oriented = orient_decoded_image(decoded, limits=limits)
    try:
        assert calls == [((2, 3), limits)]
    finally:
        oriented.close()
        decoded.close()


def test_malformed_exif_fails_safely_without_raw_detail() -> None:
    output = BytesIO()
    Image.new("RGB", (3, 2), (1, 2, 3)).save(
        output,
        format="PNG",
        exif=b"Exif\x00\x00" + SENSITIVE_EXIF_MARKER.encode("ascii"),
    )
    decoded = decode_bounded_image(BoundedImageBytes(output.getvalue()))

    try:
        with pytest.raises(ImageSanitizationError) as exc_info:
            orient_decoded_image(decoded)
    finally:
        decoded.close()

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert exc_info.value.internal_code is StorageInternalCode.IMAGE_CORRUPT
    rendered = f"{exc_info.value!s} {exc_info.value!r}"
    assert SENSITIVE_EXIF_MARKER not in rendered


def test_oriented_wrapper_redacts_image_and_source_exif() -> None:
    decoded = decode_bounded_image(BoundedImageBytes(_oriented_png(8)))
    oriented = orient_decoded_image(decoded)
    try:
        rendered = f"{oriented!s} {oriented!r}"
        assert "image=<redacted>" in rendered
        assert "Exif" not in rendered
        assert SENSITIVE_EXIF_MARKER not in rendered
    finally:
        oriented.close()
        decoded.close()
