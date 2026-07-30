import hashlib
import warnings
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from types import MappingProxyType
from typing import Final, Protocol
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.auth.error_codes import ErrorCode
from app.storage.contracts import (
    ObjectChecksumSha256,
    ObjectKey,
    SanitizedImage,
    SanitizedImageBytes,
    SanitizedImageMetadata,
)
from app.storage.errors import (
    StorageInternalCode,
    get_storage_public_error_code,
)

MAX_SOURCE_IMAGE_BYTES: Final = 10_485_760
SOURCE_READ_CHUNK_BYTES: Final = 65_536
MAX_IMAGE_DIMENSION: Final = 16_384
MAX_IMAGE_PIXELS: Final = 40_000_000


@dataclass(frozen=True)
class ImageDimensionLimits:
    max_dimension: int = MAX_IMAGE_DIMENSION
    max_pixels: int = MAX_IMAGE_PIXELS

    def __post_init__(self) -> None:
        if (
            self.max_dimension < 1
            or self.max_dimension > MAX_IMAGE_DIMENSION
            or self.max_pixels < 1
            or self.max_pixels > MAX_IMAGE_PIXELS
        ):
            raise ValueError("Image limits must not exceed frozen bounds")


DEFAULT_IMAGE_DIMENSION_LIMITS: Final = ImageDimensionLimits()
FORBIDDEN_OUTPUT_METADATA_KEYS: Final = frozenset(
    {
        "comment",
        "exif",
        "gps",
        "icc_profile",
        "iptc",
        "photoshop",
        "raw profile type exif",
        "thumbnail",
        "xmp",
        "xml:com.adobe.xmp",
    }
)


class CanonicalImageFormat(StrEnum):
    JPEG = "JPEG"
    PNG = "PNG"
    WEBP = "WEBP"


@dataclass(frozen=True)
class CanonicalImageFormatDetails:
    image_format: CanonicalImageFormat
    content_type: str
    canonical_extension: str


CANONICAL_IMAGE_FORMATS: Final[Mapping[str, CanonicalImageFormatDetails]] = (
    MappingProxyType(
        {
            CanonicalImageFormat.JPEG.value: CanonicalImageFormatDetails(
                image_format=CanonicalImageFormat.JPEG,
                content_type="image/jpeg",
                canonical_extension="jpg",
            ),
            CanonicalImageFormat.PNG.value: CanonicalImageFormatDetails(
                image_format=CanonicalImageFormat.PNG,
                content_type="image/png",
                canonical_extension="png",
            ),
            CanonicalImageFormat.WEBP.value: CanonicalImageFormatDetails(
                image_format=CanonicalImageFormat.WEBP,
                content_type="image/webp",
                canonical_extension="webp",
            ),
        }
    )
)


class AsyncImageSource(Protocol):
    async def seek(self, offset: int) -> None: ...

    async def read(self, size: int) -> bytes: ...


class ImageSanitizationError(RuntimeError):
    def __init__(
        self,
        *,
        public_code: ErrorCode,
        internal_code: StorageInternalCode | None = None,
    ) -> None:
        if public_code not in {
            ErrorCode.FILE_STORAGE_ERROR,
            ErrorCode.FILE_TOO_LARGE,
            ErrorCode.UNSUPPORTED_FILE_TYPE,
        }:
            raise ValueError("Unsupported image public error code")
        if (
            internal_code is not None
            and get_storage_public_error_code(internal_code) is not public_code
        ):
            raise ValueError("Image internal/public error mapping does not match")

        self.public_code = public_code
        self.internal_code = internal_code
        super().__init__(public_code.value)

    def __repr__(self) -> str:
        internal = self.internal_code.value if self.internal_code else None
        return (
            "ImageSanitizationError("
            f"public_code={self.public_code.value!r}, "
            f"internal_code={internal!r}"
            ")"
        )

    @classmethod
    def from_internal(
        cls,
        internal_code: StorageInternalCode,
    ) -> "ImageSanitizationError":
        return cls(
            public_code=get_storage_public_error_code(internal_code),
            internal_code=internal_code,
        )


