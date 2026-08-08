"""M14 payment authority, error-message, and route contracts without routers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage
from app.shop.enums import ShopRole, ShopStatus

__all__ = (
    "PAYMENT_ROUTE_CONTRACTS",
    "PaymentCustomerCapability",
    "PaymentCustomerCapabilityContext",
    "PaymentRouteContract",
    "PaymentShopCapability",
    "PaymentShopCapabilityContext",
    "customer_payment_capabilities",
    "get_payment_web_error_message",
    "shop_payment_capabilities",
)


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
