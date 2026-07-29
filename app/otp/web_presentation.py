from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

OTP_LOCALE_COOKIE_NAME: Final = "nasiya_otp_locale"


class OtpWebLanguage(StrEnum):
    UZ_LATN = "uz"
    RU = "ru"


_UZ_LATN_COPY: Final[Mapping[str, str]] = MappingProxyType(
    {
        "request_page_title": "Kirish kodi",
        "request_heading": "Kirish kodini olish",
        "request_intro": (
            "Telefon raqamingizni kiriting. Agar mos hisob topilsa, kod "
            "Telegramga yuboriladi."
        ),
        "phone_label": "Telefon raqam",
        "phone_help": "Masalan: +998901234567 yoki 901234567.",
        "request_button": "Kod olish",
        "verify_page_title": "Kodni kiritish",
        "verify_heading": "Telegram kodini kiriting",
        "verify_notice": (
            "Agar kiritilgan telefon mos hisobga tegishli bo'lsa, kod "
            "Telegramga yuboriladi. Kod kelmasa, 60 soniyadan keyin yangi kod "
            "so'rang yoki parol bilan kiring."
        ),
        "code_label": "Olti xonali kod",
        "code_help": "Kod faqat raqamlardan iborat.",
        "verify_button": "Kirish",
        "new_code_button": "Yangi kod so'rash",
        "invalid_code_message": "Kod noto'g'ri yoki muddati tugagan.",
        "password_login": "Parol bilan kirish",
        "alternative_navigation": "Kirish usullari",
    }
)

_RU_COPY: Final[Mapping[str, str]] = MappingProxyType(
    {
        "request_page_title": "Код входа",
        "request_heading": "Получить код входа",
        "request_intro": (
            "Введите номер телефона. Если он соответствует аккаунту, код будет "
            "отправлен в Telegram."
        ),
        "phone_label": "Номер телефона",
        "phone_help": "Например: +998901234567 или 901234567.",
        "request_button": "Получить код",
        "verify_page_title": "Ввод кода",
        "verify_heading": "Введите код из Telegram",
        "verify_notice": (
            "Если введенный телефон соответствует аккаунту, код будет отправлен "
            "в Telegram. Если код не пришел, запросите новый через 60 секунд "
            "или войдите с паролем."
        ),
        "code_label": "Шестизначный код",
        "code_help": "Код состоит только из цифр.",
        "verify_button": "Войти",
        "new_code_button": "Запросить новый код",
        "invalid_code_message": "Код неверный или срок действия истек.",
        "password_login": "Войти с паролем",
        "alternative_navigation": "Способы входа",
    }
)

_COPY: Final[Mapping[OtpWebLanguage, Mapping[str, str]]] = MappingProxyType(
    {
        OtpWebLanguage.UZ_LATN: _UZ_LATN_COPY,
        OtpWebLanguage.RU: _RU_COPY,
    }
)


def resolve_otp_web_language(
    locale_cookie: str | None,
    accept_language: str | None,
) -> OtpWebLanguage:
    cookie_language = _parse_locale_value(locale_cookie)
    if cookie_language is not None:
        return cookie_language
    return _resolve_accept_language(accept_language)


def get_otp_web_copy(language: OtpWebLanguage) -> Mapping[str, str]:
    return _COPY[language]


def get_otp_dispatch_locale(language: OtpWebLanguage) -> str:
    if language is OtpWebLanguage.RU:
        return "ru"
    return "uz-Latn"


def _parse_locale_value(value: str | None) -> OtpWebLanguage | None:
    if value is None:
        return None
    normalized = value.strip().casefold().replace("_", "-")
    if normalized in {"uz", "uz-latn"}:
        return OtpWebLanguage.UZ_LATN
    if normalized in {"ru", "ru-ru"}:
        return OtpWebLanguage.RU
    return None


def _resolve_accept_language(accept_language: str | None) -> OtpWebLanguage:
    if accept_language is None:
        return OtpWebLanguage.UZ_LATN

    weighted: list[tuple[float, int, str]] = []
    for index, item in enumerate(accept_language.split(",")):
        pieces = [piece.strip() for piece in item.split(";")]
        language_tag = pieces[0].casefold()
        quality = 1.0
        for parameter in pieces[1:]:
            if parameter.casefold().startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        if language_tag and quality > 0:
            weighted.append((quality, -index, language_tag))

    for _quality, _position, language_tag in sorted(weighted, reverse=True):
        if language_tag == "ru" or language_tag.startswith("ru-"):
            return OtpWebLanguage.RU
        if language_tag == "uz" or language_tag.startswith("uz-"):
            return OtpWebLanguage.UZ_LATN
    return OtpWebLanguage.UZ_LATN
