from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode


class StorageInternalCode(StrEnum):
    STORAGE_CONFIGURATION_UNAVAILABLE = "STORAGE_CONFIGURATION_UNAVAILABLE"
    STORAGE_PROVIDER_UNAVAILABLE = "STORAGE_PROVIDER_UNAVAILABLE"
    IMAGE_CORRUPT = "IMAGE_CORRUPT"
    IMAGE_TRUNCATED = "IMAGE_TRUNCATED"
    IMAGE_PIXEL_LIMIT_EXCEEDED = "IMAGE_PIXEL_LIMIT_EXCEEDED"
    IMAGE_DIMENSION_LIMIT_EXCEEDED = "IMAGE_DIMENSION_LIMIT_EXCEEDED"
    IMAGE_ANIMATION_UNSUPPORTED = "IMAGE_ANIMATION_UNSUPPORTED"
    SANITIZED_OUTPUT_TOO_LARGE = "SANITIZED_OUTPUT_TOO_LARGE"
    UPLOAD_OUTCOME_UNKNOWN = "UPLOAD_OUTCOME_UNKNOWN"
    OBJECT_METADATA_MISMATCH = "OBJECT_METADATA_MISMATCH"
    OBJECT_MISSING_AFTER_UPLOAD = "OBJECT_MISSING_AFTER_UPLOAD"
    DELETE_OUTCOME_UNKNOWN = "DELETE_OUTCOME_UNKNOWN"


_INTERNAL_TO_PUBLIC: Final = MappingProxyType(
    {
        StorageInternalCode.STORAGE_CONFIGURATION_UNAVAILABLE: (
            ErrorCode.FILE_STORAGE_ERROR
        ),
        StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE: (
            ErrorCode.FILE_STORAGE_ERROR
        ),
        StorageInternalCode.IMAGE_CORRUPT: ErrorCode.UNSUPPORTED_FILE_TYPE,
        StorageInternalCode.IMAGE_TRUNCATED: ErrorCode.UNSUPPORTED_FILE_TYPE,
        StorageInternalCode.IMAGE_PIXEL_LIMIT_EXCEEDED: (
            ErrorCode.UNSUPPORTED_FILE_TYPE
        ),
        StorageInternalCode.IMAGE_DIMENSION_LIMIT_EXCEEDED: (
            ErrorCode.UNSUPPORTED_FILE_TYPE
        ),
        StorageInternalCode.IMAGE_ANIMATION_UNSUPPORTED: (
            ErrorCode.UNSUPPORTED_FILE_TYPE
        ),
        StorageInternalCode.SANITIZED_OUTPUT_TOO_LARGE: ErrorCode.FILE_TOO_LARGE,
        StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN: ErrorCode.FILE_STORAGE_ERROR,
        StorageInternalCode.OBJECT_METADATA_MISMATCH: ErrorCode.FILE_STORAGE_ERROR,
        StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD: (ErrorCode.FILE_STORAGE_ERROR),
        StorageInternalCode.DELETE_OUTCOME_UNKNOWN: ErrorCode.FILE_STORAGE_ERROR,
    }
)


class StorageBoundaryError(RuntimeError):
    def __init__(self, code: StorageInternalCode) -> None:
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"StorageBoundaryError(code={self.code.value!r})"


class StorageAccessDeniedError(RuntimeError):
    def __init__(self) -> None:
        self.code = ErrorCode.FILE_ACCESS_DENIED
        super().__init__(self.code.value)

    def __repr__(self) -> str:
        return "StorageAccessDeniedError(code='FILE_ACCESS_DENIED')"


def get_storage_public_error_code(code: StorageInternalCode) -> ErrorCode:
    return _INTERNAL_TO_PUBLIC[code]
