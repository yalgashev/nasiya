from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from types import MappingProxyType
from typing import Final


class ErrorCode(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    CSRF_FAILED = "CSRF_FAILED"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_ERROR = "VALIDATION_ERROR"


@dataclass(frozen=True)
class ErrorDefinition:
    code: ErrorCode
    user_message: str
    http_status: int


ERROR_CATALOG: Final[Mapping[ErrorCode, ErrorDefinition]] = MappingProxyType(
    {
        ErrorCode.UNAUTHORIZED: ErrorDefinition(
            code=ErrorCode.UNAUTHORIZED,
            user_message="Kirish talab qilinadi.",
            http_status=HTTPStatus.UNAUTHORIZED,
        ),
        ErrorCode.SESSION_EXPIRED: ErrorDefinition(
            code=ErrorCode.SESSION_EXPIRED,
            user_message="Sessiya muddati tugagan. Qayta kiring.",
            http_status=HTTPStatus.UNAUTHORIZED,
        ),
        ErrorCode.CSRF_FAILED: ErrorDefinition(
            code=ErrorCode.CSRF_FAILED,
            user_message="So'rov xavfsizlik tekshiruvidan o'tmadi.",
            http_status=HTTPStatus.FORBIDDEN,
        ),
        ErrorCode.RATE_LIMITED: ErrorDefinition(
            code=ErrorCode.RATE_LIMITED,
            user_message="Juda ko'p urinish. Keyinroq qayta urinib ko'ring.",
            http_status=HTTPStatus.TOO_MANY_REQUESTS,
        ),
        ErrorCode.VALIDATION_ERROR: ErrorDefinition(
            code=ErrorCode.VALIDATION_ERROR,
            user_message="Kiritilgan ma'lumotlarni tekshiring.",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
    }
)


def get_error_definition(code: ErrorCode) -> ErrorDefinition:
    return ERROR_CATALOG[code]


def get_error_http_status(code: ErrorCode) -> int:
    return int(get_error_definition(code).http_status)


def get_public_error_body(
    code: ErrorCode,
    internal_detail: str | None = None,
) -> dict[str, str]:
    _ = internal_detail
    definition = get_error_definition(code)
    return {
        "code": definition.code.value,
        "message": definition.user_message,
    }
