from io import BytesIO

import pytest
from PIL import ExifTags, Image
from PIL.TiffImagePlugin import IFDRational

from app.auth.error_codes import ErrorCode
from app.storage.errors import StorageInternalCode
from app.storage.image import (
    CANONICAL_IMAGE_FORMATS,
    BoundedImageBytes,
    CanonicalPixelImage,
    EncodedCanonicalImage,
    ImageSanitizationError,
    OrientedImage,
    build_canonical_pixel_image,
    decode_bounded_image,
    encode_jpeg_image,
    orient_decoded_image,
    verify_reopened_metadata_absence,
)

SENSITIVE_METADATA = "private-customer-229-jpeg-metadata"


def _jpeg(
    *,
    mode: str = "RGB",
    color=(10, 20, 30),
    metadata: bool = False,
) -> bytes:
    image = Image.new(mode, (8, 6), color)
    output = BytesIO()
    save_options: dict[str, object] = {}
    if metadata:
        exif = Image.Exif()
        exif[ExifTags.Base.ImageDescription] = SENSITIVE_METADATA
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps[ExifTags.GPS.GPSLatitudeRef] = "N"
        gps[ExifTags.GPS.GPSLatitude] = (
            IFDRational(41, 1),
            IFDRational(0, 1),
            IFDRational(0, 1),
        )
        save_options = {
            "exif": exif,
            "icc_profile": SENSITIVE_METADATA.encode("ascii"),
            "comment": SENSITIVE_METADATA.encode("ascii"),
            "xmp": SENSITIVE_METADATA.encode("ascii"),
        }
    image.save(output, format="JPEG", **save_options)
    image.close()
    return output.getvalue()


def _canonical_from_jpeg(payload: bytes) -> CanonicalPixelImage:
    decoded = decode_bounded_image(BoundedImageBytes(payload))
    oriented = orient_decoded_image(decoded)
    try:
        return build_canonical_pixel_image(oriented)
    finally:
        oriented.close()
        decoded.close()


def _encode(payload: bytes) -> EncodedCanonicalImage:
    canonical = _canonical_from_jpeg(payload)
    try:
        return encode_jpeg_image(canonical)
    finally:
        canonical.close()


def test_jpeg_fixed_encoder_arguments_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = _canonical_from_jpeg(_jpeg())
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
        encoded = encode_jpeg_image(canonical)
    finally:
        canonical.close()

    assert calls == [
        (
            "JPEG",
            {
                "quality": 90,
                "optimize": True,
                "progressive": False,
            },
        )
    ]
    assert encoded.format_details == CANONICAL_IMAGE_FORMATS["JPEG"]
    assert encoded.mode == "RGB"


@pytest.mark.parametrize(
    ("mode", "color"),
    [
        ("RGB", (10, 20, 30)),
        ("CMYK", (10, 20, 30, 40)),
    ],
)
def test_rgb_and_cmyk_jpeg_reencode_to_rgb_without_resize(
    mode: str,
    color,
) -> None:
    encoded = _encode(_jpeg(mode=mode, color=color))
    output_bytes = encoded.encoded_bytes.as_internal_bytes()

    assert encoded.mode == "RGB"
    assert (encoded.width_px, encoded.height_px) == (8, 6)
    assert encoded.size_bytes == len(output_bytes)
    with Image.open(BytesIO(output_bytes)) as reopened:
        reopened.load()
        assert reopened.format == "JPEG"
        assert reopened.mode == "RGB"
        assert reopened.size == (8, 6)
        verify_reopened_metadata_absence(reopened)


def test_palette_pixels_are_converted_to_rgb_before_jpeg_encode() -> None:
    palette = Image.new("P", (3, 2))
    palette.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    palette.putdata([0, 1, 0, 1, 0, 1])
    oriented = OrientedImage(
        format_details=CANONICAL_IMAGE_FORMATS["JPEG"],
        width_px=3,
        height_px=2,
        _image=palette,
    )
    canonical = build_canonical_pixel_image(oriented)
    try:
        assert canonical.mode == "RGB"
        encoded = encode_jpeg_image(canonical)
    finally:
        canonical.close()
        oriented.close()

    with Image.open(BytesIO(encoded.encoded_bytes.as_internal_bytes())) as reopened:
        reopened.load()
        assert reopened.mode == "RGB"
        assert reopened.size == (3, 2)


def test_source_metadata_is_absent_after_jpeg_reopen() -> None:
    source = _jpeg(metadata=True)
    assert SENSITIVE_METADATA.encode("ascii") in source

    encoded = _encode(source)
    output_bytes = encoded.encoded_bytes.as_internal_bytes()

    assert SENSITIVE_METADATA.encode("ascii") not in output_bytes
    with Image.open(BytesIO(output_bytes)) as reopened:
        reopened.load()
        verify_reopened_metadata_absence(reopened)
        assert len(reopened.getexif()) == 0
        assert "icc_profile" not in reopened.info
        assert "comment" not in reopened.info
        assert "xmp" not in reopened.info


def test_jpeg_encoding_is_deterministic_in_current_codec_environment() -> None:
    canonical = _canonical_from_jpeg(_jpeg())
    try:
        first = encode_jpeg_image(canonical)
        second = encode_jpeg_image(canonical)
    finally:
        canonical.close()

    assert (
        first.encoded_bytes.as_internal_bytes()
        == second.encoded_bytes.as_internal_bytes()
    )


def test_exact_output_size_is_allowed_and_one_less_is_file_too_large() -> None:
    canonical = _canonical_from_jpeg(_jpeg())
    try:
        baseline = encode_jpeg_image(canonical)
        exact_limit = baseline.size_bytes
        exact = encode_jpeg_image(
            canonical,
            max_output_bytes=exact_limit,
        )
        with pytest.raises(ImageSanitizationError) as exc_info:
            encode_jpeg_image(
                canonical,
                max_output_bytes=exact_limit - 1,
            )
    finally:
        canonical.close()

    assert exact.size_bytes == exact_limit
    assert exc_info.value.public_code is ErrorCode.FILE_TOO_LARGE
    assert (
        exc_info.value.internal_code is StorageInternalCode.SANITIZED_OUTPUT_TOO_LARGE
    )


def test_jpeg_encoder_rejects_wrong_family_without_fallback() -> None:
    image = Image.new("RGB", (2, 2), (1, 2, 3))
    canonical = CanonicalPixelImage(
        format_details=CANONICAL_IMAGE_FORMATS["PNG"],
        mode="RGB",
        width_px=2,
        height_px=2,
        _image=image,
    )
    try:
        with pytest.raises(ValueError):
            encode_jpeg_image(canonical)
    finally:
        canonical.close()


def test_encoded_wrapper_hides_bytes_and_metadata() -> None:
    encoded = _encode(_jpeg())
    rendered = f"{encoded!s} {encoded!r}"
    assert "encoded_bytes=<redacted>" in rendered
    assert SENSITIVE_METADATA not in rendered
