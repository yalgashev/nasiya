import pytest

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.storage.errors import (
    StorageBoundaryError,
    StorageInternalCode,
    get_storage_public_error_code,
)


def test_storage_internal_code_set_is_closed_and_stable() -> None:
    assert [code.value for code in StorageInternalCode] == [
        "STORAGE_CONFIGURATION_UNAVAILABLE",
        "STORAGE_PROVIDER_UNAVAILABLE",
        "IMAGE_CORRUPT",
        "IMAGE_TRUNCATED",
        "IMAGE_PIXEL_LIMIT_EXCEEDED",
        "IMAGE_DIMENSION_LIMIT_EXCEEDED",
        "IMAGE_ANIMATION_UNSUPPORTED",
        "SANITIZED_OUTPUT_TOO_LARGE",
        "UPLOAD_OUTCOME_UNKNOWN",
        "OBJECT_METADATA_MISMATCH",
        "OBJECT_MISSING_AFTER_UPLOAD",
        "DELETE_OUTCOME_UNKNOWN",
    ]


@pytest.mark.parametrize(
    ("internal_code", "public_code"),
    [
        (
            StorageInternalCode.STORAGE_CONFIGURATION_UNAVAILABLE,
            ErrorCode.FILE_STORAGE_ERROR,
        ),
        (
            StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE,
            ErrorCode.FILE_STORAGE_ERROR,
        ),
        (
            StorageInternalCode.IMAGE_CORRUPT,
            ErrorCode.UNSUPPORTED_FILE_TYPE,
        ),
        (
            StorageInternalCode.IMAGE_TRUNCATED,
            ErrorCode.UNSUPPORTED_FILE_TYPE,
        ),
        (
            StorageInternalCode.IMAGE_PIXEL_LIMIT_EXCEEDED,
            ErrorCode.UNSUPPORTED_FILE_TYPE,
        ),
        (
            StorageInternalCode.IMAGE_DIMENSION_LIMIT_EXCEEDED,
            ErrorCode.UNSUPPORTED_FILE_TYPE,
        ),
        (
            StorageInternalCode.IMAGE_ANIMATION_UNSUPPORTED,
            ErrorCode.UNSUPPORTED_FILE_TYPE,
        ),
        (
            StorageInternalCode.SANITIZED_OUTPUT_TOO_LARGE,
            ErrorCode.FILE_TOO_LARGE,
        ),
        (
            StorageInternalCode.UPLOAD_OUTCOME_UNKNOWN,
            ErrorCode.FILE_STORAGE_ERROR,
        ),
        (
            StorageInternalCode.OBJECT_METADATA_MISMATCH,
            ErrorCode.FILE_STORAGE_ERROR,
        ),
        (
            StorageInternalCode.OBJECT_MISSING_AFTER_UPLOAD,
            ErrorCode.FILE_STORAGE_ERROR,
        ),
        (
            StorageInternalCode.DELETE_OUTCOME_UNKNOWN,
            ErrorCode.FILE_STORAGE_ERROR,
        ),
    ],
)
def test_internal_codes_map_to_stable_public_codes(
    internal_code: StorageInternalCode,
    public_code: ErrorCode,
) -> None:
    assert get_storage_public_error_code(internal_code) is public_code


def test_missing_and_denied_share_the_same_public_mapping() -> None:
    missing = get_public_error_body(
        ErrorCode.FILE_ACCESS_DENIED,
        internal_detail="object row missing",
    )
    denied = get_public_error_body(
        ErrorCode.FILE_ACCESS_DENIED,
        internal_detail="domain authorizer denied",
    )

    assert missing == denied
    assert missing["code"] == "FILE_ACCESS_DENIED"
    assert "missing" not in str(missing)
    assert "denied" not in str(denied)


def test_storage_error_never_accepts_or_renders_provider_detail() -> None:
    sensitive_values = (
        "http://private-storage.invalid:9000",
        "private-bucket",
        "v1/objects/0123456789abcdef0123456789abcdef.jpg",
        "synthetic-secret-key",
        "X-Amz-Signature=sensitive",
    )
    error = StorageBoundaryError(StorageInternalCode.STORAGE_PROVIDER_UNAVAILABLE)
    rendered = f"{error!s} {error!r}"

    for sensitive_value in sensitive_values:
        assert sensitive_value not in rendered
