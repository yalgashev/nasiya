"""M14 payment authority, error-message, and route contracts without routers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage
from app.payment.enums import PaymentVoidReason
from app.shop.enums import ShopRole, ShopStatus

__all__ = (
    "PAYMENT_WEB_COPY",
    "PAYMENT_ROUTE_CONTRACTS",
    "M18_PAYMENT_VOID_ROUTE_CONTRACTS",
    "PaymentCustomerCapability",
    "PaymentCustomerCapabilityContext",
    "PaymentRouteContract",
    "CustomerPaymentVoidPresentation",
    "ShopPaymentVoidPresentation",
    "PaymentShopCapability",
    "PaymentShopCapabilityContext",
    "customer_payment_capabilities",
    "get_payment_web_error_message",
    "get_payment_web_copy",
    "get_payment_void_reason_label",
    "shop_payment_capabilities",
)


PAYMENT_WEB_COPY: Final[Mapping[DebtWebLanguage, Mapping[str, str]]] = MappingProxyType(
    {
        DebtWebLanguage.UZ_LATN: MappingProxyType(
            {
                "history": "To'lovlar tarixi",
                "new": "To'lov kiritish",
                "amount": "To'lov summasi (so'm)",
                "discounted_target": "Chegirmali qarz",
                "original_target": "Qarzning asl summasi",
                "discounted_basis": "Chegirmali hisob",
                "original_basis": "Asl summa bo'yicha hisob",
                "overdue": "To'lov muddati o'tgan",
                "late_terms": (
                    "Chegirma bekor qilindi; qoldiq asl summa bo'yicha hisoblanadi."
                ),
                "recovery_terms": (
                    "Undirishdan chiqarilgan qarz qoldig‘i asl summa bo‘yicha "
                    "qabul qilinadi."
                ),
                "paid_late": "Muddatdan keyin to'langan",
                "refresh": "Holatni yangilash",
                "navigation": "Sahifa navigatsiyasi",
                "posted_total": "To'langan jami",
                "remaining": "Qolgan qarz",
                "status": "Holat",
                "shop": "Do'kon",
                "payable": "To'lov qabul qilinadi",
                "yes": "Ha",
                "no": "Yo'q",
                "submit": "To'lovni qabul qilish",
                "receipt": "Kvitansiya",
                "method": "Usul",
                "cash": "Naqd",
                "card": "Karta",
                "transfer": "O'tkazma",
                "other": "Boshqa",
                "recorded_at": "Qabul qilingan vaqt",
                "historical_balance": "Ushbu to'lovdan keyingi qoldiq",
                "current_balance": "Hozirgi qoldiq",
                "current_status": "Hozirgi holat",
                "empty": "To'lovlar hali yo'q.",
                "read_only_suspended": "Do'kon faqat ko'rish rejimida.",
                "read_only_past_due": (
                    "To'lov muddati o'tgan; tarix faqat ko'rish uchun."
                ),
                "read_only_closed": ("Bu qarz uchun to'lov qabul qilinmaydi."),
                "customer_read_only": ("To'lovlar tarixi faqat ko'rish uchun."),
                "customer_payment_unavailable": "Mijoz to‘lov kiritolmaydi.",
                "status_pending": "Kutilmoqda",
                "status_active": "Faol",
                "status_paid": "To'langan",
                "status_rejected": "Rad etilgan",
                "status_cancelled": "Bekor qilingan",
                "status_expired": "Muddati tugagan",
                "status_overdue": "Muddati o'tgan",
                "status_written_off": "Undirishdan chiqarilgan",
                "status_written_off_settled": "Undirish qarzi yopilgan",
                "back_to_debt": "Qarzga qaytish",
                "back_to_history": "To'lovlar tarixiga qaytish",
                "void_reason_duplicate_payment": "Takroriy to'lov",
                "void_reason_incorrect_amount": "Noto'g'ri summa",
                "void_reason_incorrect_method": "Noto'g'ri usul",
                "void_reason_payment_not_received": "To'lov olinmagan",
                "void_reason_wrong_debt": "Noto'g'ri qarz",
            }
        ),
        DebtWebLanguage.RU: MappingProxyType(
            {
                "history": "История платежей",
                "new": "Внести платёж",
                "amount": "Сумма платежа (сум)",
                "discounted_target": "Сумма долга со скидкой",
                "original_target": "Первоначальная сумма долга",
                "discounted_basis": "Расчёт со скидкой",
                "original_basis": "Расчёт по первоначальной сумме",
                "overdue": "Срок оплаты истёк",
                "late_terms": (
                    "Скидка отменена; остаток рассчитан от первоначальной суммы."
                ),
                "recovery_terms": (
                    "Остаток списанного долга принимается по первоначальной сумме."
                ),
                "paid_late": "Оплачен после срока",
                "refresh": "Обновить данные",
                "navigation": "Навигация по странице",
                "posted_total": "Всего оплачено",
                "remaining": "Остаток долга",
                "status": "Статус",
                "shop": "Магазин",
                "payable": "Платёж принимается",
                "yes": "Да",
                "no": "Нет",
                "submit": "Принять платёж",
                "receipt": "Квитанция",
                "method": "Способ",
                "cash": "Наличные",
                "card": "Карта",
                "transfer": "Перевод",
                "other": "Другое",
                "recorded_at": "Время принятия",
                "historical_balance": "Остаток после этого платежа",
                "current_balance": "Текущий остаток",
                "current_status": "Текущий статус",
                "empty": "Платежей пока нет.",
                "read_only_suspended": ("Магазин доступен только для просмотра."),
                "read_only_past_due": (
                    "Срок платежа истёк; история доступна только для просмотра."
                ),
                "read_only_closed": "Платёж по этому долгу не принимается.",
                "customer_read_only": (
                    "История платежей доступна только для просмотра."
                ),
                "customer_payment_unavailable": "Клиент не может вносить платёж.",
                "status_pending": "Ожидается",
                "status_active": "Активен",
                "status_paid": "Оплачен",
                "status_rejected": "Отклонён",
                "status_cancelled": "Отменён",
                "status_expired": "Срок истёк",
                "status_overdue": "Срок оплаты истёк",
                "status_written_off": "Списан для взыскания",
                "status_written_off_settled": "Списанный долг погашен",
                "back_to_debt": "Вернуться к долгу",
                "back_to_history": "Вернуться к истории платежей",
                "void_reason_duplicate_payment": "Повторный платёж",
                "void_reason_incorrect_amount": "Неверная сумма",
                "void_reason_incorrect_method": "Неверный способ",
                "void_reason_payment_not_received": "Платёж не получен",
                "void_reason_wrong_debt": "Неверный долг",
            }
        ),
    }
)


def get_payment_web_copy(language: DebtWebLanguage) -> Mapping[str, str]:
    if not isinstance(language, DebtWebLanguage):
        raise ValueError("Payment web language is invalid")
    return PAYMENT_WEB_COPY[language]


def get_payment_void_reason_label(
    language: DebtWebLanguage, reason: PaymentVoidReason
) -> str:
    if not isinstance(reason, PaymentVoidReason):
        raise ValueError("Payment void reason is invalid")
    return get_payment_web_copy(language)[f"void_reason_{reason.value}"]


_UZ_LATN_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.PAYMENT_UNAVAILABLE: "To'lov hozir mavjud emas.",
        ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE: (
            "To'lov summasi qolgan qarzdan oshadi."
        ),
        ErrorCode.DEBT_CHANGED: "Qarz o'zgardi. Sahifani yangilang.",
        ErrorCode.DEBT_UNAVAILABLE: "Qarz hozir mavjud emas.",
        ErrorCode.DEBT_NOT_PAYABLE: "Bu qarz uchun to'lov hozir qabul qilinmaydi.",
        ErrorCode.IDEMPOTENCY_CONFLICT: (
            "Takroriy so'rov ma'lumotlari avvalgi so'rovga mos emas."
        ),
        ErrorCode.UNAUTHORIZED: "Kirish talab qilinadi.",
        ErrorCode.FORBIDDEN: "Bu amal uchun ruxsat yo'q.",
        ErrorCode.SHOP_SUSPENDED: "Do'kon vaqtincha faqat ko'rish rejimida.",
        ErrorCode.VALIDATION_ERROR: "Kiritilgan ma'lumotlarni tekshiring.",
        ErrorCode.CSRF_FAILED: "Forma muddati tugagan. Sahifani yangilang.",
    }
)
_RU_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.PAYMENT_UNAVAILABLE: "Платёж сейчас недоступен.",
        ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE: (
            "Сумма платежа превышает остаток долга."
        ),
        ErrorCode.DEBT_CHANGED: "Долг изменился. Обновите страницу.",
        ErrorCode.DEBT_UNAVAILABLE: "Долг сейчас недоступен.",
        ErrorCode.DEBT_NOT_PAYABLE: "Оплата этого долга сейчас недоступна.",
        ErrorCode.IDEMPOTENCY_CONFLICT: (
            "Данные повторного запроса не совпадают с предыдущим запросом."
        ),
        ErrorCode.UNAUTHORIZED: "Требуется вход.",
        ErrorCode.FORBIDDEN: "Недостаточно прав для этой операции.",
        ErrorCode.SHOP_SUSPENDED: "Магазин временно доступен только для чтения.",
        ErrorCode.VALIDATION_ERROR: "Проверьте введённые данные.",
        ErrorCode.CSRF_FAILED: "Срок формы истёк. Обновите страницу.",
    }
)


def get_payment_web_error_message(
    language: DebtWebLanguage,
    error_code: ErrorCode,
) -> str | None:
    if not isinstance(language, DebtWebLanguage):
        raise ValueError("Payment web language is invalid")
    if not isinstance(error_code, ErrorCode):
        raise ValueError("Payment error code is invalid")
    messages = _RU_ERRORS if language is DebtWebLanguage.RU else _UZ_LATN_ERRORS
    return messages.get(error_code)


class PaymentShopCapability(StrEnum):
    LIST = "list"
    RECEIPT = "receipt"
    CREATE_FORM = "create_form"
    CREATE = "create"
    VOID_FORM = "void_form"
    VOID = "void"


class PaymentCustomerCapability(StrEnum):
    LIST = "list"
    RECEIPT = "receipt"


@dataclass(frozen=True, slots=True)
class PaymentShopCapabilityContext:
    role: ShopRole | None
    shop_status: ShopStatus
    has_active_membership: bool
    is_platform_admin: bool = False

    def __post_init__(self) -> None:
        if self.role is not None and not isinstance(self.role, ShopRole):
            raise ValueError("Payment shop role is invalid")
        if not isinstance(self.shop_status, ShopStatus):
            raise ValueError("Payment shop status is invalid")
        if not isinstance(self.has_active_membership, bool):
            raise ValueError("Payment shop membership state is invalid")
        if not isinstance(self.is_platform_admin, bool):
            raise ValueError("Payment platform-admin state is invalid")


@dataclass(frozen=True, slots=True)
class PaymentCustomerCapabilityContext:
    """Own-customer read authority deliberately excludes risk-gate inputs."""

    is_own_customer: bool
    shop_status: ShopStatus

    def __post_init__(self) -> None:
        if not isinstance(self.is_own_customer, bool):
            raise ValueError("Payment customer ownership state is invalid")
        if not isinstance(self.shop_status, ShopStatus):
            raise ValueError("Payment customer shop status is invalid")


def shop_payment_capabilities(
    context: PaymentShopCapabilityContext,
) -> frozenset[PaymentShopCapability]:
    if not isinstance(context, PaymentShopCapabilityContext):
        raise ValueError("Payment shop capability context is invalid")
    if not context.has_active_membership or context.role is None:
        return frozenset()

    capabilities = {PaymentShopCapability.LIST, PaymentShopCapability.RECEIPT}
    if context.shop_status is ShopStatus.ACTIVE:
        capabilities.update(
            {PaymentShopCapability.CREATE_FORM, PaymentShopCapability.CREATE}
        )
        if context.role in {ShopRole.OWNER, ShopRole.MANAGER}:
            capabilities.update(
                {PaymentShopCapability.VOID_FORM, PaymentShopCapability.VOID}
            )
    return frozenset(capabilities)


def customer_payment_capabilities(
    context: PaymentCustomerCapabilityContext,
) -> frozenset[PaymentCustomerCapability]:
    if not isinstance(context, PaymentCustomerCapabilityContext):
        raise ValueError("Payment customer capability context is invalid")
    if not context.is_own_customer:
        return frozenset()
    return frozenset(
        {PaymentCustomerCapability.LIST, PaymentCustomerCapability.RECEIPT}
    )


@dataclass(frozen=True, slots=True)
class PaymentRouteContract:
    name: str
    method: str
    path: str
    form_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class ShopPaymentVoidPresentation:
    is_voided: bool
    voided_at: datetime | None
    reason_label: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.is_voided, bool):
            raise ValueError("Shop Payment void state is invalid")
        if self.is_voided != (
            self.voided_at is not None and self.reason_label is not None
        ):
            raise ValueError("Payment void presentation fields are incoherent")
        _validate_void_projection_time(self.voided_at)
        if self.reason_label is not None and (
            not isinstance(self.reason_label, str) or not self.reason_label.strip()
        ):
            raise ValueError("Shop Payment void reason label is invalid")
        if self.voided_at is not None:
            object.__setattr__(self, "voided_at", self.voided_at.astimezone(UTC))

    def __repr__(self) -> str:
        return "ShopPaymentVoidPresentation(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerPaymentVoidPresentation:
    is_voided: bool
    voided_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.is_voided, bool):
            raise ValueError("Customer Payment void state is invalid")
        if self.is_voided != (self.voided_at is not None):
            raise ValueError("Payment void presentation fields are incoherent")
        _validate_void_projection_time(self.voided_at)
        if self.voided_at is not None:
            object.__setattr__(self, "voided_at", self.voided_at.astimezone(UTC))

    def __repr__(self) -> str:
        return "CustomerPaymentVoidPresentation(<safe>)"


def _validate_void_projection_time(voided_at: datetime | None) -> None:
    if voided_at is not None and (
        not isinstance(voided_at, datetime)
        or voided_at.tzinfo is None
        or voided_at.utcoffset() is None
    ):
        raise ValueError("Payment void presentation time must be aware")


PAYMENT_ROUTE_CONTRACTS: Final[tuple[PaymentRouteContract, ...]] = (
    PaymentRouteContract(
        "shop_debt_payment_list", "GET", "/shop/debts/{debt_id}/payments"
    ),
    PaymentRouteContract(
        "shop_debt_payment_new", "GET", "/shop/debts/{debt_id}/payments/new"
    ),
    PaymentRouteContract(
        "shop_debt_payment_create",
        "POST",
        "/shop/debts/{debt_id}/payments",
        (
            "amount_uzs",
            "method",
            "idempotency_key",
            "expected_revision",
            "expected_balance_basis",
            "csrf_token",
        ),
    ),
    PaymentRouteContract("shop_payment_receipt", "GET", "/shop/payments/{payment_id}"),
    PaymentRouteContract(
        "customer_debt_payment_list", "GET", "/customer/debts/{debt_id}/payments"
    ),
    PaymentRouteContract(
        "customer_payment_receipt", "GET", "/customer/payments/{payment_id}"
    ),
)

M18_PAYMENT_VOID_ROUTE_CONTRACTS: Final[tuple[PaymentRouteContract, ...]] = (
    PaymentRouteContract(
        "shop_payment_void_form", "GET", "/shop/payments/{payment_id}/void"
    ),
    PaymentRouteContract(
        "shop_payment_void",
        "POST",
        "/shop/payments/{payment_id}/void",
        (
            "reason",
            "idempotency_key",
            "expected_revision",
            "confirmation",
            "csrf_token",
        ),
    ),
)
