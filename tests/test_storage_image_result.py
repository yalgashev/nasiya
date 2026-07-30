import inspect
from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image

import app.storage.image as image_module
from app.storage.contracts import (
    ObjectChecksumSha256,
    SanitizedImage,
    SanitizedImageBytes,
)
from app.storage.image import (
    CANONICAL_IMAGE_FORMATS,
    BoundedImageBytes,
    EncodedCanonicalImage,
    finalize_sanitized_image,
    generate_object_key,
    sanitize_bounded_image,
    verify_reopened_metadata_absence,
)

GOLDEN_BYTES = b"m8-canonical-sanitized-bytes"
GOLDEN_CHECKSUM = "b127ed04a10d7e799493693c8a2ef9ef0fd1e75e7a94da66d5e76bef15b278e8"
FIXED_UUID = UUID("123e4567-e89b-42d3-a456-426614174000")
SECOND_UUID = UUID("ffeeddcc-bbaa-4988-8776-554433221100")
SENSITIVE_FILENAME = "private-customer-665-passport.png"
SENSITIVE_CLAIMED_MIME = "application/private-customer-665"


def _encoded_fixture(image_format: str) -> bytes:
    mode = "RGBA" if image_format in {"PNG", "WEBP"} else "RGB"
    color = (10, 20, 30, 77) if mode == "RGBA" else (10, 20, 30)
    output = BytesIO()
    save_options = {"lossless": True} if image_format == "WEBP" else {}
    Image.new(mode, (4, 3), color).save(
        output,
        format=image_format,
        **save_options,
    )
    return output.getvalue()


def test_golden_checksum_is_lowercase_sha256_of_exact_sanitized_bytes() -> None:
    encoded = EncodedCanonicalImage(
        format_details=CANONICAL_IMAGE_FORMATS["PNG"],
        mode="RGB",
        width_px=2,
        height_px=2,
        encoded_bytes=SanitizedImageBytes(GOLDEN_BYTES),
    )

    result = finalize_sanitized_image(encoded)

    assert isinstance(result, SanitizedImage)
    assert result.metadata.checksum_sha256 == ObjectChecksumSha256(GOLDEN_CHECKSUM)
    assert result.metadata.checksum_sha256.as_internal_value() == GOLDEN_CHECKSUM
    assert result.sanitized_bytes.as_internal_bytes() == GOLDEN_BYTES
    assert result.metadata.size_bytes == len(GOLDEN_BYTES)


@pytest.mark.parametrize(
    ("image_format", "content_type", "extension"),
    [
        ("JPEG", "image/jpeg", "jpg"),
        ("PNG", "image/png", "png"),
        ("WEBP", "image/webp", "webp"),
    ],
)
def test_full_sanitizer_returns_only_canonical_typed_metadata(
    image_format: str,
    content_type: str,
    extension: str,
) -> None:
    result = sanitize_bounded_image(BoundedImageBytes(_encoded_fixture(image_format)))

    assert result.metadata.content_type == content_type
    assert result.metadata.canonical_extension == extension
    assert result.metadata.size_bytes == len(result.sanitized_bytes)
    assert (result.metadata.width_px, result.metadata.height_px) == (4, 3)
    assert len(result.metadata.checksum_sha256.as_internal_value()) == 64
    assert result.metadata.checksum_sha256.as_internal_value().islower()


@pytest.mark.parametrize(
    ("image_format", "expected_mode"),
    [
        ("JPEG", "RGB"),
        ("PNG", "RGBA"),
        ("WEBP", "RGBA"),
    ],
)
def test_verified_output_full_decodes_twice_as_static_metadata_free_image(
    image_format: str,
    expected_mode: str,
) -> None:
    result = sanitize_bounded_image(BoundedImageBytes(_encoded_fixture(image_format)))
    output = result.sanitized_bytes.as_internal_bytes()

    for _decode_number in range(2):
        with Image.open(BytesIO(output)) as reopened:
            reopened.load()
            assert reopened.format == image_format
            assert reopened.mode == expected_mode
            assert reopened.size == (4, 3)
            assert getattr(reopened, "n_frames", 1) == 1
            assert getattr(reopened, "is_animated", False) is False
            verify_reopened_metadata_absence(reopened)


def test_object_key_has_exact_golden_uuid4_shape() -> None:
    key = generate_object_key("png", uuid_factory=lambda: FIXED_UUID)

    assert key.as_internal_value() == "v1/objects/123e4567e89b42d3a456426614174000.png"


@pytest.mark.parametrize("extension", ["jpg", "png", "webp"])
def test_same_uuid_and_format_is_deterministic_and_different_uuid_is_unique(
    extension: str,
) -> None:
    first = generate_object_key(extension, uuid_factory=lambda: FIXED_UUID)
    repeated = generate_object_key(
        extension,
        uuid_factory=lambda: FIXED_UUID,
    )
    different = generate_object_key(
        extension,
        uuid_factory=lambda: SECOND_UUID,
    )

    assert first.as_internal_value() == repeated.as_internal_value()
    assert first.as_internal_value() != different.as_internal_value()


@pytest.mark.parametrize(
    "invalid_factory",
    [
        lambda: UUID("123e4567-e89b-12d3-a456-426614174000"),
        lambda: "not-a-uuid",
    ],
)
def test_object_key_generator_requires_injected_uuid4(
    invalid_factory,
) -> None:
    with pytest.raises(ValueError):
        generate_object_key("jpg", uuid_factory=invalid_factory)


def test_key_result_checksum_and_bytes_are_redacted_by_default() -> None:
    result = sanitize_bounded_image(BoundedImageBytes(_encoded_fixture("PNG")))
    key = generate_object_key("png", uuid_factory=lambda: FIXED_UUID)
    raw_bytes = result.sanitized_bytes.as_internal_bytes()
    raw_checksum = result.metadata.checksum_sha256.as_internal_value()
    raw_key = key.as_internal_value()

    rendered = f"{result!s} {result!r} {key!s} {key!r}"

    assert raw_bytes.hex() not in rendered
    assert raw_checksum not in rendered
    assert raw_key not in rendered
    assert SENSITIVE_FILENAME not in rendered
    assert SENSITIVE_CLAIMED_MIME not in rendered


def test_sanitizer_and_key_api_have_no_filename_or_claimed_mime_input() -> None:
    sanitizer_signature = inspect.signature(image_module.sanitize_bounded_image)
    key_signature = inspect.signature(image_module.generate_object_key)

    assert "filename" not in sanitizer_signature.parameters
    assert "content_type" not in sanitizer_signature.parameters
    assert "mime" not in sanitizer_signature.parameters
    assert "filename" not in key_signature.parameters
    assert "content_type" not in key_signature.parameters


def test_invalid_extension_is_rejected_before_uuid_generation() -> None:
    calls = 0

    def uuid_factory() -> UUID:
        nonlocal calls
        calls += 1
        return FIXED_UUID

    with pytest.raises(ValueError):
        generate_object_key("jpeg", uuid_factory=uuid_factory)

    assert calls == 0
