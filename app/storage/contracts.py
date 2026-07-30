import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import SecretStr

from app.storage.errors import StorageInternalCode

_BUCKET_PATTERN: Final = re.compile(
    r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$",
    flags=re.ASCII,
)
_IPV4_STYLE_BUCKET_PATTERN: Final = re.compile(
    r"^[0-9]{1,3}(?:\.[0-9]{1,3}){3}$",
    flags=re.ASCII,
)
_OBJECT_KEY_PATTERN: Final = re.compile(
    r"^v1/objects/[0-9a-f]{32}\.(jpg|png|webp)$",
    flags=re.ASCII,
)
_CHECKSUM_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_FORMAT_DETAILS: Final = frozenset(
    {
        ("image/jpeg", "jpg"),
        ("image/png", "png"),
        ("image/webp", "webp"),
    }
)


@dataclass(frozen=True, repr=False)
class StorageConfig:
    endpoint_url: SecretStr
    region: str
    bucket: str
    access_key: SecretStr
    secret_key: SecretStr
    use_ssl: bool
    addressing_style: str
    presigned_ttl_seconds: int
    max_upload_bytes: int
    max_multipart_bytes: int
    max_image_pixels: int
    max_image_dimension: int
    upload_rate_limit_window_seconds: int
    upload_rate_limit_user_attempts: int
    upload_rate_limit_ip_attempts: int
    reconcile_stale_seconds: int

    def __repr__(self) -> str:
        return (
            "StorageConfig("
            "endpoint_url=<redacted>, region=<set>, bucket=<redacted>, "
            "access_key=<redacted>, secret_key=<redacted>, "
            f"use_ssl={self.use_ssl!r}, "
            f"addressing_style={self.addressing_style!r}, "
            f"presigned_ttl_seconds={self.presigned_ttl_seconds!r}, "
            f"max_upload_bytes={self.max_upload_bytes!r}, "
            f"max_multipart_bytes={self.max_multipart_bytes!r}, "
            f"max_image_pixels={self.max_image_pixels!r}, "
            f"max_image_dimension={self.max_image_dimension!r}, "
            "upload_rate_limit_window_seconds="
            f"{self.upload_rate_limit_window_seconds!r}, "
            "upload_rate_limit_user_attempts="
            f"{self.upload_rate_limit_user_attempts!r}, "
            "upload_rate_limit_ip_attempts="
            f"{self.upload_rate_limit_ip_attempts!r}, "
            f"reconcile_stale_seconds={self.reconcile_stale_seconds!r}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class BucketName:
    _value: str

    def __post_init__(self) -> None:
        if (
            _BUCKET_PATTERN.fullmatch(self._value) is None
            or ".." in self._value
            or ".-" in self._value
            or "-." in self._value
            or _IPV4_STYLE_BUCKET_PATTERN.fullmatch(self._value) is not None
        ):
            raise ValueError("Invalid bucket name")

    def __repr__(self) -> str:
        return "BucketName(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_internal_value(self) -> str:
        return self._value


@dataclass(frozen=True, repr=False)
class ObjectKey:
    _value: str

    def __post_init__(self) -> None:
        if _OBJECT_KEY_PATTERN.fullmatch(self._value) is None:
            raise ValueError("Invalid object key")

    def __repr__(self) -> str:
        return "ObjectKey(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_internal_value(self) -> str:
        return self._value


@dataclass(frozen=True, repr=False)
class ObjectChecksumSha256:
    _value: str

    def __post_init__(self) -> None:
        if _CHECKSUM_PATTERN.fullmatch(self._value) is None:
            raise ValueError("Invalid object checksum")

    def __repr__(self) -> str:
        return "ObjectChecksumSha256(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_internal_value(self) -> str:
        return self._value


@dataclass(frozen=True, repr=False)
class SanitizedImageBytes:
    _value: bytes

    def __post_init__(self) -> None:
        if not isinstance(self._value, bytes) or not self._value:
            raise ValueError("Sanitized image bytes must be non-empty bytes")

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return f"SanitizedImageBytes(<redacted>, size_bytes={len(self._value)})"

    def __str__(self) -> str:
        return "<redacted>"

    def as_internal_bytes(self) -> bytes:
        return self._value


@dataclass(frozen=True)
class SanitizedImageMetadata:
    content_type: str
    canonical_extension: str
    size_bytes: int
    width_px: int
    height_px: int
    checksum_sha256: ObjectChecksumSha256

    def __post_init__(self) -> None:
        if (self.content_type, self.canonical_extension) not in _FORMAT_DETAILS:
            raise ValueError("Invalid sanitized image format details")
        if min(self.size_bytes, self.width_px, self.height_px) <= 0:
            raise ValueError("Sanitized image dimensions and size must be positive")


@dataclass(frozen=True)
class SanitizedImage:
    metadata: SanitizedImageMetadata
    sanitized_bytes: SanitizedImageBytes = field(repr=False)

    def __post_init__(self) -> None:
        if self.metadata.size_bytes != len(self.sanitized_bytes):
            raise ValueError("Sanitized image size does not match payload")

    def __repr__(self) -> str:
        return (
            "SanitizedImage("
            f"content_type={self.metadata.content_type!r}, "
            f"canonical_extension={self.metadata.canonical_extension!r}, "
            f"size_bytes={self.metadata.size_bytes!r}, "
            f"width_px={self.metadata.width_px!r}, "
            f"height_px={self.metadata.height_px!r}, "
            "checksum_sha256=<redacted>, sanitized_bytes=<redacted>"
            ")"
        )


@dataclass(frozen=True)
class StoredObjectHead:
    size_bytes: int
    content_type: str
    checksum_sha256: ObjectChecksumSha256

    def __post_init__(self) -> None:
        if self.size_bytes <= 0:
            raise ValueError("Stored object size must be positive")
        if self.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("Stored object content type is invalid")


@dataclass(frozen=True, repr=False)
class PresignedObjectUrl:
    _value: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self._value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("Invalid presigned object URL")

    def __repr__(self) -> str:
        return "PresignedObjectUrl(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def as_response_value(self) -> str:
        return self._value


class StorageProviderOperationResult(StrEnum):
    SUCCESS = "SUCCESS"
    MISSING = "MISSING"


class StorageProviderFailureKind(StrEnum):
    DEFINITE = "DEFINITE"
    AMBIGUOUS = "AMBIGUOUS"


class StorageProviderError(RuntimeError):
    def __init__(
        self,
        *,
        kind: StorageProviderFailureKind,
        code: StorageInternalCode,
    ) -> None:
        self.kind = kind
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return (
            f"StorageProviderError(kind={self.kind.value!r}, code={self.code.value!r})"
        )


@runtime_checkable
class ObjectStorageService(Protocol):
    def put_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
        image: SanitizedImage,
    ) -> StorageProviderOperationResult: ...

    def head_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
    ) -> StoredObjectHead | None: ...

    def delete_object(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
    ) -> StorageProviderOperationResult: ...

    def create_presigned_get_url(
        self,
        *,
        bucket: BucketName,
        key: ObjectKey,
        ttl_seconds: int,
    ) -> PresignedObjectUrl: ...

    def ensure_private_bucket(
        self,
        *,
        bucket: BucketName,
    ) -> StorageProviderOperationResult: ...


@dataclass(frozen=True, repr=False)
class ObjectReadAuthorizationRequest:
    actor_user_id: UUID
    object_file_id: UUID
    domain_parent_reference: object = field(repr=False)

    def __repr__(self) -> str:
        return (
            "ObjectReadAuthorizationRequest("
            "actor_user_id=<redacted>, "
            "object_file_id=<redacted>, "
            "domain_parent_reference=<redacted>"
            ")"
        )


class ObjectReadAuthorizationResult(StrEnum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"


@runtime_checkable
class ObjectFileAccessAuthorizer(Protocol):
    def authorize(
        self,
        request: ObjectReadAuthorizationRequest,
    ) -> ObjectReadAuthorizationResult: ...
