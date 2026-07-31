from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.otp.web_presentation import (
    OtpWebLanguage,
    resolve_otp_web_language,
)

OFFER_WEB_LOCALE_COOKIE_NAME: Final = "nasiya_offer_locale"


class OfferWebLanguage(StrEnum):
    UZ_LATN = "uz"
    RU = "ru"


class OfferWebMessageCode(StrEnum):
    DRAFT_CREATED = "draft-created"
    TEXT_UPDATED = "text-updated"
    OFFER_APPROVED = "offer-approved"
    OFFER_MADE_CURRENT = "offer-made-current"
    OFFER_ALREADY_CURRENT = "offer-already-current"
    VALIDATION_ERROR = "validation-error"
    OFFER_NOT_DRAFT = "offer-not-draft"
    OFFER_INCOMPLETE = "offer-incomplete"
    OFFER_NOT_APPROVED = "offer-not-approved"
    OFFER_CHANGED = "offer-changed"
    OFFER_UNAVAILABLE = "offer-unavailable"
    ACCEPTANCE_RECORDED = "acceptance-recorded"
    ACCEPTANCE_REPLAYED = "acceptance-replayed"
    LEGAL_REVIEW_EVIDENCE_REQUIRED = "legal-review-evidence-required"


_MESSAGES: Final[Mapping[OfferWebLanguage, Mapping[OfferWebMessageCode, str]]] = (
    MappingProxyType(
        {
            OfferWebLanguage.UZ_LATN: MappingProxyType(
                {
                    OfferWebMessageCode.DRAFT_CREATED: (
                        "Yangi offer qoralamasi yaratildi."
                    ),
                    OfferWebMessageCode.TEXT_UPDATED: "Legal matn saqlandi.",
                    OfferWebMessageCode.OFFER_APPROVED: "Offer tasdiqlandi.",
                    OfferWebMessageCode.OFFER_MADE_CURRENT: (
                        "Offer joriy versiya qilindi."
                    ),
                    OfferWebMessageCode.OFFER_ALREADY_CURRENT: (
                        "Bu offer allaqachon joriy."
                    ),
                    OfferWebMessageCode.VALIDATION_ERROR: (
                        "Kiritilgan qiymatni tekshiring."
                    ),
                    OfferWebMessageCode.OFFER_NOT_DRAFT: (
                        "Faqat qoralama offerni tahrirlash mumkin."
                    ),
                    OfferWebMessageCode.OFFER_INCOMPLETE: (
                        "Offerning barcha legal til variantlarini to‘ldiring."
                    ),
                    OfferWebMessageCode.OFFER_NOT_APPROVED: (
                        "Faqat tasdiqlangan offerni joriy qilish mumkin."
                    ),
                    OfferWebMessageCode.OFFER_CHANGED: (
                        "Joriy offer o‘zgargan. Sahifani yangilang."
                    ),
                    OfferWebMessageCode.OFFER_UNAVAILABLE: (
                        "Joriy registration offer hozir mavjud emas."
                    ),
                    OfferWebMessageCode.ACCEPTANCE_RECORDED: (
                        "Registration offer qabul qilindi."
                    ),
                    OfferWebMessageCode.ACCEPTANCE_REPLAYED: (
                        "Bu registration offer avval qabul qilingan."
                    ),
                    OfferWebMessageCode.LEGAL_REVIEW_EVIDENCE_REQUIRED: (
                        "Tashqi legal review dalili talab qilinadi."
                    ),
                }
            ),
            OfferWebLanguage.RU: MappingProxyType(
                {
                    OfferWebMessageCode.DRAFT_CREATED: (
                        "Новый черновик оферты создан."
                    ),
                    OfferWebMessageCode.TEXT_UPDATED: "Юридический текст сохранён.",
                    OfferWebMessageCode.OFFER_APPROVED: "Оферта утверждена.",
                    OfferWebMessageCode.OFFER_MADE_CURRENT: (
                        "Оферта назначена текущей."
                    ),
                    OfferWebMessageCode.OFFER_ALREADY_CURRENT: (
                        "Эта оферта уже является текущей."
                    ),
                    OfferWebMessageCode.VALIDATION_ERROR: (
                        "Проверьте введённое значение."
                    ),
                    OfferWebMessageCode.OFFER_NOT_DRAFT: (
                        "Редактировать можно только черновик оферты."
                    ),
                    OfferWebMessageCode.OFFER_INCOMPLETE: (
                        "Заполните все языковые варианты оферты."
                    ),
                    OfferWebMessageCode.OFFER_NOT_APPROVED: (
                        "Текущей можно назначить только утверждённую оферту."
                    ),
                    OfferWebMessageCode.OFFER_CHANGED: (
                        "Текущая оферта изменилась. Обновите страницу."
                    ),
                    OfferWebMessageCode.OFFER_UNAVAILABLE: (
                        "Текущая регистрационная оферта сейчас недоступна."
                    ),
                    OfferWebMessageCode.ACCEPTANCE_RECORDED: (
                        "Регистрационная оферта принята."
                    ),
                    OfferWebMessageCode.ACCEPTANCE_REPLAYED: (
                        "Эта регистрационная оферта уже была принята."
                    ),
                    OfferWebMessageCode.LEGAL_REVIEW_EVIDENCE_REQUIRED: (
                        "Требуется подтверждение внешней юридической проверки."
                    ),
                }
            ),
        }
    )
)


def resolve_offer_web_language(
    locale_cookie: str | None,
    accept_language: str | None,
) -> OfferWebLanguage:
    inherited = resolve_otp_web_language(locale_cookie, accept_language)
    if inherited is OtpWebLanguage.RU:
        return OfferWebLanguage.RU
    return OfferWebLanguage.UZ_LATN


def get_offer_web_message(
    language: OfferWebLanguage,
    raw_code: str | None,
) -> str | None:
    try:
        code = OfferWebMessageCode(raw_code)
    except (TypeError, ValueError):
        return None
    return _MESSAGES[language][code]
