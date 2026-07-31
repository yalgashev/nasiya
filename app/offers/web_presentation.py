from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
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


@dataclass(frozen=True, slots=True)
class OfferAdminListCopy:
    page_title: str
    heading: str
    intro: str
    create_link: str
    versions_heading: str
    status_label: str
    legal_languages_label: str
    no_text: str
    completeness_label: str
    complete: str
    incomplete: str
    empty: str


@dataclass(frozen=True, slots=True)
class OfferAdminCreateCopy:
    page_title: str
    navigation_label: str
    back: str
    heading: str
    intro: str
    purpose_label: str
    create_button: str


@dataclass(frozen=True, slots=True)
class OfferAdminDetailCopy:
    page_title_prefix: str
    navigation_label: str
    back: str
    status_label: str
    completeness_label: str
    legal_review_reference_label: str
    legal_texts_heading: str
    title_label: str
    body_label: str
    save_text_button: str
    approval_heading: str
    missing_languages_label: str
    review_authority_label: str
    reviewed_at_label: str
    review_reference_label: str
    approve_button: str
    current_heading: str
    current_offer_label: str
    no_current: str
    make_current_guidance: str
    already_current_button: str
    make_current_button: str


@dataclass(frozen=True, slots=True)
class RegistrationOfferPageCopy:
    page_title: str
    heading: str
    language_navigation_label: str
    accept_button: str


@dataclass(frozen=True, slots=True)
class OfferWebCopy:
    admin_list: OfferAdminListCopy
    admin_create: OfferAdminCreateCopy
    admin_detail: OfferAdminDetailCopy
    registration: RegistrationOfferPageCopy
    account_registration_offer_link: str
    purpose_labels: Mapping[OfferPurpose, str]
    status_labels: Mapping[OfferStatus, str]
    legal_language_labels: Mapping[OfferLanguage, str]


_UZ_LATN_COPY: Final = OfferWebCopy(
    admin_list=OfferAdminListCopy(
        page_title="Offer versiyalari",
        heading="Offer versiyalari",
        intro="Platform darajasidagi legal offer versiyalari.",
        create_link="Yangi qoralama yaratish",
        versions_heading="Versiyalar",
        status_label="Holat",
        legal_languages_label="Legal tillar",
        no_text="Hali matn yo‘q",
        completeness_label="To‘liqlik",
        complete="To‘liq",
        incomplete="To‘liq emas",
        empty="Offer versiyalari hali yaratilmagan.",
    ),
    admin_create=OfferAdminCreateCopy(
        page_title="Yangi offer qoralamasi",
        navigation_label="Offer navigatsiyasi",
        back="← Offer versiyalariga qaytish",
        heading="Yangi offer qoralamasi",
        intro="Versiya raqami server tomonidan avtomatik belgilanadi.",
        purpose_label="Offer maqsadi",
        create_button="Qoralama yaratish",
    ),
    admin_detail=OfferAdminDetailCopy(
        page_title_prefix="Offer",
        navigation_label="Offer navigatsiyasi",
        back="← Offer versiyalariga qaytish",
        status_label="Holat",
        completeness_label="To‘liqlik",
        legal_review_reference_label="Legal tekshiruv identifikatori",
        legal_texts_heading="Legal matnlar",
        title_label="Sarlavha",
        body_label="Legal matn",
        save_text_button="Matnni saqlash",
        approval_heading="Offerni tasdiqlash",
        missing_languages_label="Yetishmayotgan tillar",
        review_authority_label="Tekshiruvchi / vakolatli tomon",
        reviewed_at_label="Ko‘rib chiqilgan vaqt (UTC)",
        review_reference_label="Tekshiruv identifikatori",
        approve_button="Offerni tasdiqlash",
        current_heading="Joriy versiyani almashtirish",
        current_offer_label="Hozirgi joriy offer",
        no_current="Bu maqsad uchun joriy offer yo‘q.",
        make_current_guidance=(
            "Ushbu tasdiqlangan versiyani joriy offer sifatida belgilang."
        ),
        already_current_button="Allaqachon joriy",
        make_current_button="Joriy versiya qilish",
    ),
    registration=RegistrationOfferPageCopy(
        page_title="Ro‘yxatdan o‘tish ofertasi",
        heading="Ro‘yxatdan o‘tish ofertasi",
        language_navigation_label="Legal matn tili",
        accept_button="Ro‘yxatdan o‘tish ofertasini qabul qilish",
    ),
    account_registration_offer_link="Ro‘yxatdan o‘tish ofertasini ko‘rish",
    purpose_labels=MappingProxyType(
        {
            OfferPurpose.REGISTRATION: "Ro‘yxatdan o‘tish",
            OfferPurpose.DEBT_ACCEPTANCE: "Qarz qabul qilish",
        }
    ),
    status_labels=MappingProxyType(
        {
            OfferStatus.DRAFT: "Qoralama",
            OfferStatus.APPROVED: "Tasdiqlangan",
            OfferStatus.CURRENT: "Joriy",
        }
    ),
    legal_language_labels=MappingProxyType(
        {
            OfferLanguage.UZ_LATN: "O‘zbekcha (lotin)",
            OfferLanguage.UZ_CYRL: "O‘zbekcha (kirill)",
            OfferLanguage.RU: "Ruscha",
        }
    ),
)