@dataclass(frozen=True, repr=False)
class BoundedImageBytes:
    _value: bytes

    def __post_init__(self) -> None:
        if (
            not isinstance(self._value, bytes)
            or not self._value
            or len(self._value) > MAX_SOURCE_IMAGE_BYTES
        ):
            raise ValueError("Invalid bounded image bytes")

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return f"BoundedImageBytes(<redacted>, size_bytes={len(self._value)})"

    def __str__(self) -> str:
        return "<redacted>"

    def as_internal_bytes(self) -> bytes:
        return self._value


@dataclass(repr=False)
class DecodedImage:
    format_details: CanonicalImageFormatDetails
    width_px: int
    height_px: int
    _image: Image.Image

    def __post_init__(self) -> None:
        if self.width_px <= 0 or self.height_px <= 0:
            raise ValueError("Decoded image dimensions must be positive")
        if self._image.size != (self.width_px, self.height_px):
            raise ValueError("Decoded image dimensions do not match payload")

    def __repr__(self) -> str:
        return (
            "DecodedImage("
            f"image_format={self.format_details.image_format.value!r}, "
            f"content_type={self.format_details.content_type!r}, "
            f"canonical_extension="
            f"{self.format_details.canonical_extension!r}, "
            f"width_px={self.width_px!r}, "
            f"height_px={self.height_px!r}, "
            "image=<redacted>"
            ")"
        )

    def as_internal_image(self) -> Image.Image:
        return self._image

    def close(self) -> None:
        self._image.close()


@dataclass(repr=False)
class OrientedImage:
    format_details: CanonicalImageFormatDetails
    width_px: int
    height_px: int
    _image: Image.Image

    def __post_init__(self) -> None:
        if self._image.size != (self.width_px, self.height_px):
            raise ValueError("Oriented image dimensions do not match payload")

    def __repr__(self) -> str:
        return (
            "OrientedImage("
            f"image_format={self.format_details.image_format.value!r}, "
            f"width_px={self.width_px!r}, "
            f"height_px={self.height_px!r}, "
            "image=<redacted>"
            ")"
        )

    def as_internal_image(self) -> Image.Image:
        return self._image

    def close(self) -> None:
        self._image.close()


@dataclass(repr=False)
class CanonicalPixelImage:
    format_details: CanonicalImageFormatDetails
    mode: str
    width_px: int
    height_px: int
    _image: Image.Image

    def __post_init__(self) -> None:
        allowed_modes = (
            {"RGB"}
            if self.format_details.image_format is CanonicalImageFormat.JPEG
            else {"RGB", "RGBA"}
        )
        if self.mode not in allowed_modes or self._image.mode != self.mode:
            raise ValueError("Canonical image mode is invalid")
        if self._image.size != (self.width_px, self.height_px):
            raise ValueError("Canonical image dimensions do not match payload")
        if self._image.info or len(self._image.getexif()) != 0:
            raise ValueError("Canonical image must not contain metadata")

    def __repr__(self) -> str:
        return (
            "CanonicalPixelImage("
            f"image_format={self.format_details.image_format.value!r}, "
            f"mode={self.mode!r}, "
            f"width_px={self.width_px!r}, "
            f"height_px={self.height_px!r}, "
            "image=<redacted>"
            ")"
        )

    def as_internal_image(self) -> Image.Image:
        return self._image

    def close(self) -> None:
        self._image.close()


