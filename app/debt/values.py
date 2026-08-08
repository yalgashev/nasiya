"""Typed, redacted identifiers used by the debt domain."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

from app.shop.values import ShopId, UserId
from app.shop_customer.values import CustomerId, ShopCustomerId

__all__ = (
    "CustomerId",
    "DebtId",
    "DebtRevision",
    "DiscountBasisPoints",
    "DiscountedAmountUZS",
    "DiscountPercent",
    "MAX_DEBT_AMOUNT_UZS",
    "MAX_DISCOUNT_BASIS_POINTS",
    "OriginalAmountUZS",
    "ShopCustomerId",
    "ShopId",
    "UserId",
    "calculate_discounted_amount",
    "parse_discount_percent",
    "parse_original_amount_uzs",
)

MIN_DEBT_AMOUNT_UZS: Final = Decimal("1")
MAX_DEBT_AMOUNT_UZS: Final = Decimal("1000000000000")
MAX_DISCOUNT_BASIS_POINTS: Final = 10_000
_DISCOUNT_PERCENT_INPUT_PATTERN: Final = re.compile(
    r"[0-9]+(?:\.[0-9]{1,2})?", flags=re.ASCII
)


@dataclass(frozen=True, slots=True, repr=False)
class DebtId:
    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("Debt identity is invalid")

    def as_uuid(self) -> UUID:
        return self.value

    def __repr__(self) -> str:
        return "DebtId(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class DebtRevision:
    value: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, int)
            or isinstance(self.value, bool)
            or self.value < 1
        ):
            raise ValueError("Debt revision must be positive")


@dataclass(frozen=True, slots=True)
class OriginalAmountUZS:
    value: Decimal

    def __post_init__(self) -> None:
        _require_bounded_whole_uzs(self.value, field_name="Original amount")


@dataclass(frozen=True, slots=True)
class DiscountBasisPoints:
    value: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, int)
            or isinstance(self.value, bool)
            or not 0 <= self.value <= MAX_DISCOUNT_BASIS_POINTS
        ):
            raise ValueError("Discount basis points must be between 0 and 10000")


@dataclass(frozen=True, slots=True)
class DiscountPercent:
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("Discount percent must be a Decimal")
        if not self.value.is_finite() or self.value.as_tuple().exponent < -2:
            raise ValueError("Discount percent must have at most two decimal places")
        if not Decimal("0") <= self.value <= Decimal("100"):
            raise ValueError("Discount percent must be between 0 and 100")

    def as_basis_points(self) -> DiscountBasisPoints:
        return DiscountBasisPoints(int(self.value * 100))


@dataclass(frozen=True, slots=True)
class DiscountedAmountUZS:
    value: Decimal

    def __post_init__(self) -> None:
        _require_bounded_whole_uzs(self.value, field_name="Discounted amount")


def parse_discount_percent(value: str) -> DiscountPercent:
    if (
        not isinstance(value, str)
        or _DISCOUNT_PERCENT_INPUT_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("Discount percent must be ASCII decimal percentage")
    return DiscountPercent(Decimal(value))


def parse_original_amount_uzs(value: str) -> OriginalAmountUZS:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]+", value, flags=re.ASCII) is None
    ):
        raise ValueError("Original amount must be ASCII whole UZS")
    return OriginalAmountUZS(Decimal(value))


def calculate_discounted_amount(
    original_amount: OriginalAmountUZS,
    discount_basis_points: DiscountBasisPoints,
) -> DiscountedAmountUZS:
    if not isinstance(original_amount, OriginalAmountUZS):
        raise TypeError("Original amount must be an OriginalAmountUZS")
    if not isinstance(discount_basis_points, DiscountBasisPoints):
        raise TypeError("Discount basis points must be DiscountBasisPoints")

    result = (
        original_amount.value
        * (MAX_DISCOUNT_BASIS_POINTS - discount_basis_points.value)
        / MAX_DISCOUNT_BASIS_POINTS
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if result < MIN_DEBT_AMOUNT_UZS:
        raise ValueError("Discounted amount must be at least 1 UZS")
    return DiscountedAmountUZS(result)


def _require_bounded_whole_uzs(value: Decimal, *, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal")
    if not value.is_finite() or value.as_tuple().exponent != 0:
        raise ValueError(f"{field_name} must be whole UZS")
    if not MIN_DEBT_AMOUNT_UZS <= value <= MAX_DEBT_AMOUNT_UZS:
        raise ValueError(f"{field_name} is outside allowed bounds")
