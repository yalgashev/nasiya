from types import MappingProxyType

import pytest

from app.auth.error_codes import (
    ERROR_CATALOG,
    ErrorCode,
    get_error_definition,
    get_error_http_status,
    get_public_error_body,
)


def test_m2_error_catalog_contains_only_stable_m2_codes() -> None:
    assert set(ERROR_CATALOG.keys()) == {
        ErrorCode.UNAUTHORIZED,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.CSRF_FAILED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.VALIDATION_ERROR,
    }
    assert len(ERROR_CATALOG) == 5


def test_error_code_values_are_stable() -> None:
    assert [code.value for code in ErrorCode] == [
        "UNAUTHORIZED",
        "SESSION_EXPIRED",
        "CSRF_FAILED",
        "RATE_LIMITED",
        "VALIDATION_ERROR",
    ]


@pytest.mark.parametrize(
    ("code", "http_status"),
    [
        (ErrorCode.UNAUTHORIZED, 401),
        (ErrorCode.SESSION_EXPIRED, 401),
        (ErrorCode.CSRF_FAILED, 403),
        (ErrorCode.RATE_LIMITED, 429),
        (ErrorCode.VALIDATION_ERROR, 422),
    ],
)
def test_error_http_status_mapping_is_stable(
    code: ErrorCode,
    http_status: int,
) -> None:
    assert get_error_definition(code).http_status == http_status
    assert get_error_http_status(code) == http_status


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (ErrorCode.UNAUTHORIZED, "Kirish talab qilinadi."),
        (ErrorCode.SESSION_EXPIRED, "Sessiya muddati tugagan. Qayta kiring."),
        (
            ErrorCode.CSRF_FAILED,
            "So'rov xavfsizlik tekshiruvidan o'tmadi.",
        ),
        (
            ErrorCode.RATE_LIMITED,
            "Juda ko'p urinish. Keyinroq qayta urinib ko'ring.",
        ),
        (ErrorCode.VALIDATION_ERROR, "Kiritilgan ma'lumotlarni tekshiring."),
    ],
)
def test_error_user_messages_are_safe_and_stable(
    code: ErrorCode,
    message: str,
) -> None:
    definition = get_error_definition(code)
    public_body = get_public_error_body(code)

    assert definition.user_message == message
    assert public_body == {"code": code.value, "message": message}


def test_internal_detail_is_not_exposed_to_user_body() -> None:
    internal_detail = "database said token hash abc123 was revoked"

    public_body = get_public_error_body(
        ErrorCode.SESSION_EXPIRED,
        internal_detail=internal_detail,
    )

    assert "internal_detail" not in public_body
    assert internal_detail not in str(public_body)
    assert "abc123" not in str(public_body)


def test_error_catalog_is_not_mutable() -> None:
    assert isinstance(ERROR_CATALOG, MappingProxyType)
    with pytest.raises(TypeError):
        ERROR_CATALOG[ErrorCode.UNAUTHORIZED] = get_error_definition(
            ErrorCode.UNAUTHORIZED
        )