@dataclass(frozen=True, repr=False)
class EncodedCanonicalImage:
    format_details: CanonicalImageFormatDetails
    mode: str
    width_px: int
    height_px: int
    encoded_bytes: SanitizedImageBytes

    def __post_init__(self) -> None:
        allowed_modes = (
            {"RGB"}
            if self.format_details.image_format is CanonicalImageFormat.JPEG
            else {"RGB", "RGBA"}
        )
        if self.mode not in allowed_modes or self.width_px <= 0 or self.height_px <= 0:
            raise ValueError("Encoded canonical image metadata is invalid")

    @property
    def size_bytes(self) -> int:
        return len(self.encoded_bytes)

    def __repr__(self) -> str:
        return (
            "EncodedCanonicalImage("
            f"image_format={self.format_details.image_format.value!r}, "
            f"mode={self.mode!r}, "
            f"size_bytes={self.size_bytes!r}, "
            f"width_px={self.width_px!r}, "
            f"height_px={self.height_px!r}, "
            "encoded_bytes=<redacted>"
            ")"
        )


class _OutputLimitExceeded(OSError):
    pass


class _CappedOutputWriter(BytesIO):
    def __init__(self, max_output_bytes: int) -> None:
        super().__init__()
        self._retained_limit = max_output_bytes + 1

    def write(self, data: bytes, /) -> int:
        if not isinstance(data, bytes):
            raise TypeError("encoded output writes must be bytes")

        current_position = self.tell()
        permitted_bytes = max(self._retained_limit - current_position, 0)
        if len(data) > permitted_bytes:
            if permitted_bytes:
                super().write(data[:permitted_bytes])
            raise _OutputLimitExceeded
        return super().write(data)


async def read_bounded_image(
    source: AsyncImageSource,
    *,
    max_bytes: int = MAX_SOURCE_IMAGE_BYTES,
) -> BoundedImageBytes:
    """Read a borrowed async source through max+1 without closing its owner."""

    if max_bytes < 1 or max_bytes > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError("max_bytes must be within the frozen source limit")

    try:
        await source.seek(0)
    except (OSError, RuntimeError, ValueError, TypeError):
        raise ImageSanitizationError(public_code=ErrorCode.FILE_STORAGE_ERROR) from None

    collected = bytearray()
    while len(collected) <= max_bytes:
        requested_bytes = min(
            SOURCE_READ_CHUNK_BYTES,
            max_bytes + 1 - len(collected),
        )
        try:
            chunk = await source.read(requested_bytes)
        except (OSError, RuntimeError, ValueError, TypeError):
            raise ImageSanitizationError(
                public_code=ErrorCode.FILE_STORAGE_ERROR
            ) from None

        if not isinstance(chunk, bytes) or len(chunk) > requested_bytes:
            raise ImageSanitizationError(public_code=ErrorCode.FILE_STORAGE_ERROR)
        if not chunk:
            break
        collected.extend(chunk)

    if len(collected) > max_bytes:
        raise ImageSanitizationError(public_code=ErrorCode.FILE_TOO_LARGE)
    if not collected:
        raise ImageSanitizationError.from_internal(StorageInternalCode.IMAGE_CORRUPT)
    return BoundedImageBytes(bytes(collected))


