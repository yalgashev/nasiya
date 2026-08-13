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
        ErrorCode.TELEGRAM_CONTACT_REQUIRED,
        ErrorCode.TELEGRAM_PHONE_MISMATCH,
        ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED,
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
        ErrorCode.OFFER_UNAVAILABLE,
        ErrorCode.OFFER_CHANGED,
        ErrorCode.OFFER_INCOMPLETE,
        ErrorCode.OFFER_NOT_DRAFT,
        ErrorCode.OFFER_NOT_APPROVED,
        ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED,
        ErrorCode.DUPLICATE_JSHSHIR,
        ErrorCode.CUSTOMER_DRAFT_REQUIRED,
        ErrorCode.CUSTOMER_IDENTITY_CHANGED,
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED,
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
        ErrorCode.OTP_INVALID,
        ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
        ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
        ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
        ErrorCode.CUSTOMER_LINK_UNAVAILABLE,
        ErrorCode.SHOP_CUSTOMER_UNAVAILABLE,
        ErrorCode.SHOP_CUSTOMER_CHANGED,
        ErrorCode.CUSTOMER_NOT_ACTIVE,
        ErrorCode.CUSTOMER_BLACKLISTED,
        ErrorCode.CUSTOMER_RATING_BLOCKED,
        ErrorCode.CREDIT_LIMIT_EXCEEDED,
        ErrorCode.MAX_OPEN_DEBTS,
        ErrorCode.DEBT_UNAVAILABLE,
        ErrorCode.DEBT_NOT_PENDING,
        ErrorCode.DEBT_EXPIRED,
        ErrorCode.IDEMPOTENCY_CONFLICT,
        ErrorCode.PAYMENT_UNAVAILABLE,
        ErrorCode.PAYMENT_NOT_VOIDABLE,
        ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE,
        ErrorCode.DEBT_CHANGED,
        ErrorCode.DEBT_NOT_PAYABLE,
    }
    assert len(ERROR_CATALOG) == 53


