"""M13 capability, safe-view, and route/form contracts without router code."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.auth.error_codes import ErrorCode
from app.debt.contracts import DebtAggregate
from app.debt.enums import DebtStatus
from app.debt.values import DiscountBasisPoints, DiscountedAmountUZS, OriginalAmountUZS
from app.shop.enums import ShopRole, ShopStatus

__all__ = (
    "DEBT_ROUTE_CONTRACTS",
    "DebtCustomerCapability",
    "DebtCustomerCapabilityContext",
    "DebtRouteContract",
    "DebtSafeDetailProjection",
    "DebtSafeListProjection",
    "DebtShopCapability",
    "DebtShopCapabilityContext",
    "DebtWebLanguage",
    "customer_capabilities",
    "get_debt_web_error_message",
    "present_debt_detail",
    "present_debt_list_item",
    "shop_capabilities",
)


class DebtWebLanguage(StrEnum):
    UZ_LATN = "uz"
    RU = "ru"


_UZ_LATN_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.CUSTOMER_NOT_ACTIVE: "Mijoz hali faol emas.",
        ErrorCode.CUSTOMER_BLACKLISTED: "Mijoz uchun qarz yaratish mumkin emas.",
        ErrorCode.CUSTOMER_RATING_BLOCKED: (
            "Mijoz uchun qarz amali hozir mavjud emas."
        ),
        ErrorCode.CREDIT_LIMIT_EXCEEDED: "Kredit limiti oshib ketadi.",
        ErrorCode.MAX_OPEN_DEBTS: "Ochiq qarzlar limiti to'lgan.",
        ErrorCode.DEBT_UNAVAILABLE: "Qarz hozir mavjud emas.",
        ErrorCode.DEBT_NOT_PENDING: "Amal faqat kutilayotgan qarz uchun mumkin.",
        ErrorCode.DEBT_EXPIRED: "Qarzni qabul qilish muddati tugagan.",
        ErrorCode.IDEMPOTENCY_CONFLICT: (
            "Takroriy so'rov ma'lumotlari avvalgi so'rovga mos emas."
        ),
    }
)
_RU_ERRORS: Final[Mapping[ErrorCode, str]] = MappingProxyType(
    {
        ErrorCode.CUSTOMER_NOT_ACTIVE: "Клиент ещё не активен.",
        ErrorCode.CUSTOMER_BLACKLISTED: "Создание долга для клиента недоступно.",
        ErrorCode.CUSTOMER_RATING_BLOCKED: (
            "Операция с долгом для клиента сейчас недоступна."
        ),
        ErrorCode.CREDIT_LIMIT_EXCEEDED: "Кредитный лимит будет превышен.",
        ErrorCode.MAX_OPEN_DEBTS: "Лимит открытых долгов исчерпан.",
        ErrorCode.DEBT_UNAVAILABLE: "Долг сейчас недоступен.",
        ErrorCode.DEBT_NOT_PENDING: (
            "Операция доступна только для ожидающего решения долга."
        ),
        ErrorCode.DEBT_EXPIRED: "Срок принятия долга истёк.",
        ErrorCode.IDEMPOTENCY_CONFLICT: (
            "Данные повторного запроса не совпадают с предыдущим запросом."
        ),
    }
)


def get_debt_web_error_message(
    language: DebtWebLanguage,
    error_code: ErrorCode,
) -> str | None:
    if not isinstance(language, DebtWebLanguage):
        raise ValueError("Debt web language is invalid")
    if not isinstance(error_code, ErrorCode):
        raise ValueError("Debt error code is invalid")
    messages = _RU_ERRORS if language is DebtWebLanguage.RU else _UZ_LATN_ERRORS
    return messages.get(error_code)


class DebtShopCapability(StrEnum):
    LIST = "list"
    DETAIL = "detail"
    CREATE_FORM = "create_form"
    CREATE = "create"
    CANCEL = "cancel"


class DebtCustomerCapability(StrEnum):
    LIST = "list"
    DETAIL = "detail"
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DebtShopCapabilityContext:
    role: ShopRole | None
    shop_status: ShopStatus
    has_active_membership: bool
    is_platform_admin: bool = False

    def __post_init__(self) -> None:
        if self.role is not None and not isinstance(self.role, ShopRole):
            raise ValueError("Debt shop role is invalid")
        if not isinstance(self.shop_status, ShopStatus):
            raise ValueError("Debt shop status is invalid")
        if not isinstance(self.has_active_membership, bool):
            raise ValueError("Debt shop membership state is invalid")
        if not isinstance(self.is_platform_admin, bool):
            raise ValueError("Debt platform-admin state is invalid")


@dataclass(frozen=True, slots=True)
class DebtCustomerCapabilityContext:
    is_own_customer: bool
    is_customer_active: bool
    shop_status: ShopStatus
    is_platform_admin: bool = False

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, bool)
            for value in (
                self.is_own_customer,
                self.is_customer_active,
                self.is_platform_admin,
            )
        ):
            raise ValueError("Debt customer authority state is invalid")
        if not isinstance(self.shop_status, ShopStatus):
            raise ValueError("Debt customer shop status is invalid")


def shop_capabilities(
    context: DebtShopCapabilityContext,
) -> frozenset[DebtShopCapability]:
    if not isinstance(context, DebtShopCapabilityContext):
        raise ValueError("Debt shop capability context is invalid")
    if not context.has_active_membership or context.role is None:
        return frozenset()
    capabilities = {DebtShopCapability.LIST, DebtShopCapability.DETAIL}
    if context.shop_status is ShopStatus.ACTIVE:
        capabilities.update(
            {
                DebtShopCapability.CREATE_FORM,
                DebtShopCapability.CREATE,
                DebtShopCapability.CANCEL,
            }
        )
    return frozenset(capabilities)


def customer_capabilities(
    context: DebtCustomerCapabilityContext,
) -> frozenset[DebtCustomerCapability]:
    if not isinstance(context, DebtCustomerCapabilityContext):
        raise ValueError("Debt customer capability context is invalid")
    if not context.is_own_customer:
        return frozenset()
    capabilities = {DebtCustomerCapability.LIST, DebtCustomerCapability.DETAIL}
    if context.is_customer_active:
        capabilities.add(DebtCustomerCapability.REJECT)
        if context.shop_status is ShopStatus.ACTIVE:
            capabilities.add(DebtCustomerCapability.ACCEPT)
    return frozenset(capabilities)


@dataclass(frozen=True, slots=True)
class DebtSafeListProjection:
    status: DebtStatus
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: str
    pending_expires_at: str


@dataclass(frozen=True, slots=True)
class DebtSafeDetailProjection:
    status: DebtStatus
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: str
    pending_expires_at: str
    accepted_at: str | None
    rejected_at: str | None
    cancelled_at: str | None
    expired_at: str | None


def present_debt_list_item(debt: DebtAggregate) -> DebtSafeListProjection:
    if not isinstance(debt, DebtAggregate):
        raise ValueError("Debt list source is invalid")
    return DebtSafeListProjection(
        status=debt.status,
        original_amount=debt.original_amount,
        discount_basis_points=debt.discount_basis_points,
        discounted_amount=debt.discounted_amount,
        due_date=debt.due_date.isoformat(),
        pending_expires_at=debt.pending_expires_at.isoformat(),
    )


def present_debt_detail(debt: DebtAggregate) -> DebtSafeDetailProjection:
    list_item = present_debt_list_item(debt)
    return DebtSafeDetailProjection(
        status=list_item.status,
        original_amount=list_item.original_amount,
        discount_basis_points=list_item.discount_basis_points,
        discounted_amount=list_item.discounted_amount,
        due_date=list_item.due_date,
        pending_expires_at=list_item.pending_expires_at,
        accepted_at=_optional_iso(debt.accepted_at),
        rejected_at=_optional_iso(debt.rejected_at),
        cancelled_at=_optional_iso(debt.cancelled_at),
        expired_at=_optional_iso(debt.expired_at),
    )


def _optional_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class DebtRouteContract:
    method: str
    path: str
    form_fields: tuple[str, ...] = ()


DEBT_ROUTE_CONTRACTS: Final[tuple[DebtRouteContract, ...]] = (
    DebtRouteContract("GET", "/shop/customers/{shop_customer_id}/debts"),
    DebtRouteContract("GET", "/shop/customers/{shop_customer_id}/debts/new"),
    DebtRouteContract(
        "POST",
        "/shop/customers/{shop_customer_id}/debts",
        (
            "original_amount_uzs",
            "discount_percent",
            "due_date",
            "idempotency_key",
            "csrf_token",
        ),
    ),
    DebtRouteContract("GET", "/shop/debts/{debt_id}"),
    DebtRouteContract("POST", "/shop/debts/{debt_id}/cancel", ("reason", "csrf_token")),
    DebtRouteContract("GET", "/customer/debts"),
    DebtRouteContract("GET", "/customer/debts/{debt_id}"),
    DebtRouteContract(
        "POST",
        "/customer/debts/{debt_id}/accept",
        ("language", "displayed_offer_text_id", "csrf_token"),
    ),
    DebtRouteContract(
        "POST", "/customer/debts/{debt_id}/reject", ("reason", "csrf_token")
    ),
)
