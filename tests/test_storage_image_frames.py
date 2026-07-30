import inspect
from io import BytesIO

import pytest
from PIL import Image

import app.storage.image as image_module
from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    BoundedImageBytes,
    CanonicalImageFormat,
    ImageSanitizationError,
    decode_bounded_image,
)


def _static_image(image_format: str) -> bytes:
    output = BytesIO()
    save_options = {"lossless": True} if image_format == "WEBP" else {}
    Image.new("RGBA", (3, 2), (255, 0, 0, 128)).save(
        output,
        format=image_format,
        **save_options,
    )
    return output.getvalue()


def _animated_image(image_format: str) -> bytes:
    first = Image.new("RGBA", (3, 2), (255, 0, 0, 255))
    second = Image.new("RGBA", (3, 2), (0, 0, 255, 255))
    output = BytesIO()
    save_options = {"lossless": True} if image_format == "WEBP" else {}
    first.save(
        output,
        format=image_format,
        save_all=True,
        append_images=[second],
        duration=[100, 100],
        loop=0,
        **save_options,
    )
    first.close()
    second.close()
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_format", "expected_format"),
    [
        ("PNG", CanonicalImageFormat.PNG),
        ("WEBP", CanonicalImageFormat.WEBP),
    ],
)
def test_static_png_and_webp_are_accepted(
    image_format: str,
    expected_format: CanonicalImageFormat,
) -> None:
    decoded = decode_bounded_image(BoundedImageBytes(_static_image(image_format)))
    try:
        image = decoded.as_internal_image()
        assert decoded.format_details.image_format is expected_format
        assert getattr(image, "n_frames", 1) == 1
        assert getattr(image, "is_animated", False) is False
    finally:
        decoded.close()


@pytest.mark.parametrize("image_format", ["PNG", "WEBP"])
def test_animated_apng_and_webp_are_rejected_without_first_frame_fallback(
    image_format: str,
) -> None:
    payload = _animated_image(image_format)

    with Image.open(BytesIO(payload)) as fixture:
        assert fixture.n_frames == 2
        assert fixture.is_animated is True

    with pytest.raises(ImageSanitizationError) as exc_info:
        decode_bounded_image(BoundedImageBytes(payload))

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert (
        exc_info.value.internal_code is StorageInternalCode.IMAGE_ANIMATION_UNSUPPORTED
    )


@pytest.mark.parametrize("animated", [False, True])
def test_gif_is_always_unsupported(animated: bool) -> None:
    payload = _animated_image("GIF") if animated else _static_image("GIF")

    with pytest.raises(ImageSanitizationError) as exc_info:
        decode_bounded_image(BoundedImageBytes(payload))

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert exc_info.value.internal_code in {
        StorageInternalCode.IMAGE_CORRUPT,
        StorageInternalCode.IMAGE_ANIMATION_UNSUPPORTED,
    }


def test_jpeg_without_pillow_frame_attributes_uses_static_fallback() -> None:
    output = BytesIO()
    Image.new("RGB", (3, 2), (1, 2, 3)).save(output, format="JPEG")
    decoded = decode_bounded_image(BoundedImageBytes(output.getvalue()))
    try:
        image = decoded.as_internal_image()
        assert not hasattr(image, "n_frames")
        assert not hasattr(image, "is_animated")
        assert decoded.format_details.image_format is CanonicalImageFormat.JPEG
    finally:
        decoded.close()


def test_decoder_contains_no_frame_iteration_or_image_seek_fallback() -> None:
    source = inspect.getsource(image_module.decode_bounded_image)
    assert "ImageSequence" not in source
    assert ".seek(" not in source
