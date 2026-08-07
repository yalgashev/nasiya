"""Feature-local, identifier-safe UZ-Latn and Russian M12 web presentation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode
from app.otp.web_presentation import OtpWebLanguage, resolve_otp_web_language


class ShopCustomerWebLanguage(StrEnum):
    UZ_LATN = "uz"
    RU = "ru"


@dataclass(frozen=True, slots=True)
class ShopCustomerWebCopy:
    page_title: str
    roster_heading: str
    link_heading: str
    phone_label: str
    link_button: str
    defaults_heading: str
    credit_limit_label: str
    max_open_debts_label: str
    list_status_label: str
    save_button: str
    linked_notice: str
    updated_notice: str
    customer_shops_heading: str
    no_linked_shops: str
    empty_roster: str
    read_only_notice: str
    workspace_link: str
    profile_link: str
    already_linked_notice: str
    unchanged_notice: str
    normal_status: str
    whitelisted_status: str
    blacklisted_status: str


_UZ_LATN_COPY: Final = ShopCustomerWebCopy(
    page_title="Do'kon mijozlari",
    roster_heading="Mijozlar ro'yxati",
    link_heading="Mijozni bog'lash",
    phone_label="Telefon raqam",
    link_button="Bog'lash",
    defaults_heading="Yangi mijozlar uchun kredit sozlamalari",
    credit_limit_label="Kredit limiti (UZS)",
    max_open_debts_label="Maksimal ochiq qarzlar",
    list_status_label="Ro'yxat holati",
    save_button="Saqlash",
    linked_notice="Mijoz do'konga bog'landi.",
    updated_notice="Sozlamalar saqlandi.",
    customer_shops_heading="Bog'langan do'konlar",
    no_linked_shops="Bog'langan do'konlar yo'q.",
    empty_roster="Do'kon mijozlari hali yo'q.",
    read_only_notice="faqat ko'rish rejimi",
    workspace_link="Ish joyi",
    profile_link="Mijoz profiliga qaytish",
    already_linked_notice="Mijoz avval bog'langan.",
    unchanged_notice="O'zgarish topilmadi.",
    normal_status="Oddiy",
    whitelisted_status="Oq ro'yxat",
    blacklisted_status="Qora ro'yxat",
)
_RU_COPY: Final = ShopCustomerWebCopy(
    page_title="Клиенты магазина",
    roster_heading="Список клиентов",
    link_heading="Привязать клиента",
    phone_label="Номер телефона",
    link_button="Привязать",
    defaults_heading="Кредитные настройки для новых клиентов",
    credit_limit_label="Кредитный лимит (UZS)",
    max_open_debts_label="Максимум открытых долгов",
    list_status_label="Статус списка",
    save_button="Сохранить",
    linked_notice="Клиент привязан к магазину.",
    updated_notice="Настройки сохранены.",
    customer_shops_heading="Привязанные магазины",
    no_linked_shops="Привязанных магазинов нет.",
    empty_roster="Клиентов магазина пока нет.",
    read_only_notice="Режим только для просмотра.",
    workspace_link="Рабочее место",
    profile_link="Вернуться к профилю клиента",
    already_linked_notice="Клиент уже привязан.",
    unchanged_notice="Изменений нет.",
    normal_status="Обычный",
    whitelisted_status="Белый список",
    blacklisted_status="Черный список",
)
_COPY: Final[Mapping[ShopCustomerWebLanguage, ShopCustomerWebCopy]] = MappingProxyType(
    {
        ShopCustomerWebLanguage.UZ_LATN: _UZ_LATN_COPY,
        ShopCustomerWebLanguage.RU: _RU_COPY,
    }
)

_UZ_LATN_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.CSRF_FAILED: "So'rov tasdiqlanmadi. Sahifani yangilang.",
        ErrorCode.RATE_LIMITED: "Juda ko'p urinish. Keyinroq qayta urinib ko'ring.",
        ErrorCode.VALIDATION_ERROR: "Kiritilgan ma'lumotlarni tekshiring.",
        ErrorCode.FORBIDDEN: "Bu amal uchun ruxsat yo'q.",
        ErrorCode.SHOP_SUSPENDED: "Do'kon vaqtincha to'xtatilgan.",
        ErrorCode.CUSTOMER_LINK_UNAVAILABLE: "Mijozni bog'lash hozir mavjud emas.",
        ErrorCode.SHOP_CUSTOMER_UNAVAILABLE: "Mijoz bog'lanishi mavjud emas.",
        ErrorCode.SHOP_CUSTOMER_CHANGED: (
            "Mijoz bog'lanishi o'zgargan. Sahifani yangilang."
        ),
    }
)
_RU_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.CSRF_FAILED: "Запрос не подтвержден. Обновите страницу.",
        ErrorCode.RATE_LIMITED: "Слишком много попыток. Повторите позже.",
        ErrorCode.VALIDATION_ERROR: "Проверьте введенные данные.",
        ErrorCode.FORBIDDEN: "Нет разрешения на это действие.",
        ErrorCode.SHOP_SUSPENDED: "Магазин временно приостановлен.",
        ErrorCode.CUSTOMER_LINK_UNAVAILABLE: "Привязка клиента сейчас недоступна.",
        ErrorCode.SHOP_CUSTOMER_UNAVAILABLE: "Привязка клиента недоступна.",
        ErrorCode.SHOP_CUSTOMER_CHANGED: (
            "Привязка клиента изменилась. Обновите страницу."
        ),
    }
)


def resolve_shop_customer_web_language(
    locale_cookie: str | None,
    accept_language: str | None,
) -> ShopCustomerWebLanguage:
    inherited = resolve_otp_web_language(locale_cookie, accept_language)
    if inherited is OtpWebLanguage.RU:
        return ShopCustomerWebLanguage.RU
    return ShopCustomerWebLanguage.UZ_LATN


def get_shop_customer_web_copy(
    language: ShopCustomerWebLanguage,
) -> ShopCustomerWebCopy:
    return _COPY[language]


def get_shop_customer_web_error_message(
    language: ShopCustomerWebLanguage,
    error_code: ErrorCode,
) -> str | None:
    messages = _RU_ERRORS if language is ShopCustomerWebLanguage.RU else _UZ_LATN_ERRORS
    return messages.get(error_code)
