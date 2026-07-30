from types import MappingProxyType
from uuid import uuid4

import pytest

from app.auth.error_codes import (
    ERROR_CATALOG,
    ErrorCode,
    get_error_definition,
    get_error_http_status,
    get_public_error_body,
)


def test_error_catalog_contains_only_stable_codes() -> None:
    assert set(ERROR_CATALOG.keys()) == {
        ErrorCode.UNAUTHORIZED,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.CSRF_FAILED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.TELEGRAM_ALREADY_LINKED,
        ErrorCode.TELEGRAM_NOT_LINKED,
        ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED,
        ErrorCode.LINK_TOKEN_INVALID,
        ErrorCode.FORBIDDEN,
        ErrorCode.SHOP_SUSPENDED,
        ErrorCode.LAST_OWNER,
        ErrorCode.REASON_REQUIRED,
        ErrorCode.FILE_ACCESS_DENIED,
        ErrorCode.FILE_STORAGE_ERROR,
        ErrorCode.FILE_TOO_LARGE,
        ErrorCode.UNSUPPORTED_FILE_TYPE,
    }
    assert len(ERROR_CATALOG) == 17


def test_error_code_values_are_stable() -> None:
    assert [code.value for code in ErrorCode] == [
        "UNAUTHORIZED",
        "SESSION_EXPIRED",
        "CSRF_FAILED",
        "RATE_LIMITED",
        "VALIDATION_ERROR",
        "TELEGRAM_ALREADY_LINKED",
        "TELEGRAM_NOT_LINKED",
        "TELEGRAM_CHAT_ALREADY_LINKED",
        "LINK_TOKEN_INVALID",
        "FORBIDDEN",
        "SHOP_SUSPENDED",
        "LAST_OWNER",
        "REASON_REQUIRED",
        "FILE_ACCESS_DENIED",
        "FILE_STORAGE_ERROR",
        "FILE_TOO_LARGE",
        "UNSUPPORTED_FILE_TYPE",
    ]


@pytest.mark.parametrize(
    ("code", "http_status"),
    [
        (ErrorCode.UNAUTHORIZED, 401),
        (ErrorCode.SESSION_EXPIRED, 401),
        (ErrorCode.CSRF_FAILED, 403),
        (ErrorCode.RATE_LIMITED, 429),
        (ErrorCode.VALIDATION_ERROR, 422),
        (ErrorCode.TELEGRAM_ALREADY_LINKED, 409),
        (ErrorCode.TELEGRAM_NOT_LINKED, 409),
        (ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED, 409),
        (ErrorCode.LINK_TOKEN_INVALID, 422),
        (ErrorCode.FORBIDDEN, 403),
        (ErrorCode.SHOP_SUSPENDED, 403),
        (ErrorCode.LAST_OWNER, 409),
        (ErrorCode.REASON_REQUIRED, 422),
        (ErrorCode.FILE_ACCESS_DENIED, 403),
        (ErrorCode.FILE_STORAGE_ERROR, 503),
        (ErrorCode.FILE_TOO_LARGE, 413),
        (ErrorCode.UNSUPPORTED_FILE_TYPE, 415),
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
        (
            ErrorCode.TELEGRAM_ALREADY_LINKED,
            "Telegram akkauntingiz allaqachon bog'langan.",
        ),
        (
            ErrorCode.TELEGRAM_NOT_LINKED,
            "Telegram akkauntingiz bog'lanmagan.",
        ),
        (
            ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED,
            "Bu Telegram chat allaqachon bog'langan.",
        ),
        (
            ErrorCode.LINK_TOKEN_INVALID,
            "Telegram bog'lash tokeni yaroqsiz yoki muddati tugagan.",
        ),
        (ErrorCode.FORBIDDEN, "Bu amal uchun ruxsat yo'q."),
        (ErrorCode.SHOP_SUSPENDED, "Do'kon vaqtincha to'xtatilgan."),
        (ErrorCode.LAST_OWNER, "Oxirgi egani olib tashlab bo'lmaydi."),
        (ErrorCode.REASON_REQUIRED, "Sabab ko'rsatilishi shart."),
        (ErrorCode.FILE_ACCESS_DENIED, "Faylga kirish ruxsati yo'q."),
        (
            ErrorCode.FILE_STORAGE_ERROR,
            "Fayl saqlash xizmati vaqtincha ishlamayapti.",
        ),
        (
            ErrorCode.FILE_TOO_LARGE,
            "Fayl hajmi ruxsat etilgan limitdan oshgan.",
        ),
        (ErrorCode.UNSUPPORTED_FILE_TYPE, "Bu fayl turi qabul qilinmaydi."),
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


def test_m5_error_codes_are_in_catalog_without_extra_shop_not_found_code() -> None:
    m5_codes = {
        ErrorCode.UNAUTHORIZED,
        ErrorCode.FORBIDDEN,
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.CSRF_FAILED,
        ErrorCode.LAST_OWNER,
        ErrorCode.SHOP_SUSPENDED,
        ErrorCode.REASON_REQUIRED,
    }

    assert m5_codes.issubset(ERROR_CATALOG)
    assert "SHOP_NOT_FOUND" not in {code.value for code in ErrorCode}
    assert "SHOP_NOT_FOUND" not in str(ERROR_CATALOG)


def test_tenant_missing_and_forbidden_use_same_external_error_shape() -> None:
    missing_body = get_public_error_body(
        ErrorCode.FORBIDDEN,
        internal_detail="shop id does not exist",
    )
    denied_body = get_public_error_body(
        ErrorCode.FORBIDDEN,
        internal_detail="user is not a member of existing shop",
    )

    assert missing_body == denied_body
    assert missing_body == {
        "code": ErrorCode.FORBIDDEN.value,
        "message": "Bu amal uchun ruxsat yo'q.",
    }
    assert "not found" not in str(missing_body).casefold()
    assert "exists" not in str(missing_body).casefold()
    assert "member" not in str(missing_body).casefold()


@pytest.mark.parametrize(
    "code",
    [
        ErrorCode.FORBIDDEN,
        ErrorCode.SHOP_SUSPENDED,
        ErrorCode.LAST_OWNER,
        ErrorCode.REASON_REQUIRED,
    ],
)
def test_m5_public_errors_do_not_expose_raw_constraints_or_uuid(
    code: ErrorCode,
) -> None:
    leaked_uuid = uuid4()
    internal_detail = (
        f"constraint fk_shop_staff_shop_id_shops_id failed for shop_id={leaked_uuid}"
    )

    public_body = get_public_error_body(code, internal_detail=internal_detail)
    rendered_body = str(public_body)

    assert "constraint" not in rendered_body.casefold()
    assert "fk_" not in rendered_body
    assert str(leaked_uuid) not in rendered_body


def test_error_catalog_is_not_mutable() -> None:
    assert isinstance(ERROR_CATALOG, MappingProxyType)
    with pytest.raises(TypeError):
        ERROR_CATALOG[ErrorCode.UNAUTHORIZED] = get_error_definition(
            ErrorCode.UNAUTHORIZED
        )
