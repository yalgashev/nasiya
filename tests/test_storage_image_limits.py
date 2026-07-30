import inspect
from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

import app.storage.image as image_module
from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    DEFAULT_IMAGE_DIMENSION_LIMITS,
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_PIXELS,
    BoundedImageBytes,
    ImageDimensionLimits,
    ImageSanitizationError,
    decode_bounded_image,
    validate_image_dimensions,
)


def _png(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(output, format="PNG")
    return output.getvalue()


def _decode_with_limits(
    payload: bytes,
    *,
    max_dimension: int,
    max_pixels: int,
):
    return decode_bounded_image(
        BoundedImageBytes(payload),
        limits=ImageDimensionLimits(
            max_dimension=max_dimension,
            max_pixels=max_pixels,
        ),
    )


def _assert_limit_error(
    exc: ImageSanitizationError,
    internal_code: StorageInternalCode,
) -> None:
    assert exc.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert exc.internal_code is internal_code


def test_frozen_dimension_and_pixel_limits_are_exact() -> None:
    assert MAX_IMAGE_DIMENSION == 16_384
    assert MAX_IMAGE_PIXELS == 40_000_000
    assert DEFAULT_IMAGE_DIMENSION_LIMITS == ImageDimensionLimits(
        max_dimension=16_384,
        max_pixels=40_000_000,
    )


@pytest.mark.parametrize(
    ("max_dimension", "max_pixels"),
    [
        (0, 1),
        (MAX_IMAGE_DIMENSION + 1, 1),
        (1, 0),
        (1, MAX_IMAGE_PIXELS + 1),
    ],
)
def test_injected_limits_can_only_tighten_frozen_policy(
    max_dimension: int,
    max_pixels: int,
) -> None:
    with pytest.raises(ValueError):
        ImageDimensionLimits(
            max_dimension=max_dimension,
            max_pixels=max_pixels,
        )


def test_axis_limit_rejects_before_full_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _png(6, 2)
    load_calls = 0

    def forbidden_load(
        self: PngImagePlugin.PngImageFile,
        *args: object,
        **kwargs: object,
    ):
        nonlocal load_calls
        load_calls += 1
        raise AssertionError("full load happened before dimension rejection")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", forbidden_load)

    with pytest.raises(ImageSanitizationError) as exc_info:
        _decode_with_limits(payload, max_dimension=5, max_pixels=100)

    _assert_limit_error(
        exc_info.value,
        StorageInternalCode.IMAGE_DIMENSION_LIMIT_EXCEEDED,
    )
    assert load_calls == 0


def test_pixel_product_limit_rejects_small_synthetic_fixture() -> None:
    with pytest.raises(ImageSanitizationError) as exc_info:
        _decode_with_limits(
            _png(5, 5),
            max_dimension=5,
            max_pixels=24,
        )

    _assert_limit_error(
        exc_info.value,
        StorageInternalCode.IMAGE_PIXEL_LIMIT_EXCEEDED,
    )


def test_exact_injected_dimension_and_pixel_bounds_are_allowed() -> None:
    decoded = _decode_with_limits(
        _png(5, 4),
        max_dimension=5,
        max_pixels=20,
    )
    try:
        assert (decoded.width_px, decoded.height_px) == (5, 4)
    finally:
        decoded.close()


def test_dimensions_are_rechecked_after_full_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _png(3, 2)
    original_load = PngImagePlugin.PngImageFile.load
    load_calls = 0

    def changing_load(
        self: PngImagePlugin.PngImageFile,
        *args: object,
        **kwargs: object,
    ):
        nonlocal load_calls
        result = original_load(self, *args, **kwargs)
        load_calls += 1
        self._size = (11, 2)
        return result

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", changing_load)

    with pytest.raises(ImageSanitizationError) as exc_info:
        _decode_with_limits(payload, max_dimension=10, max_pixels=100)

    _assert_limit_error(
        exc_info.value,
        StorageInternalCode.IMAGE_DIMENSION_LIMIT_EXCEEDED,
    )
    assert load_calls == 1


@pytest.mark.parametrize("temporary_pillow_limit", [300, 100])
def test_pillow_bomb_warning_and_error_are_fatal_without_large_allocation(
    monkeypatch: pytest.MonkeyPatch,
    temporary_pillow_limit: int,
) -> None:
    payload = _png(20, 20)
    original_pillow_limit = Image.MAX_IMAGE_PIXELS

    with monkeypatch.context() as patch:
        patch.setattr(Image, "MAX_IMAGE_PIXELS", temporary_pillow_limit)
        with pytest.raises(ImageSanitizationError) as exc_info:
            decode_bounded_image(BoundedImageBytes(payload))
        _assert_limit_error(
            exc_info.value,
            StorageInternalCode.IMAGE_PIXEL_LIMIT_EXCEEDED,
        )

    assert Image.MAX_IMAGE_PIXELS == original_pillow_limit


def test_post_orientation_validation_seam_uses_same_policy() -> None:
    limits = ImageDimensionLimits(max_dimension=5, max_pixels=20)
    validate_image_dimensions((5, 4), limits)

    with pytest.raises(ImageSanitizationError) as exc_info:
        validate_image_dimensions((4, 6), limits)
    _assert_limit_error(
        exc_info.value,
        StorageInternalCode.IMAGE_DIMENSION_LIMIT_EXCEEDED,
    )


def test_product_code_never_mutates_global_pillow_pixel_setting() -> None:
    source = inspect.getsource(image_module)
    assert "Image.MAX_IMAGE_PIXELS =" not in source
