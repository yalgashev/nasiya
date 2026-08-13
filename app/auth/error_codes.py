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
    TELEGRAM_CONTACT_REQUIRED = "TELEGRAM_CONTACT_REQUIRED"
    TELEGRAM_PHONE_MISMATCH = "TELEGRAM_PHONE_MISMATCH"
    TELEGRAM_PHONE_NOT_VERIFIED = "TELEGRAM_PHONE_NOT_VERIFIED"
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
    OFFER_UNAVAILABLE = "OFFER_UNAVAILABLE"
    OFFER_CHANGED = "OFFER_CHANGED"
    OFFER_INCOMPLETE = "OFFER_INCOMPLETE"
    OFFER_NOT_DRAFT = "OFFER_NOT_DRAFT"
    OFFER_NOT_APPROVED = "OFFER_NOT_APPROVED"
    LEGAL_REVIEW_EVIDENCE_REQUIRED = "LEGAL_REVIEW_EVIDENCE_REQUIRED"
    DUPLICATE_JSHSHIR = "DUPLICATE_JSHSHIR"
    CUSTOMER_DRAFT_REQUIRED = "CUSTOMER_DRAFT_REQUIRED"
    CUSTOMER_IDENTITY_CHANGED = "CUSTOMER_IDENTITY_CHANGED"
    CUSTOMER_DOCUMENT_CHANGED = "CUSTOMER_DOCUMENT_CHANGED"
    CUSTOMER_IDENTITY_UNAVAILABLE = "CUSTOMER_IDENTITY_UNAVAILABLE"
    CUSTOMER_DOCUMENT_UNAVAILABLE = "CUSTOMER_DOCUMENT_UNAVAILABLE"
    OTP_INVALID = "OTP_INVALID"
    REGISTRATION_OFFER_NOT_ACCEPTED = "REGISTRATION_OFFER_NOT_ACCEPTED"
    CUSTOMER_ACTIVATION_CHANGED = "CUSTOMER_ACTIVATION_CHANGED"
    TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER = "TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER"
    CUSTOMER_LINK_UNAVAILABLE = "CUSTOMER_LINK_UNAVAILABLE"
    SHOP_CUSTOMER_UNAVAILABLE = "SHOP_CUSTOMER_UNAVAILABLE"
    SHOP_CUSTOMER_CHANGED = "SHOP_CUSTOMER_CHANGED"
    CUSTOMER_NOT_ACTIVE = "CUSTOMER_NOT_ACTIVE"
    CUSTOMER_BLACKLISTED = "CUSTOMER_BLACKLISTED"
    CUSTOMER_RATING_BLOCKED = "CUSTOMER_RATING_BLOCKED"
    CREDIT_LIMIT_EXCEEDED = "CREDIT_LIMIT_EXCEEDED"
    MAX_OPEN_DEBTS = "MAX_OPEN_DEBTS"
    DEBT_UNAVAILABLE = "DEBT_UNAVAILABLE"
    DEBT_NOT_PENDING = "DEBT_NOT_PENDING"
    DEBT_EXPIRED = "DEBT_EXPIRED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PAYMENT_UNAVAILABLE = "PAYMENT_UNAVAILABLE"
    PAYMENT_NOT_VOIDABLE = "PAYMENT_NOT_VOIDABLE"
    PAYMENT_AMOUNT_EXCEEDS_BALANCE = "PAYMENT_AMOUNT_EXCEEDS_BALANCE"
    DEBT_CHANGED = "DEBT_CHANGED"
    DEBT_NOT_PAYABLE = "DEBT_NOT_PAYABLE"


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
        ErrorCode.TELEGRAM_CONTACT_REQUIRED: ErrorDefinition(
            code=ErrorCode.TELEGRAM_CONTACT_REQUIRED,
            user_message="Telegram orqali o'zingizning kontaktingizni yuboring.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.TELEGRAM_PHONE_MISMATCH: ErrorDefinition(
            code=ErrorCode.TELEGRAM_PHONE_MISMATCH,
            user_message="Telegram kontaktini tasdiqlab bo'lmadi.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED: ErrorDefinition(
            code=ErrorCode.TELEGRAM_PHONE_NOT_VERIFIED,
            user_message="Telegram kontaktingizni tasdiqlang.",
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
        ErrorCode.OFFER_UNAVAILABLE: ErrorDefinition(
            code=ErrorCode.OFFER_UNAVAILABLE,
            user_message="Joriy taklif hozir mavjud emas.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.OFFER_CHANGED: ErrorDefinition(
            code=ErrorCode.OFFER_CHANGED,
            user_message="Taklif o'zgargan. Sahifani yangilang.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.OFFER_INCOMPLETE: ErrorDefinition(
            code=ErrorCode.OFFER_INCOMPLETE,
            user_message="Taklifning barcha til variantlarini to'ldiring.",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        ErrorCode.OFFER_NOT_DRAFT: ErrorDefinition(
            code=ErrorCode.OFFER_NOT_DRAFT,
            user_message="Faqat qoralama taklifni tahrirlash mumkin.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.OFFER_NOT_APPROVED: ErrorDefinition(
            code=ErrorCode.OFFER_NOT_APPROVED,
            user_message="Faqat tasdiqlangan taklifni joriy qilish mumkin.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED: ErrorDefinition(
            code=ErrorCode.LEGAL_REVIEW_EVIDENCE_REQUIRED,
            user_message="Tashqi yuridik ko'rib chiqish dalili talab qilinadi.",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        ErrorCode.DUPLICATE_JSHSHIR: ErrorDefinition(
            code=ErrorCode.DUPLICATE_JSHSHIR,
            user_message="Bu JSHSHIR bilan mijoz allaqachon mavjud.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_DRAFT_REQUIRED: ErrorDefinition(
            code=ErrorCode.CUSTOMER_DRAFT_REQUIRED,
            user_message="Avval mijoz qoralamasini yarating.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_IDENTITY_CHANGED: ErrorDefinition(
            code=ErrorCode.CUSTOMER_IDENTITY_CHANGED,
            user_message="Shaxsiy ma'lumotlar o'zgargan. Sahifani yangilang.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED: ErrorDefinition(
            code=ErrorCode.CUSTOMER_DOCUMENT_CHANGED,
            user_message="Hujjat o'zgargan. Sahifani yangilang.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE: ErrorDefinition(
            code=ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
            user_message="Shaxsiy ma'lumotlar hozir mavjud emas.",
            http_status=HTTPStatus.SERVICE_UNAVAILABLE,
        ),
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE: ErrorDefinition(
            code=ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
            user_message="Hujjat hozir mavjud emas.",
            http_status=HTTPStatus.SERVICE_UNAVAILABLE,
        ),
        ErrorCode.OTP_INVALID: ErrorDefinition(
            code=ErrorCode.OTP_INVALID,
            user_message="Kod noto'g'ri yoki muddati tugagan.",
            http_status=HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED: ErrorDefinition(
            code=ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
            user_message="Joriy ro'yxatdan o'tish taklifini qabul qiling.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_ACTIVATION_CHANGED: ErrorDefinition(
            code=ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
            user_message="Faollashtirish ma'lumotlari o'zgargan. Yangi kod so'rang.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER: ErrorDefinition(
            code=ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
            user_message="Faol mijoz uchun Telegram bog'lanishi saqlanishi kerak.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_LINK_UNAVAILABLE: ErrorDefinition(
            code=ErrorCode.CUSTOMER_LINK_UNAVAILABLE,
            user_message="Mijozni bog'lash hozir mavjud emas.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.SHOP_CUSTOMER_UNAVAILABLE: ErrorDefinition(
            code=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE,
            user_message="Mijoz bog'lanishi mavjud emas.",
            http_status=HTTPStatus.NOT_FOUND,
        ),
        ErrorCode.SHOP_CUSTOMER_CHANGED: ErrorDefinition(
            code=ErrorCode.SHOP_CUSTOMER_CHANGED,
            user_message="Mijoz bog'lanishi o'zgargan. Sahifani yangilang.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_NOT_ACTIVE: ErrorDefinition(
            code=ErrorCode.CUSTOMER_NOT_ACTIVE,
            user_message="Mijoz hali faol emas.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_BLACKLISTED: ErrorDefinition(
            code=ErrorCode.CUSTOMER_BLACKLISTED,
            user_message="Mijoz uchun qarz yaratish mumkin emas.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CUSTOMER_RATING_BLOCKED: ErrorDefinition(
            code=ErrorCode.CUSTOMER_RATING_BLOCKED,
            user_message="Mijoz uchun qarz amali hozir mavjud emas.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.CREDIT_LIMIT_EXCEEDED: ErrorDefinition(
            code=ErrorCode.CREDIT_LIMIT_EXCEEDED,
            user_message="Kredit limiti oshib ketadi.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.MAX_OPEN_DEBTS: ErrorDefinition(
            code=ErrorCode.MAX_OPEN_DEBTS,
            user_message="Ochiq qarzlar limiti to'lgan.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.DEBT_UNAVAILABLE: ErrorDefinition(
            code=ErrorCode.DEBT_UNAVAILABLE,
            user_message="Qarz hozir mavjud emas.",
            http_status=HTTPStatus.NOT_FOUND,
        ),
        ErrorCode.DEBT_NOT_PENDING: ErrorDefinition(
            code=ErrorCode.DEBT_NOT_PENDING,
            user_message="Amal faqat kutilayotgan qarz uchun mumkin.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.DEBT_EXPIRED: ErrorDefinition(
            code=ErrorCode.DEBT_EXPIRED,
            user_message="Qarzni qabul qilish muddati tugagan.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.IDEMPOTENCY_CONFLICT: ErrorDefinition(
            code=ErrorCode.IDEMPOTENCY_CONFLICT,
            user_message="Bu takrorlash kaliti boshqa so'rov bilan ishlatilgan.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.PAYMENT_UNAVAILABLE: ErrorDefinition(
            code=ErrorCode.PAYMENT_UNAVAILABLE,
            user_message="To'lov hozir mavjud emas.",
            http_status=HTTPStatus.NOT_FOUND,
        ),
        ErrorCode.PAYMENT_NOT_VOIDABLE: ErrorDefinition(
            code=ErrorCode.PAYMENT_NOT_VOIDABLE,
            user_message="Bu to'lovni hozir bekor qilib bo'lmaydi.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE: ErrorDefinition(
            code=ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE,
            user_message="To'lov summasi qolgan qarzdan oshadi.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.DEBT_CHANGED: ErrorDefinition(
            code=ErrorCode.DEBT_CHANGED,
            user_message="Qarz o'zgardi. Sahifani yangilang.",
            http_status=HTTPStatus.CONFLICT,
        ),
        ErrorCode.DEBT_NOT_PAYABLE: ErrorDefinition(
            code=ErrorCode.DEBT_NOT_PAYABLE,
            user_message="Bu qarz uchun to'lov hozir qabul qilinmaydi.",
            http_status=HTTPStatus.CONFLICT,
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
