"""Typed identifiers and whole-UZS values for the bounded M12 domain."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final, NewType
from uuid import UUID

from app.shop.values import ShopId, UserId

__all__ = (
    "CustomerId",
    "CreditLimitUzbekistanSom",
    "DEFAULT_CREDIT_LIMIT_UZS",
    "DEFAULT_MAX_OPEN_DEBTS",
    "MAX_CREDIT_LIMIT_UZS",
    "MAX_OPEN_DEBTS",
    "MaxOpenDebts",
    "MIN_CREDIT_LIMIT_UZS",
    "ShopCustomerId",
    "ShopId",
    "UserId",
    "parse_credit_limit_uzs",
)

MIN_CREDIT_LIMIT_UZS: Final = Decimal("0")
MAX_CREDIT_LIMIT_UZS: Final = Decimal("1000000000000")
MAX_OPEN_DEBTS: Final = 100
_WHOLE_UZS_INPUT_PATTERN: Final = re.compile(r"[0-9]+", flags=re.ASCII)

CustomerId = NewType("CustomerId", UUID)


@dataclass(frozen=True, slots=True, repr=False)
class ShopCustomerId:
    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("Shop customer identity is invalid")

    def as_uuid(self) -> UUID:
        return self.value

    def __repr__(self) -> str:
        return "ShopCustomerId(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class CreditLimitUzbekistanSom:
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("Credit limit must be a Decimal")
        if not self.value.is_finite() or self.value.as_tuple().exponent != 0:
            raise ValueError("Credit limit must be whole UZS")
        if not MIN_CREDIT_LIMIT_UZS <= self.value <= MAX_CREDIT_LIMIT_UZS:
            raise ValueError("Credit limit is outside allowed bounds")


@dataclass(frozen=True, slots=True)
class MaxOpenDebts:
    value: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, int)
            or isinstance(self.value, bool)
            or not 1 <= self.value <= MAX_OPEN_DEBTS
        ):
            raise ValueError("Max open debts must be between 1 and 100")


DEFAULT_CREDIT_LIMIT_UZS: Final = CreditLimitUzbekistanSom(Decimal("1000000"))
DEFAULT_MAX_OPEN_DEBTS: Final = MaxOpenDebts(2)


def parse_credit_limit_uzs(value: str) -> CreditLimitUzbekistanSom:
    if not isinstance(value, str) or _WHOLE_UZS_INPUT_PATTERN.fullmatch(value) is None:
        raise ValueError("Credit limit must be ASCII whole UZS")
    return CreditLimitUzbekistanSom(Decimal(value))
