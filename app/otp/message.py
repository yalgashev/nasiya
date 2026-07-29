from __future__ import annotations

from app.otp.code import OtpCode

OTP_LOCALE_UZ_LATN = "uz-Latn"
OTP_LOCALE_RU = "ru"
SUPPORTED_OTP_MESSAGE_LOCALES = frozenset({OTP_LOCALE_UZ_LATN, OTP_LOCALE_RU})
OTP_MESSAGE_MAX_LENGTH = 512


def format_login_otp_message(
    *,
    code: OtpCode,
    ttl_seconds: int,
    locale: str,
) -> str:
    if not isinstance(code, OtpCode):
        raise ValueError("OTP message code is required")
    if ttl_seconds < 1:
        raise ValueError("OTP message TTL must be positive")

    normalized_locale = (
        locale if locale in SUPPORTED_OTP_MESSAGE_LOCALES else OTP_LOCALE_UZ_LATN
    )
    ttl_minutes = max(1, ttl_seconds // 60)
    code_value = code.as_internal_value()
    if normalized_locale == OTP_LOCALE_RU:
        message = (
            f"Kod: {code_value}\n"
            f"Srok deystviya: {ttl_minutes} min.\n"
            "Nikomu ne peredavayte etot kod.\n"
            "Esli vy ne zaprashivali kod, proignoriruyte eto soobshchenie."
        )
    else:
        message = (
            f"Kod: {code_value}\n"
            f"Amal qilish muddati: {ttl_minutes} daqiqa.\n"
            "Bu kodni hech kimga bermang.\n"
            "Agar kodni siz so'ramagan bo'lsangiz, xabarni e'tiborsiz qoldiring."
        )
    if len(message) > OTP_MESSAGE_MAX_LENGTH:
        raise ValueError("OTP message is too long")
    return message
