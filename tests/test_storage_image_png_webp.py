from collections.abc import Callable
from io import BytesIO

import pytest
from PIL import ExifTags, Image, PngImagePlugin

from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    BoundedImageBytes,
    CanonicalPixelImage,
    EncodedCanonicalImage,
    ImageSanitizationError,
    build_canonical_pixel_image,
    decode_bounded_image,
    encode_png_image,
    encode_webp_image,
    orient_decoded_image,
    verify_reopened_metadata_absence,
)

SENSITIVE_METADATA = "private-customer-337-png-webp-metadata"
Encoder = Callable[..., EncodedCanonicalImage]


def _source_image(
    image_format: str,
    *,
    mode: str,
    color,
    metadata: bool = False,
) -> bytes:
    image = Image.new(mode, (5, 4), color)
    output = BytesIO()
    save_options: dict[str, object] = {}
    if image_format == "WEBP":
        save_options["lossless"] = True
    if metadata:
        exif = Image.Exif()
        exif[ExifTags.Base.ImageDescription] = SENSITIVE_METADATA
        save_options.update(
            {
                "exif": exif,
                "icc_profile": SENSITIVE_METADATA.encode("ascii"),
            }
        )
        if image_format == "PNG":
            png_info = PngImagePlugin.PngInfo()
            png_info.add_text("Comment", SENSITIVE_METADATA)
            png_info.add_itxt("XML:com.adobe.xmp", SENSITIVE_METADATA)
            save_options["pnginfo"] = png_info
        else:
            save_options["xmp"] = SENSITIVE_METADATA.encode("ascii")

    image.save(output, format=image_format, **save_options)
    image.close()
    return output.getvalue()


def _canonical(payload: bytes) -> CanonicalPixelImage:
    decoded = decode_bounded_image(BoundedImageBytes(payload))
    oriented = orient_decoded_image(decoded)
    try:
        return build_canonical_pixel_image(oriented)
    finally:
        oriented.close()
        decoded.close()


@pytest.mark.parametrize(
    ("image_format", "encoder", "expected_options"),
    [
        (
            "PNG",
            encode_png_image,
            {"optimize": True, "compress_level": 9},
        ),
        (
            "WEBP",
            encode_webp_image,
            {"lossless": True, "method": 6},
        ),
    ],
)
def test_png_and_webp_fixed_encoder_arguments_are_exact(
    monkeypatch: pytest.MonkeyPatch,
    image_format: str,
    encoder: Encoder,
    expected_options: dict[str, object],
) -> None:
    canonical = _canonical(
        _source_image(
            image_format,
            mode="RGBA",
            color=(10, 20, 30, 77),
        )
    )
    original_save = Image.Image.save
    calls: list[tuple[str | None, dict[str, object]]] = []

    def recording_save(
        self: Image.Image,
        fp,
        format: str | None = None,
        **params: object,
    ) -> None:
        calls.append((format, params))
        original_save(self, fp, format=format, **params)

    monkeypatch.setattr(Image.Image, "save", recording_save)
    try:
        encoded = encoder(canonical)
    finally:
        canonical.close()

    assert calls == [(image_format, expected_options)]
    assert encoded.format_details.image_format.value == image_format


@pytest.mark.parametrize(
    ("image_format", "encoder"),
    [("PNG", encode_png_image), ("WEBP", encode_webp_image)],
)
@pytest.mark.parametrize(
    ("mode", "color"),
    [
        ("RGB", (10, 20, 30)),
        ("RGBA", (10, 20, 30, 77)),
    ],
)
def test_transparent_and_opaque_pixels_round_trip_exactly(
    image_format: str,
    encoder: Encoder,
    mode: str,
    color,
) -> None:
    canonical = _canonical(_source_image(image_format, mode=mode, color=color))
    try:
        expected_pixels = canonical.as_internal_image().tobytes()
        encoded = encoder(canonical)
    finally:
        canonical.close()

    with Image.open(BytesIO(encoded.encoded_bytes.as_internal_bytes())) as reopened:
        reopened.load()
        assert reopened.format == image_format
        assert reopened.mode == mode
        assert reopened.size == (5, 4)
        assert reopened.tobytes() == expected_pixels
        if mode == "RGBA":
            assert reopened.getpixel((0, 0))[3] == 77
        verify_reopened_metadata_absence(reopened)