def test_error_code_values_are_stable() -> None:
    assert [code.value for code in ErrorCode] == [
        "UNAUTHORIZED",
        "SESSION_EXPIRED",
        "CSRF_FAILED",
        "RATE_LIMITED",
        "VALIDATION_ERROR",
        "TELEGRAM_ALREADY_LINKED",
        "TELEGRAM_NOT_LINKED",
        "TELEGRAM_CONTACT_REQUIRED",
        "TELEGRAM_PHONE_MISMATCH",
        "TELEGRAM_PHONE_NOT_VERIFIED",
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
        "OFFER_UNAVAILABLE",
        "OFFER_CHANGED",
        "OFFER_INCOMPLETE",
        "OFFER_NOT_DRAFT",
        "OFFER_NOT_APPROVED",
        "LEGAL_REVIEW_EVIDENCE_REQUIRED",
        "DUPLICATE_JSHSHIR",
        "CUSTOMER_DRAFT_REQUIRED",
        "CUSTOMER_IDENTITY_CHANGED",
        "CUSTOMER_DOCUMENT_CHANGED",
        "CUSTOMER_IDENTITY_UNAVAILABLE",
        "CUSTOMER_DOCUMENT_UNAVAILABLE",
        "OTP_INVALID",
        "REGISTRATION_OFFER_NOT_ACCEPTED",
        "CUSTOMER_ACTIVATION_CHANGED",
        "TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER",
        "CUSTOMER_LINK_UNAVAILABLE",
        "SHOP_CUSTOMER_UNAVAILABLE",
        "SHOP_CUSTOMER_CHANGED",
        "CUSTOMER_NOT_ACTIVE",
        "CUSTOMER_BLACKLISTED",
        "CUSTOMER_RATING_BLOCKED",
        "CREDIT_LIMIT_EXCEEDED",
        "MAX_OPEN_DEBTS",
        "DEBT_UNAVAILABLE",
        "DEBT_NOT_PENDING",
        "DEBT_EXPIRED",
        "IDEMPOTENCY_CONFLICT",
        "PAYMENT_UNAVAILABLE",
        "PAYMENT_NOT_VOIDABLE",
        "PAYMENT_AMOUNT_EXCEEDS_BALANCE",
        "DEBT_CHANGED",
        "DEBT_NOT_PAYABLE",
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
        (ErrorCode.TELEGRAM_CONTACT_REQUIRED, 409),
        (ErrorCode.TELEGRAM_PHONE_MISMATCH, 409),
        (ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED, 409),
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
        (ErrorCode.OFFER_UNAVAILABLE, 409),
        (ErrorCode.OFFER_CHANGED, 409),
        (ErrorCode.OFFER_INCOMPLETE, 422),
        (ErrorCode.OFFER_NOT_DRAFT, 409),
        (ErrorCode.OFFER_NOT_APPROVED, 409),
        (ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED, 422),
        (ErrorCode.DUPLICATE_JSHSHIR, 409),
        (ErrorCode.CUSTOMER_DRAFT_REQUIRED, 409),
        (ErrorCode.CUSTOMER_IDENTITY_CHANGED, 409),
        (ErrorCode.CUSTOMER_DOCUMENT_CHANGED, 409),
        (ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE, 503),
        (ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE, 503),
        (ErrorCode.OTP_INVALID, 422),
        (ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED, 409),
        (ErrorCode.CUSTOMER_ACTIVATION_CHANGED, 409),
        (ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER, 409),
        (ErrorCode.CUSTOMER_LINK_UNAVAILABLE, 409),
        (ErrorCode.SHOP_CUSTOMER_UNAVAILABLE, 404),
        (ErrorCode.SHOP_CUSTOMER_CHANGED, 409),
        (ErrorCode.PAYMENT_UNAVAILABLE, 404),
        (ErrorCode.PAYMENT_NOT_VOIDABLE, 409),
        (ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE, 409),
        (ErrorCode.DEBT_CHANGED, 409),
        (ErrorCode.DEBT_NOT_PAYABLE, 409),
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
            ErrorCode.TELEGRAM_CONTACT_REQUIRED,
            "Telegram orqali o'zingizning kontaktingizni yuboring.",
        ),
        (
            ErrorCode.TELEGRAM_PHONE_MISMATCH,
            "Telegram kontaktini tasdiqlab bo'lmadi.",
        ),
        (
            ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED,
            "Telegram kontaktingizni tasdiqlang.",
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
        (ErrorCode.OFFER_UNAVAILABLE, "Joriy taklif hozir mavjud emas."),
        (ErrorCode.OFFER_CHANGED, "Taklif o'zgargan. Sahifani yangilang."),
        (
            ErrorCode.OFFER_INCOMPLETE,
            "Taklifning barcha til variantlarini to'ldiring.",
        ),
        (
            ErrorCode.OFFER_NOT_DRAFT,
            "Faqat qoralama taklifni tahrirlash mumkin.",
        ),
        (
            ErrorCode.OFFER_NOT_APPROVED,
            "Faqat tasdiqlangan taklifni joriy qilish mumkin.",
        ),
        (
            ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED,
            "Tashqi yuridik ko'rib chiqish dalili talab qilinadi.",
        ),
        (
            ErrorCode.DUPLICATE_JSHSHIR,
            "Bu JSHSHIR bilan mijoz allaqachon mavjud.",
        ),
        (ErrorCode.CUSTOMER_DRAFT_REQUIRED, "Avval mijoz qoralamasini yarating."),
        (
            ErrorCode.CUSTOMER_IDENTITY_CHANGED,
            "Shaxsiy ma'lumotlar o'zgargan. Sahifani yangilang.",
        ),
        (
            ErrorCode.CUSTOMER_DOCUMENT_CHANGED,
            "Hujjat o'zgargan. Sahifani yangilang.",
        ),
        (
            ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
            "Shaxsiy ma'lumotlar hozir mavjud emas.",
        ),
        (
            ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
            "Hujjat hozir mavjud emas.",
        ),
        (ErrorCode.OTP_INVALID, "Kod noto'g'ri yoki muddati tugagan."),
        (
            ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
            "Joriy ro'yxatdan o'tish taklifini qabul qiling.",
        ),
        (
            ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
            "Faollashtirish ma'lumotlari o'zgargan. Yangi kod so'rang.",
        ),
        (
            ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
            "Faol mijoz uchun Telegram bog'lanishi saqlanishi kerak.",
        ),
        (
            ErrorCode.CUSTOMER_LINK_UNAVAILABLE,
            "Mijozni bog'lash hozir mavjud emas.",
        ),
        (
            ErrorCode.SHOP_CUSTOMER_UNAVAILABLE,
            "Mijoz bog'lanishi mavjud emas.",
        ),
        (
            ErrorCode.SHOP_CUSTOMER_CHANGED,
            "Mijoz bog'lanishi o'zgargan. Sahifani yangilang.",
        ),
        (ErrorCode.PAYMENT_UNAVAILABLE, "To'lov hozir mavjud emas."),
        (
            ErrorCode.PAYMENT_NOT_VOIDABLE,
            "Bu to'lovni hozir bekor qilib bo'lmaydi.",
        ),
        (
            ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE,
            "To'lov summasi qolgan qarzdan oshadi.",
        ),
        (ErrorCode.DEBT_CHANGED, "Qarz o'zgardi. Sahifani yangilang."),
        (
            ErrorCode.DEBT_NOT_PAYABLE,
            "Bu qarz uchun to'lov hozir qabul qilinmaydi.",
        ),
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


def test_m9_offer_error_codes_are_exactly_the_six_frozen_codes() -> None:
    offer_codes = {
        code
        for code in ErrorCode
        if code.value.startswith("OFFER_")
        or code is ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED
    }

    assert offer_codes == {
        ErrorCode.OFFER_UNAVAILABLE,
        ErrorCode.OFFER_CHANGED,
        ErrorCode.OFFER_INCOMPLETE,
        ErrorCode.OFFER_NOT_DRAFT,
        ErrorCode.OFFER_NOT_APPROVED,
        ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED,
    }


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