def decode_bounded_image(
    source: BoundedImageBytes,
    *,
    limits: ImageDimensionLimits = DEFAULT_IMAGE_DIMENSION_LIMITS,
) -> DecodedImage:
    """Verify, reopen, and fully decode an allowlisted image from bounded bytes."""

    raw_source = source.as_internal_bytes()
    try:
        with _fatal_decompression_warnings():
            with Image.open(BytesIO(raw_source)) as verification_image:
                format_details = _get_canonical_format_details(
                    verification_image.format
                )
                validate_image_dimensions(verification_image.size, limits)
                _validate_static_frame_policy(verification_image)
                verification_image.verify()
    except ImageSanitizationError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_PIXEL_LIMIT_EXCEEDED
        ) from None
    except UnidentifiedImageError:
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None
    except OSError:
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_TRUNCATED
        ) from None
    except (SyntaxError, ValueError, TypeError):
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None

    decode_stream = BytesIO(raw_source)
    decoded_image: Image.Image | None = None
    try:
        with _fatal_decompression_warnings():
            decoded_image = Image.open(decode_stream)
            reopened_details = _get_canonical_format_details(decoded_image.format)
            if reopened_details != format_details:
                raise ImageSanitizationError.from_internal(
                    StorageInternalCode.IMAGE_CORRUPT
                )
            validate_image_dimensions(decoded_image.size, limits)
            _validate_static_frame_policy(decoded_image)
            decoded_image.load()
            validate_image_dimensions(decoded_image.size, limits)
            _validate_static_frame_policy(decoded_image)
            width_px, height_px = decoded_image.size
    except ImageSanitizationError:
        if decoded_image is not None:
            decoded_image.close()
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        if decoded_image is not None:
            decoded_image.close()
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_PIXEL_LIMIT_EXCEEDED
        ) from None
    except UnidentifiedImageError:
        if decoded_image is not None:
            decoded_image.close()
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None
    except OSError:
        if decoded_image is not None:
            decoded_image.close()
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_TRUNCATED
        ) from None
    except (SyntaxError, ValueError, TypeError):
        if decoded_image is not None:
            decoded_image.close()
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None
    finally:
        decode_stream.close()

    assert decoded_image is not None
    return DecodedImage(
        format_details=format_details,
        width_px=width_px,
        height_px=height_px,
        _image=decoded_image,
    )


def orient_decoded_image(
    decoded: DecodedImage,
    *,
    limits: ImageDimensionLimits = DEFAULT_IMAGE_DIMENSION_LIMITS,
) -> OrientedImage:
    oriented_image: Image.Image | None = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            oriented_image = ImageOps.exif_transpose(decoded.as_internal_image())
        if not isinstance(oriented_image, Image.Image):
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.IMAGE_CORRUPT
            )
        oriented_image.getexif().clear()
        oriented_image.info.pop("exif", None)
        validate_image_dimensions(oriented_image.size, limits)
        width_px, height_px = oriented_image.size
    except ImageSanitizationError:
        if oriented_image is not None:
            oriented_image.close()
        raise
    except (OSError, SyntaxError, TypeError, UserWarning, ValueError):
        if oriented_image is not None:
            oriented_image.close()
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None

    return OrientedImage(
        format_details=decoded.format_details,
        width_px=width_px,
        height_px=height_px,
        _image=oriented_image,
    )


def build_canonical_pixel_image(oriented: OrientedImage) -> CanonicalPixelImage:
    source_image = oriented.as_internal_image()
    if oriented.format_details.image_format is CanonicalImageFormat.JPEG:
        canonical_mode = "RGB"
    else:
        canonical_mode = "RGBA" if source_image.has_transparency_data else "RGB"

    converted_image: Image.Image | None = None
    fresh_image: Image.Image | None = None
    try:
        converted_image = source_image.convert(canonical_mode)
        raw_pixels = converted_image.tobytes()
        fresh_image = Image.frombytes(
            canonical_mode,
            converted_image.size,
            raw_pixels,
        )
        if fresh_image.info or len(fresh_image.getexif()) != 0:
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.IMAGE_CORRUPT
            )
        width_px, height_px = fresh_image.size
    except ImageSanitizationError:
        if fresh_image is not None:
            fresh_image.close()
        raise
    except (OSError, TypeError, ValueError):
        if fresh_image is not None:
            fresh_image.close()
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None
    finally:
        if converted_image is not None:
            converted_image.close()

    return CanonicalPixelImage(
        format_details=oriented.format_details,
        mode=canonical_mode,
        width_px=width_px,
        height_px=height_px,
        _image=fresh_image,
    )


