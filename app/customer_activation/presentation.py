from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode
from app.customer_activation.contracts import (
    RegistrationReadinessComponent,
    RegistrationReadinessComponentStatus,
    RegistrationReadinessState,
    RegistrationReadinessView,
)
from app.otp.web_presentation import OtpWebLanguage, resolve_otp_web_language

CUSTOMER_ACTIVATION_LOCALE_COOKIE_NAME: Final = "nasiya_otp_locale"
CUSTOMER_ACTIVATION_PUBLIC_ERROR_CODES: Final = (
    ErrorCode.CSRF_FAILED,
    ErrorCode.RATE_LIMITED,
    ErrorCode.CUSTOMER_DRAFT_REQUIRED,
    ErrorCode.TELEGRAM_NOT_LINKED,
    ErrorCode.OFFER_UNAVAILABLE,
    ErrorCode.OTP_INVALID,
    ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED,
    ErrorCode.CUSTOMER_ACTIVATION_CHANGED,
    ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE,
    ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
    ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER,
)


@dataclass(frozen=True, slots=True)
class CustomerActivationReadinessPresentation:
    state: RegistrationReadinessState
    telegram_link: RegistrationReadinessComponentStatus
    current_offer_acceptance: RegistrationReadinessComponentStatus
    customer_identity: RegistrationReadinessComponentStatus
    current_document: RegistrationReadinessComponentStatus

    @property
    def completed(self) -> bool:
        return self.state is RegistrationReadinessState.ACTIVE

    @property
    def ready_for_otp(self) -> bool:
        return self.state is RegistrationReadinessState.READY_FOR_OTP


def present_customer_activation_readiness(
    readiness: RegistrationReadinessView,
) -> CustomerActivationReadinessPresentation:
    if not isinstance(readiness, RegistrationReadinessView):
        raise TypeError("Registration readiness view is invalid")
    statuses = {
        component.component: component.status for component in readiness.components
    }
    return CustomerActivationReadinessPresentation(
        state=readiness.state,
        telegram_link=statuses[RegistrationReadinessComponent.TELEGRAM_LINK],
        current_offer_acceptance=statuses[
            RegistrationReadinessComponent.OFFER_ACCEPTANCE
        ],
        customer_identity=statuses[RegistrationReadinessComponent.CUSTOMER_IDENTITY],
        current_document=statuses[RegistrationReadinessComponent.CUSTOMER_DOCUMENT],
    )


@dataclass(frozen=True, slots=True)
class CustomerActivationWebCopy:
    page_title: str
    heading: str
    intro: str
    telegram_link_label: str
    current_offer_label: str
    customer_identity_label: str
    current_document_label: str
    complete_label: str
    incomplete_label: str
    request_code_button: str
    code_label: str
    code_help: str
    verify_button: str
    new_code_button: str
    delivery_pending_notice: str
    completed_heading: str
    completed_body: str
    profile_link: str
    profile_status_heading: str
    profile_missing_status: str
    profile_draft_status: str
    profile_active_status: str
    profile_activation_link: str


_UZ_LATN_COPY: Final = CustomerActivationWebCopy(
    page_title="Mijozni faollashtirish",
    heading="Mijozni faollashtirish",
    intro="Faollashtirishdan oldin tayyorgarlik bosqichlarini tekshiring.",
    telegram_link_label="Telegram bog'langan",
    current_offer_label="Joriy taklif qabul qilingan",
    customer_identity_label="Shaxsiy ma'lumotlar to'liq",
    current_document_label="Joriy hujjat mavjud",
    complete_label="Tayyor",
    incomplete_label="Tugallanmagan",
    request_code_button="Faollashtirish kodini olish",
    code_label="Olti xonali kod",
    code_help="Telegram orqali kelgan kodni kiriting.",
    verify_button="Tasdiqlash va faollashtirish",
    new_code_button="Yangi kod so'rash",
    delivery_pending_notice="Kod Telegram orqali yuborish uchun qabul qilindi.",
    completed_heading="Mijoz faollashtirilgan",
    completed_body="Faollashtirish muvaffaqiyatli yakunlangan.",
    profile_link="Mijoz profiliga qaytish",
    profile_status_heading="Faollashtirish holati",
    profile_missing_status="Mijoz qoralamasi mavjud emas",
    profile_draft_status="Faollashtirishga tayyorgarlik",
    profile_active_status="Faollashtirilgan",
    profile_activation_link="Faollashtirish sahifasiga o'tish",
)

