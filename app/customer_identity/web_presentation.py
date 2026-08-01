from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode
from app.otp.web_presentation import OtpWebLanguage, resolve_otp_web_language


class CustomerIdentityWebLanguage(StrEnum):
    UZ_LATN = "uz"
    RU = "ru"


_UZ_LATN_MESSAGES: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.DUPLICATE_JSHSHIR: ("Bu JSHSHIR bilan mijoz allaqachon mavjud."),
        ErrorCode.CUSTOMER_DRAFT_REQUIRED: "Avval mijoz qoralamasini yarating.",
        ErrorCode.CUSTOMER_IDENTITY_CHANGED: (
            "Shaxsiy ma'lumotlar o'zgargan. Sahifani yangilang."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED: ("Hujjat o'zgargan. Sahifani yangilang."),
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE: (
            "Shaxsiy ma'lumotlar hozir mavjud emas."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE: "Hujjat hozir mavjud emas.",
    }
)
_RU_MESSAGES: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.DUPLICATE_JSHSHIR: ("Клиент с таким ПИНФЛ уже существует."),
        ErrorCode.CUSTOMER_DRAFT_REQUIRED: ("Сначала создайте черновик клиента."),
        ErrorCode.CUSTOMER_IDENTITY_CHANGED: (
            "Персональные данные изменились. Обновите страницу."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_CHANGED: ("Документ изменился. Обновите страницу."),
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE: (
            "Персональные данные сейчас недоступны."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE: ("Документ сейчас недоступен."),
    }
)
_MESSAGES: Final[Mapping[CustomerIdentityWebLanguage, Mapping[ErrorCode, str]]] = (
    MappingProxyType(
        {
            CustomerIdentityWebLanguage.UZ_LATN: _UZ_LATN_MESSAGES,
            CustomerIdentityWebLanguage.RU: _RU_MESSAGES,
        }
    )
)


def resolve_customer_identity_web_language(
    locale_cookie: str | None,
    accept_language: str | None,
) -> CustomerIdentityWebLanguage:
    inherited = resolve_otp_web_language(locale_cookie, accept_language)
    if inherited is OtpWebLanguage.RU:
        return CustomerIdentityWebLanguage.RU
    return CustomerIdentityWebLanguage.UZ_LATN


def get_customer_identity_web_message(
    language: CustomerIdentityWebLanguage,
    code: ErrorCode,
) -> str | None:
    return _MESSAGES[language].get(code)