def encode_jpeg_image(
    canonical: CanonicalPixelImage,
    *,
    max_output_bytes: int = MAX_SOURCE_IMAGE_BYTES,
) -> EncodedCanonicalImage:
    if canonical.format_details.image_format is not CanonicalImageFormat.JPEG:
        raise ValueError("JPEG encoder requires canonical JPEG input")
    if canonical.mode != "RGB":
        raise ValueError("JPEG encoder requires RGB input")

    return _encode_canonical_image(
        canonical,
        max_output_bytes=max_output_bytes,
        save_options={
            "format": "JPEG",
            "quality": 90,
            "optimize": True,
            "progressive": False,
        },
    )


def encode_png_image(
    canonical: CanonicalPixelImage,
    *,
    max_output_bytes: int = MAX_SOURCE_IMAGE_BYTES,
) -> EncodedCanonicalImage:
    if canonical.format_details.image_format is not CanonicalImageFormat.PNG:
        raise ValueError("PNG encoder requires canonical PNG input")

    return _encode_canonical_image(
        canonical,
        max_output_bytes=max_output_bytes,
        save_options={
            "format": "PNG",
            "optimize": True,
            "compress_level": 9,
        },
    )


def encode_webp_image(
    canonical: CanonicalPixelImage,
    *,
    max_output_bytes: int = MAX_SOURCE_IMAGE_BYTES,
) -> EncodedCanonicalImage:
    if canonical.format_details.image_format is not CanonicalImageFormat.WEBP:
        raise ValueError("WebP encoder requires canonical WebP input")

    return _encode_canonical_image(
        canonical,
        max_output_bytes=max_output_bytes,
        save_options={
            "format": "WEBP",
            "lossless": True,
            "method": 6,
        },
    )


def finalize_sanitized_image(
    encoded: EncodedCanonicalImage,
) -> SanitizedImage:
    exact_bytes = encoded.encoded_bytes.as_internal_bytes()
    checksum = ObjectChecksumSha256(hashlib.sha256(exact_bytes).hexdigest())
    metadata = SanitizedImageMetadata(
        content_type=encoded.format_details.content_type,
        canonical_extension=encoded.format_details.canonical_extension,
        size_bytes=encoded.size_bytes,
        width_px=encoded.width_px,
        height_px=encoded.height_px,
        checksum_sha256=checksum,
    )
    return SanitizedImage(
        metadata=metadata,
        sanitized_bytes=encoded.encoded_bytes,
    )


def sanitize_bounded_image(
    source: BoundedImageBytes,
    *,
    limits: ImageDimensionLimits = DEFAULT_IMAGE_DIMENSION_LIMITS,
    max_output_bytes: int = MAX_SOURCE_IMAGE_BYTES,
) -> SanitizedImage:
    decoded: DecodedImage | None = None
    oriented: OrientedImage | None = None
    canonical: CanonicalPixelImage | None = None
    try:
        decoded = decode_bounded_image(source, limits=limits)
        oriented = orient_decoded_image(decoded, limits=limits)
        canonical = build_canonical_pixel_image(oriented)
        if canonical.format_details.image_format is CanonicalImageFormat.JPEG:
            encoded = encode_jpeg_image(
                canonical,
                max_output_bytes=max_output_bytes,
            )
        elif canonical.format_details.image_format is CanonicalImageFormat.PNG:
            encoded = encode_png_image(
                canonical,
                max_output_bytes=max_output_bytes,
            )
        else:
            encoded = encode_webp_image(
                canonical,
                max_output_bytes=max_output_bytes,
            )
        return finalize_sanitized_image(encoded)
    finally:
        if canonical is not None:
            canonical.close()
        if oriented is not None:
            oriented.close()
        if decoded is not None:
            decoded.close()