@pytest.mark.parametrize(
    ("image_format", "encoder"),
    [("PNG", encode_png_image), ("WEBP", encode_webp_image)],
)
def test_png_and_webp_source_metadata_is_absent_after_reopen(
    image_format: str,
    encoder: Encoder,
) -> None:
    source = _source_image(
        image_format,
        mode="RGBA",
        color=(1, 2, 3, 77),
        metadata=True,
    )
    assert SENSITIVE_METADATA.encode("ascii") in source
    canonical = _canonical(source)
    try:
        encoded = encoder(canonical)
    finally:
        canonical.close()

    output_bytes = encoded.encoded_bytes.as_internal_bytes()
    assert SENSITIVE_METADATA.encode("ascii") not in output_bytes
    with Image.open(BytesIO(output_bytes)) as reopened:
        reopened.load()
        verify_reopened_metadata_absence(reopened)
        assert len(reopened.getexif()) == 0
        assert "icc_profile" not in reopened.info
        assert "xmp" not in reopened.info
        assert "XML:com.adobe.xmp" not in reopened.info
        assert "comment" not in reopened.info


@pytest.mark.parametrize(
    ("image_format", "encoder"),
    [("PNG", encode_png_image), ("WEBP", encode_webp_image)],
)
def test_png_and_webp_are_deterministic_in_current_codec(
    image_format: str,
    encoder: Encoder,
) -> None:
    canonical = _canonical(
        _source_image(
            image_format,
            mode="RGBA",
            color=(1, 2, 3, 77),
        )
    )
    try:
        first = encoder(canonical)
        second = encoder(canonical)
    finally:
        canonical.close()

    assert (
        first.encoded_bytes.as_internal_bytes()
        == second.encoded_bytes.as_internal_bytes()
    )


@pytest.mark.parametrize(
    ("image_format", "encoder"),
    [("PNG", encode_png_image), ("WEBP", encode_webp_image)],
)
def test_low_injected_output_limit_proves_exact_and_limit_plus_one(
    image_format: str,
    encoder: Encoder,
) -> None:
    canonical = _canonical(
        _source_image(
            image_format,
            mode="RGBA",
            color=(1, 2, 3, 77),
        )
    )
    try:
        baseline = encoder(canonical)
        exact_limit = baseline.size_bytes
        exact = encoder(canonical, max_output_bytes=exact_limit)
        with pytest.raises(ImageSanitizationError) as exc_info:
            encoder(canonical, max_output_bytes=exact_limit - 1)
    finally:
        canonical.close()

    assert exact.size_bytes == exact_limit
    assert exc_info.value.public_code is ErrorCode.FILE_TOO_LARGE
    assert (
        exc_info.value.internal_code is StorageInternalCode.SANITIZED_OUTPUT_TOO_LARGE
    )


@pytest.mark.parametrize(
    ("image_format", "encoder"),
    [("PNG", encode_png_image), ("WEBP", encode_webp_image)],
)
def test_animated_source_never_reaches_encoder(
    image_format: str,
    encoder: Encoder,
) -> None:
    first = Image.new("RGBA", (3, 2), (255, 0, 0, 255))
    second = Image.new("RGBA", (3, 2), (0, 0, 255, 255))
    output = BytesIO()
    save_options = {"lossless": True} if image_format == "WEBP" else {}
    first.save(
        output,
        format=image_format,
        save_all=True,
        append_images=[second],
        duration=100,
        loop=0,
        **save_options,
    )
    first.close()
    second.close()

    with pytest.raises(ImageSanitizationError) as exc_info:
        _canonical(output.getvalue())

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert (
        exc_info.value.internal_code is StorageInternalCode.IMAGE_ANIMATION_UNSUPPORTED
    )


def test_png_and_webp_encoders_reject_cross_family_conversion() -> None:
    png = _canonical(_source_image("PNG", mode="RGB", color=(1, 2, 3)))
    webp = _canonical(_source_image("WEBP", mode="RGB", color=(1, 2, 3)))
    try:
        with pytest.raises(ValueError):
            encode_webp_image(png)
        with pytest.raises(ValueError):
            encode_png_image(webp)
    finally:
        png.close()
        webp.close()
