from io import BytesIO

import pytest
from PIL import ExifTags, Image, PngImagePlugin
from PIL.TiffImagePlugin import IFDRational

from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    BoundedImageBytes,
    CanonicalPixelImage,
    ImageSanitizationError,
    build_canonical_pixel_image,
    decode_bounded_image,
    orient_decoded_image,
    verify_reopened_metadata_absence,
)

SENSITIVE_METADATA = "private-customer-884-metadata"


def _encoded(
    image_format: str,
    *,
    mode: str,
    color,
) -> bytes:
    output = BytesIO()
    save_options = {"lossless": True} if image_format == "WEBP" else {}
    Image.new(mode, (3, 2), color).save(
        output,
        format=image_format,
        **save_options,
    )
    return output.getvalue()


def _build(payload: bytes) -> CanonicalPixelImage:
    decoded = decode_bounded_image(BoundedImageBytes(payload))
    oriented = orient_decoded_image(decoded)
    try:
        return build_canonical_pixel_image(oriented)
    finally:
        oriented.close()
        decoded.close()


@pytest.mark.parametrize(
    ("image_format", "mode", "color", "expected_mode"),
    [
        ("JPEG", "CMYK", (10, 20, 30, 40), "RGB"),
        ("PNG", "RGB", (10, 20, 30), "RGB"),
        ("PNG", "RGBA", (10, 20, 30, 40), "RGBA"),
        ("WEBP", "RGB", (10, 20, 30), "RGB"),
        ("WEBP", "RGBA", (10, 20, 30, 40), "RGBA"),
    ],
)
def test_format_family_gets_exact_canonical_mode_and_fresh_pixels(
    image_format: str,
    mode: str,
    color,
    expected_mode: str,
) -> None:
    canonical = _build(_encoded(image_format, mode=mode, color=color))
    try:
        image = canonical.as_internal_image()
        assert canonical.format_details.image_format.value == image_format
        assert canonical.mode == expected_mode
        assert image.mode == expected_mode
        assert image.info == {}
        assert len(image.getexif()) == 0
        assert "image=<redacted>" in repr(canonical)
    finally:
        canonical.close()


@pytest.mark.parametrize(
    ("source_mode", "transparency", "expected_mode", "expected_alpha"),
    [
        ("P", None, "RGB", None),
        ("P", 0, "RGBA", 0),
        ("LA", None, "RGBA", 64),
    ],
)
def test_palette_la_and_palette_transparency_conversion(
    source_mode: str,
    transparency: int | None,
    expected_mode: str,
    expected_alpha: int | None,
) -> None:
    if source_mode == "P":
        source = Image.new("P", (2, 1))
        source.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
        source.putdata([0, 1])
        if transparency is not None:
            source.info["transparency"] = transparency
    else:
        source = Image.new("LA", (2, 1), (120, 64))

    output = BytesIO()
    source.save(output, format="PNG")
    source.close()
    canonical = _build(output.getvalue())
    try:
        image = canonical.as_internal_image()
        assert canonical.mode == expected_mode
        if expected_alpha is not None:
            assert image.getpixel((0, 0))[-1] == expected_alpha
    finally:
        canonical.close()


def _metadata_rich_png() -> bytes:
    image = Image.new("RGBA", (3, 2), (10, 20, 30, 128))
    exif = Image.Exif()
    exif[ExifTags.Base.ImageDescription] = SENSITIVE_METADATA
    exif[ExifTags.Base.Orientation] = 1
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    gps[ExifTags.GPS.GPSLatitudeRef] = "N"
    gps[ExifTags.GPS.GPSLatitude] = (
        IFDRational(41, 1),
        IFDRational(0, 1),
        IFDRational(0, 1),
    )
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("Comment", SENSITIVE_METADATA)
    png_info.add_itxt("XML:com.adobe.xmp", SENSITIVE_METADATA)
    png_info.add_text("private-text", SENSITIVE_METADATA)

    output = BytesIO()
    image.save(
        output,
        format="PNG",
        exif=exif,
        icc_profile=SENSITIVE_METADATA.encode("ascii"),
        pnginfo=png_info,
    )
    image.close()
    return output.getvalue()


def test_exif_gps_xmp_icc_comment_thumbnail_and_text_never_reach_fresh_output() -> None:
    payload = _metadata_rich_png()
    decoded = decode_bounded_image(BoundedImageBytes(payload))
    source_image = decoded.as_internal_image()
    assert source_image.getexif()
    assert source_image.getexif().get_ifd(ExifTags.IFD.GPSInfo)
    assert source_image.info["icc_profile"]
    assert source_image.text

    oriented = orient_decoded_image(decoded)
    oriented_image = oriented.as_internal_image()
    oriented_image.info["thumbnail"] = SENSITIVE_METADATA.encode("ascii")
    canonical = build_canonical_pixel_image(oriented)
    try:
        fresh_image = canonical.as_internal_image()
        assert fresh_image.info == {}
        assert len(fresh_image.getexif()) == 0

        output = BytesIO()
        fresh_image.save(output, format="PNG")
        output_bytes = output.getvalue()
        assert SENSITIVE_METADATA.encode("ascii") not in output_bytes

        with Image.open(BytesIO(output_bytes)) as reopened:
            reopened.load()
            verify_reopened_metadata_absence(reopened)
            assert reopened.info == {}
            assert reopened.text == {}
    finally:
        canonical.close()
        oriented.close()
        decoded.close()


@pytest.mark.parametrize("metadata_kind", ["exif", "comment", "text"])
def test_output_metadata_verifier_fails_closed_without_value_leak(
    metadata_kind: str,
) -> None:
    image = Image.new("RGB", (2, 2), (1, 2, 3))
    if metadata_kind == "exif":
        image.getexif()[ExifTags.Base.ImageDescription] = SENSITIVE_METADATA
    elif metadata_kind == "comment":
        image.info["comment"] = SENSITIVE_METADATA
    else:
        image.text = {"private": SENSITIVE_METADATA}  # type: ignore[attr-defined]

    with pytest.raises(ImageSanitizationError) as exc_info:
        verify_reopened_metadata_absence(image)
    image.close()

    assert exc_info.value.public_code is ErrorCode.UNSUPPORTED_FILE_TYPE
    assert exc_info.value.internal_code is StorageInternalCode.IMAGE_CORRUPT
    assert SENSITIVE_METADATA not in f"{exc_info.value!s} {exc_info.value!r}"


@pytest.mark.parametrize(
    ("image_format", "mode"),
    [("JPEG", "RGB"), ("PNG", "RGBA"), ("WEBP", "RGBA")],
)
def test_fresh_pixel_image_can_be_reopened_without_sensitive_metadata(
    image_format: str,
    mode: str,
) -> None:
    canonical = _build(
        _encoded(
            image_format,
            mode=mode,
            color=(1, 2, 3, 77) if mode == "RGBA" else (1, 2, 3),
        )
    )
    try:
        output = BytesIO()
        save_options = {"lossless": True} if image_format == "WEBP" else {}
        canonical.as_internal_image().save(
            output,
            format=image_format,
            **save_options,
        )
        with Image.open(BytesIO(output.getvalue())) as reopened:
            reopened.load()
            verify_reopened_metadata_absence(reopened)
    finally:
        canonical.close()