def generate_object_key(
    canonical_extension: str,
    *,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> ObjectKey:
    if canonical_extension not in {"jpg", "png", "webp"}:
        raise ValueError("Object key requires a canonical image extension")
    object_id = uuid_factory()
    if not isinstance(object_id, UUID) or object_id.version != 4:
        raise ValueError("Object key generator must return UUID4")
    return ObjectKey(f"v1/objects/{object_id.hex}.{canonical_extension}")


def _encode_canonical_image(
    canonical: CanonicalPixelImage,
    *,
    max_output_bytes: int,
    save_options: Mapping[str, object],
) -> EncodedCanonicalImage:
    if max_output_bytes < 1 or max_output_bytes > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError("max_output_bytes must be within the frozen limit")

    writer = _CappedOutputWriter(max_output_bytes)
    try:
        try:
            canonical.as_internal_image().save(writer, **save_options)
            encoded_bytes = writer.getvalue()
        except _OutputLimitExceeded:
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.SANITIZED_OUTPUT_TOO_LARGE
            ) from None
        except (OSError, TypeError, ValueError):
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.IMAGE_CORRUPT
            ) from None
    finally:
        writer.close()

    if len(encoded_bytes) > max_output_bytes:
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.SANITIZED_OUTPUT_TOO_LARGE
        )

    _verify_encoded_image(encoded_bytes, canonical)
    return EncodedCanonicalImage(
        format_details=canonical.format_details,
        mode=canonical.mode,
        width_px=canonical.width_px,
        height_px=canonical.height_px,
        encoded_bytes=SanitizedImageBytes(encoded_bytes),
    )


def _verify_encoded_image(
    encoded_bytes: bytes,
    canonical: CanonicalPixelImage,
) -> None:
    decoded_output: DecodedImage | None = None
    try:
        decoded_output = decode_bounded_image(BoundedImageBytes(encoded_bytes))
        output_image = decoded_output.as_internal_image()
        if (
            decoded_output.format_details != canonical.format_details
            or output_image.mode != canonical.mode
            or output_image.size != (canonical.width_px, canonical.height_px)
        ):
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.IMAGE_CORRUPT
            )
        verify_reopened_metadata_absence(output_image)
    except ImageSanitizationError:
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None
    finally:
        if decoded_output is not None:
            decoded_output.close()


def verify_reopened_metadata_absence(image: Image.Image) -> None:
    try:
        if len(image.getexif()) != 0:
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.IMAGE_CORRUPT
            )
        normalized_info_keys = {str(key).strip().casefold() for key in image.info}
        if normalized_info_keys & FORBIDDEN_OUTPUT_METADATA_KEYS:
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.IMAGE_CORRUPT
            )
        text = getattr(image, "text", None)
        if text:
            raise ImageSanitizationError.from_internal(
                StorageInternalCode.IMAGE_CORRUPT
            )
    except ImageSanitizationError:
        raise
    except (OSError, SyntaxError, TypeError, ValueError):
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None


def validate_image_dimensions(
    size: tuple[int, int],
    limits: ImageDimensionLimits = DEFAULT_IMAGE_DIMENSION_LIMITS,
) -> None:
    width_px, height_px = size
    if (
        width_px < 1
        or height_px < 1
        or width_px > limits.max_dimension
        or height_px > limits.max_dimension
    ):
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_DIMENSION_LIMIT_EXCEEDED
        )
    if width_px * height_px > limits.max_pixels:
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_PIXEL_LIMIT_EXCEEDED
        )


@contextmanager
def _fatal_decompression_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        yield


def _validate_static_frame_policy(image: Image.Image) -> None:
    if (
        getattr(image, "n_frames", 1) != 1
        or getattr(image, "is_animated", False) is True
    ):
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_ANIMATION_UNSUPPORTED
        )


def _get_canonical_format_details(
    image_format: str | None,
) -> CanonicalImageFormatDetails:
    if image_format is None:
        raise ImageSanitizationError.from_internal(StorageInternalCode.IMAGE_CORRUPT)
    try:
        return CANONICAL_IMAGE_FORMATS[image_format]
    except KeyError:
        raise ImageSanitizationError.from_internal(
            StorageInternalCode.IMAGE_CORRUPT
        ) from None