_RU_COPY: Final = CustomerActivationWebCopy(
    page_title="Активация клиента",
    heading="Активация клиента",
    intro="Перед активацией проверьте этапы готовности.",
    telegram_link_label="Telegram подключен",
    current_offer_label="Текущее предложение принято",
    customer_identity_label="Персональные данные заполнены",
    current_document_label="Текущий документ загружен",
    complete_label="Готово",
    incomplete_label="Не завершено",
    request_code_button="Получить код активации",
    code_label="Шестизначный код",
    code_help="Введите код, полученный в Telegram.",
    verify_button="Подтвердить и активировать",
    new_code_button="Запросить новый код",
    delivery_pending_notice="Код принят для отправки через Telegram.",
    completed_heading="Клиент активирован",
    completed_body="Активация успешно завершена.",
    profile_link="Вернуться к профилю клиента",
    profile_status_heading="Статус активации",
    profile_missing_status="Черновик клиента отсутствует",
    profile_draft_status="Подготовка к активации",
    profile_active_status="Активирован",
    profile_activation_link="Перейти к активации",
)

_COPY: Final[Mapping[OtpWebLanguage, CustomerActivationWebCopy]] = MappingProxyType(
    {
        OtpWebLanguage.UZ_LATN: _UZ_LATN_COPY,
        OtpWebLanguage.RU: _RU_COPY,
    }
)

_UZ_LATN_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.CSRF_FAILED: (
            "So'rov tasdiqlanmadi. Sahifani yangilab qayta urinib ko'ring."
        ),
        ErrorCode.RATE_LIMITED: "Juda ko'p urinish. Keyinroq qayta urinib ko'ring.",
        ErrorCode.CUSTOMER_DRAFT_REQUIRED: "Mijoz qoralamasi talab qilinadi.",
        ErrorCode.TELEGRAM_NOT_LINKED: "Avval Telegram akkauntingizni bog'lang.",
        ErrorCode.OFFER_UNAVAILABLE: "Joriy taklif hozir mavjud emas.",
        ErrorCode.OTP_INVALID: "Kod noto'g'ri yoki muddati tugagan.",
        ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED: (
            "Joriy ro'yxatdan o'tish taklifini qabul qiling."
        ),
        ErrorCode.CUSTOMER_ACTIVATION_CHANGED: (
            "Faollashtirish ma'lumotlari o'zgargan. Yangi kod so'rang."
        ),
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE: (
            "Shaxsiy ma'lumotlar hozir mavjud emas."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE: "Hujjat hozir mavjud emas.",
        ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER: (
            "Faol mijoz uchun Telegram bog'lanishi saqlanishi kerak."
        ),
    }
)

_RU_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.CSRF_FAILED: "Запрос не подтвержден. Обновите страницу и повторите.",
        ErrorCode.RATE_LIMITED: "Слишком много попыток. Повторите позже.",
        ErrorCode.CUSTOMER_DRAFT_REQUIRED: "Требуется черновик клиента.",
        ErrorCode.TELEGRAM_NOT_LINKED: "Сначала подключите аккаунт Telegram.",
        ErrorCode.OFFER_UNAVAILABLE: "Текущее предложение сейчас недоступно.",
        ErrorCode.OTP_INVALID: "Код неверный или срок его действия истек.",
        ErrorCode.REGISTRATION_OFFER_NOT_ACCEPTED: (
            "Примите текущее предложение для регистрации."
        ),
        ErrorCode.CUSTOMER_ACTIVATION_CHANGED: (
            "Данные активации изменились. Запросите новый код."
        ),
        ErrorCode.CUSTOMER_IDENTITY_UNAVAILABLE: (
            "Персональные данные сейчас недоступны."
        ),
        ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE: "Документ сейчас недоступен.",
        ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER: (
            "Для активного клиента связь с Telegram должна сохраняться."
        ),
    }
)


def resolve_customer_activation_language(
    locale_cookie: str | None,
    accept_language: str | None,
) -> OtpWebLanguage:
    return resolve_otp_web_language(locale_cookie, accept_language)


def get_customer_activation_copy(
    language: OtpWebLanguage,
) -> CustomerActivationWebCopy:
    return _COPY[language]


def get_customer_activation_error_message(
    language: OtpWebLanguage,
    error_code: ErrorCode,
) -> str | None:
    messages = _RU_ERRORS if language is OtpWebLanguage.RU else _UZ_LATN_ERRORS
    return messages.get(error_code)