_RU_COPY: Final = OfferWebCopy(
    admin_list=OfferAdminListCopy(
        page_title="Версии оферты",
        heading="Версии оферты",
        intro="Версии юридической оферты уровня платформы.",
        create_link="Создать новый черновик",
        versions_heading="Версии",
        status_label="Статус",
        legal_languages_label="Языки юридического текста",
        no_text="Текст ещё не добавлен",
        completeness_label="Полнота",
        complete="Полная",
        incomplete="Неполная",
        empty="Версии оферты ещё не созданы.",
    ),
    admin_create=OfferAdminCreateCopy(
        page_title="Новый черновик оферты",
        navigation_label="Навигация оферт",
        back="← Вернуться к версиям оферты",
        heading="Новый черновик оферты",
        intro="Номер версии назначается сервером автоматически.",
        purpose_label="Назначение оферты",
        create_button="Создать черновик",
    ),
    admin_detail=OfferAdminDetailCopy(
        page_title_prefix="Оферта",
        navigation_label="Навигация оферт",
        back="← Вернуться к версиям оферты",
        status_label="Статус",
        completeness_label="Полнота",
        legal_review_reference_label="Ссылка на юридическую проверку",
        legal_texts_heading="Юридические тексты",
        title_label="Заголовок",
        body_label="Юридический текст",
        save_text_button="Сохранить текст",
        approval_heading="Утверждение оферты",
        missing_languages_label="Отсутствующие языки",
        review_authority_label="Проверяющий / организация",
        reviewed_at_label="Время проверки (UTC)",
        review_reference_label="Ссылка на проверку",
        approve_button="Утвердить оферту",
        current_heading="Смена текущей версии",
        current_offer_label="Текущая оферта",
        no_current="Для этого назначения нет текущей оферты.",
        make_current_guidance=("Назначьте эту утверждённую версию текущей офертой."),
        already_current_button="Уже текущая",
        make_current_button="Назначить текущей",
    ),
    registration=RegistrationOfferPageCopy(
        page_title="Регистрационная оферта",
        heading="Регистрационная оферта",
        language_navigation_label="Язык юридического текста",
        accept_button="Принять регистрационную оферту",
    ),
    account_registration_offer_link="Посмотреть регистрационную оферту",
    purpose_labels=MappingProxyType(
        {
            OfferPurpose.REGISTRATION: "Регистрация",
            OfferPurpose.DEBT_ACCEPTANCE: "Принятие долга",
        }
    ),
    status_labels=MappingProxyType(
        {
            OfferStatus.DRAFT: "Черновик",
            OfferStatus.APPROVED: "Утверждена",
            OfferStatus.CURRENT: "Текущая",
        }
    ),
    legal_language_labels=MappingProxyType(
        {
            OfferLanguage.UZ_LATN: "Узбекский (латиница)",
            OfferLanguage.UZ_CYRL: "Узбекский (кириллица)",
            OfferLanguage.RU: "Русский",
        }
    ),
)

_COPY: Final[Mapping[OfferWebLanguage, OfferWebCopy]] = MappingProxyType(
    {
        OfferWebLanguage.UZ_LATN: _UZ_LATN_COPY,
        OfferWebLanguage.RU: _RU_COPY,
    }
)

_LEGAL_LANGUAGE_TAGS: Final[Mapping[OfferLanguage, str]] = MappingProxyType(
    {
        OfferLanguage.UZ_LATN: "uz-Latn",
        OfferLanguage.UZ_CYRL: "uz-Cyrl",
        OfferLanguage.RU: "ru",
    }
)


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


def get_offer_web_copy(language: OfferWebLanguage) -> OfferWebCopy:
    return _COPY[language]


def get_offer_legal_language_tag(language: OfferLanguage) -> str:
    return _LEGAL_LANGUAGE_TAGS[language]
