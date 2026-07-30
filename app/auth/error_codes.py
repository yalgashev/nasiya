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
    TELEGRAM_ALREADY_LINKED = "TELEGRAM_ALREADY_LINKED"
    TELEGRAM_NOT_LINKED = "TELEGRAM_NOT_LINKED"
    TELEGRAM_CHAT_ALREADY_LINKED = "TELEGRAM_CHAT_ALREADY_LINKED"
    LINK_TOKEN_INVALID = "LINK_TOKEN_INVALID"
    FORBIDDEN = "FORBIDDEN"
    SHOP_SUSPENDED = "SHOP_SUSPENDED"
    LAST_OWNER = "LAST_OWNER"
    REASON_REQUIRED = "REASON_REQUIRED"
    FILE_ACCESS_DENIED = "FILE_ACCESS_DENIED"
    FILE_STORAGE_ERROR = "FILE_STORAGE_ERROR"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"


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
        ErrorCode.TELEGRAM_ALREADY_LINKED: ErrorDefinition(
            code=ErrorCode.TELEGRAM_ALREADY_LINKED,
            user_message="Telegram akkauntingiz allaqachon bog'langan.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.TELEGRAM_NOT_LINKED: ErrorDefinition(
            code=ErrorCode.TELEGRAM_NOT_LINKED,
            user_message="Telegram akkauntingiz bog'lanmagan.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED: ErrorDefinition(
            code=ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED,
            user_message="Bu Telegram chat allaqachon bog'langan.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.LINK_TOKEN_INVALID: ErrorDefinition(
            code=ErrorCode.LINK_TOKEN_INVALID,
            user_message="Telegram bog'lash tokeni yaroqsiz yoki muddati tugagan.",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        ErrorCode.FORBIDDEN: ErrorDefinition(
            code=ErrorCode.FORBIDDEN,
            user_message="Bu amal uchun ruxsat yo'q.",
            http_status=HTTPStatus.FORBIDDEN,
        ),
        ErrorCode.SHOP_SUSPENDED: ErrorDefinition(
            code=ErrorCode.SHOP_SUSPENDED,
            user_message="Do'kon vaqtincha to'xtatilgan.",
            http_status=HTTPStatus.FORBIDDEN,
        ),
        ErrorCode.LAST_OWNER: ErrorDefinition(
            code=ErrorCode.LAST_OWNER,
            user_message="Oxirgi egani olib tashlab bo'lmaydi.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.REASON_REQUIRED: ErrorDefinition(
            code=ErrorCode.REASON_REQUIRED,
            user_message="Sabab ko'rsatilishi shart.",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        ErrorCode.FILE_ACCESS_DENIED: ErrorDefinition(
            code=ErrorCode.FILE_ACCESS_DENIED,
            user_message="Faylga kirish ruxsati yo'q.",
            http_status=HTTPStatus.FORBIDDEN,
        ),
        ErrorCode.FILE_STORAGE_ERROR: ErrorDefinition(
            code=ErrorCode.FILE_STORAGE_ERROR,
            user_message="Fayl saqlash xizmati vaqtincha ishlamayapti.",
            http_status=HTTPStatus.SERVICE_UNAVAILABLE,
        ),
        ErrorCode.FILE_TOO_LARGE: ErrorDefinition(
            code=ErrorCode.FILE_TOO_LARGE,
            user_message="Fayl hajmi ruxsat etilgan limitdan oshgan.",
            http_status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        ),
        ErrorCode.UNSUPPORTED_FILE_TYPE: ErrorDefinition(
            code=ErrorCode.UNSUPPORTED_FILE_TYPE,
            user_message="Bu fayl turi qabul qilinmaydi.",
            http_status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
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
