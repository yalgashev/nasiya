from __future__ import annotations

from app.otp.code import OtpCode
from app.otp.contracts import OtpPurpose

OTP_LOCALE_UZ_LATN = "uz-Latn"
OTP_LOCALE_RU = "ru"
SUPPORTED_OTP_MESSAGE_LOCALES = frozenset({OTP_LOCALE_UZ_LATN, OTP_LOCALE_RU})
OTP_MESSAGE_MAX_LENGTH = 512


class OtpTelegramMessage:
    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        if not isinstance(text, str) or not text or len(text) > OTP_MESSAGE_MAX_LENGTH:
            raise ValueError("OTP message is invalid")
        self._text = text

    def as_send_text(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return "OtpTelegramMessage(<redacted>)"

    def __str__(self) -> str:
        return "<redacted-otp-telegram-message>"


def prepare_otp_telegram_message(
    *,
    purpose: OtpPurpose,
    code: OtpCode,
    ttl_seconds: int,
    locale: str,
) -> OtpTelegramMessage:
    if not isinstance(purpose, OtpPurpose):
        raise ValueError("OTP message purpose is invalid")
    formatter = (
        format_login_otp_message
        if purpose is OtpPurpose.LOGIN
        else format_registration_otp_message
    )
    return OtpTelegramMessage(
        formatter(code=code, ttl_seconds=ttl_seconds, locale=locale)
    )


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


def format_registration_otp_message(
    *,
    code: OtpCode,
    ttl_seconds: int,
    locale: str,
) -> str:
    if not isinstance(code, OtpCode):
        raise ValueError("OTP message code is required")
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool):
        raise ValueError("OTP message TTL must be positive")
    if ttl_seconds < 1:
        raise ValueError("OTP message TTL must be positive")

    normalized_locale = (
        locale if locale in SUPPORTED_OTP_MESSAGE_LOCALES else OTP_LOCALE_UZ_LATN
    )
    ttl_minutes = max(1, ttl_seconds // 60)
    code_value = code.as_internal_value()
    if normalized_locale == OTP_LOCALE_RU:
        message = (
            f"Kod aktivatsii: {code_value}\n"
            f"Srok deystviya: {ttl_minutes} min.\n"
            "Nikomu ne peredavayte etot kod.\n"
            "Esli vy ne zaprashivali aktivatsiyu, "
            "proignoriruyte eto soobshchenie."
        )
    else:
        message = (
            f"Faollashtirish kodi: {code_value}\n"
            f"Amal qilish muddati: {ttl_minutes} daqiqa.\n"
            "Bu kodni hech kimga bermang.\n"
            "Agar faollashtirishni siz so'ramagan bo'lsangiz, "
            "xabarni e'tiborsiz qoldiring."
        )
    if len(message) > OTP_MESSAGE_MAX_LENGTH:
        raise ValueError("OTP message is too long")
    return message
