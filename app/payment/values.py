"""Redacted payment identifiers and strict whole-UZS value contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final
from uuid import UUID

from app.debt.enums import M15_PERSISTED_STATUSES, DebtBalanceBasis, DebtStatus
from app.debt.values import (
    ClawbackIncreaseUZS,
    DiscountedAmountUZS,
    OriginalAmountUZS,
)

__all__ = (
    "ClawbackIncreaseUZS",
    "MAX_PAYMENT_AMOUNT_UZS",
    "MIN_PAYMENT_AMOUNT_UZS",
    "IncoherentPaymentLedgerError",
    "PaymentAmountUZS",
    "PaymentExposureUZS",
    "PaymentId",
    "PostedPaymentTotalUZS",
    "RemainingDueUZS",
    "calculate_payment_exposure",
    "calculate_clawback_increase",
    "calculate_discounted_remaining_due",
    "calculate_overdue_remaining_due",
    "calculate_remaining_due",
    "calculate_remaining_due_for_basis",
    "open_debt_count_contribution",
    "parse_payment_amount_uzs",
    "require_payment_amount_within_remaining",
)

MIN_PAYMENT_AMOUNT_UZS: Final = Decimal("1")
MAX_PAYMENT_AMOUNT_UZS: Final = Decimal("1000000000000")
_WHOLE_UZS_INPUT_PATTERN: Final = re.compile(r"[0-9]+", flags=re.ASCII)
_ZERO_UZS: Final = Decimal("0")


class IncoherentPaymentLedgerError(ValueError):
    """Raised when persisted payment totals cannot represent a lawful ledger."""


@dataclass(frozen=True, slots=True, repr=False)
class PaymentId:
    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("Payment identity is invalid")

    def as_uuid(self) -> UUID:
        return self.value

    def __repr__(self) -> str:
        return "PaymentId(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentAmountUZS:
    value: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _require_bounded_whole_uzs(self.value, field_name="Payment amount")

    def __repr__(self) -> str:
        return "PaymentAmountUZS(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class PostedPaymentTotalUZS:
    value: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonnegative_whole_uzs(self.value, field_name="Posted payment total")

    def __repr__(self) -> str:
        return "PostedPaymentTotalUZS(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class RemainingDueUZS:
    value: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonnegative_whole_uzs(self.value, field_name="Remaining due")

    def __repr__(self) -> str:
        return "RemainingDueUZS(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentExposureUZS:
    value: Decimal = field(repr=False)

    def __post_init__(self) -> None:
        _require_nonnegative_whole_uzs(self.value, field_name="Payment exposure")

    def __repr__(self) -> str:
        return "PaymentExposureUZS(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


def parse_payment_amount_uzs(value: str) -> PaymentAmountUZS:
    if not isinstance(value, str) or _WHOLE_UZS_INPUT_PATTERN.fullmatch(value) is None:
        raise ValueError("Payment amount must be ASCII whole UZS")
    return PaymentAmountUZS(Decimal(value))


def calculate_remaining_due(
    *,
    discounted_amount: DiscountedAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> RemainingDueUZS:
    """Backward-compatible name for discounted, on-time remaining."""

    return calculate_discounted_remaining_due(
        discounted_amount=discounted_amount,
        posted_total=posted_total,
    )


def calculate_discounted_remaining_due(
    *,
    discounted_amount: DiscountedAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> RemainingDueUZS:
    if not isinstance(discounted_amount, DiscountedAmountUZS):
        raise TypeError("Discounted amount must be a DiscountedAmountUZS")
    if not isinstance(posted_total, PostedPaymentTotalUZS):
        raise TypeError("Posted total must be a PostedPaymentTotalUZS")
    _require_coherent_posted_total(
        discounted_amount=discounted_amount, posted_total=posted_total
    )
    return RemainingDueUZS(discounted_amount.value - posted_total.value)


def calculate_overdue_remaining_due(
    *,
    original_amount: OriginalAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> RemainingDueUZS:
    if not isinstance(original_amount, OriginalAmountUZS):
        raise TypeError("Original amount must be an OriginalAmountUZS")
    if not isinstance(posted_total, PostedPaymentTotalUZS):
        raise TypeError("Posted total must be a PostedPaymentTotalUZS")
    if posted_total.value > original_amount.value:
        raise IncoherentPaymentLedgerError(
            "Posted payment total exceeds original amount"
        )
    return RemainingDueUZS(original_amount.value - posted_total.value)


def calculate_remaining_due_for_basis(
    *,
    basis: DebtBalanceBasis,
    original_amount: OriginalAmountUZS,
    discounted_amount: DiscountedAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> RemainingDueUZS:
    _require_debt_amount_relationship(
        original_amount=original_amount,
        discounted_amount=discounted_amount,
    )
    if not isinstance(basis, DebtBalanceBasis):
        raise TypeError("Balance basis must be a DebtBalanceBasis")
    if basis is DebtBalanceBasis.DISCOUNTED:
        return calculate_discounted_remaining_due(
            discounted_amount=discounted_amount,
            posted_total=posted_total,
        )
    return calculate_overdue_remaining_due(
        original_amount=original_amount,
        posted_total=posted_total,
    )


def calculate_clawback_increase(
    *,
    original_amount: OriginalAmountUZS,
    discounted_amount: DiscountedAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> ClawbackIncreaseUZS:
    _require_debt_amount_relationship(
        original_amount=original_amount,
        discounted_amount=discounted_amount,
    )
    calculate_discounted_remaining_due(
        discounted_amount=discounted_amount,
        posted_total=posted_total,
    )
    return ClawbackIncreaseUZS(original_amount.value - discounted_amount.value)


def calculate_payment_exposure(
    *,
    status: DebtStatus,
    original_amount: OriginalAmountUZS,
    discounted_amount: DiscountedAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> PaymentExposureUZS:
    _require_m15_debt_status(status)
    _require_debt_amount_relationship(
        original_amount=original_amount,
        discounted_amount=discounted_amount,
    )
    if not isinstance(posted_total, PostedPaymentTotalUZS):
        raise TypeError("Posted total must be a PostedPaymentTotalUZS")
    _require_status_coherent_posted_total(
        status=status,
        original_amount=original_amount,
        discounted_amount=discounted_amount,
        posted_total=posted_total,
    )

    if status is DebtStatus.PENDING:
        return PaymentExposureUZS(original_amount.value)
    if status in {DebtStatus.ACTIVE, DebtStatus.OVERDUE}:
        return PaymentExposureUZS(original_amount.value - posted_total.value)
    return PaymentExposureUZS(_ZERO_UZS)


def open_debt_count_contribution(status: DebtStatus) -> int:
    _require_m15_debt_status(status)
    return int(status in {DebtStatus.PENDING, DebtStatus.ACTIVE, DebtStatus.OVERDUE})


def require_payment_amount_within_remaining(
    *, amount: PaymentAmountUZS, remaining_due: RemainingDueUZS
) -> PaymentAmountUZS:
    """Return an amount only when it does not exceed a trusted whole-UZS balance."""

    if not isinstance(amount, PaymentAmountUZS):
        raise TypeError("Payment amount must be a PaymentAmountUZS")
    if not isinstance(remaining_due, RemainingDueUZS):
        raise TypeError("Remaining due must be a RemainingDueUZS")
    if amount.value > remaining_due.value:
        raise ValueError("Payment amount exceeds remaining due")
    return amount


def _require_m15_debt_status(status: DebtStatus) -> None:
    if not isinstance(status, DebtStatus) or status not in M15_PERSISTED_STATUSES:
        raise ValueError("Debt status is outside the M15 persisted subset")


def _require_coherent_posted_total(
    *,
    discounted_amount: DiscountedAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> None:
    if posted_total.value > discounted_amount.value:
        raise IncoherentPaymentLedgerError(
            "Posted payment total exceeds discounted amount"
        )


def _require_debt_amount_relationship(
    *,
    original_amount: OriginalAmountUZS,
    discounted_amount: DiscountedAmountUZS,
) -> None:
    if not isinstance(original_amount, OriginalAmountUZS):
        raise TypeError("Original amount must be an OriginalAmountUZS")
    if not isinstance(discounted_amount, DiscountedAmountUZS):
        raise TypeError("Discounted amount must be a DiscountedAmountUZS")
    if discounted_amount.value > original_amount.value:
        raise IncoherentPaymentLedgerError("Discounted amount exceeds original amount")


def _require_status_coherent_posted_total(
    *,
    status: DebtStatus,
    original_amount: OriginalAmountUZS,
    discounted_amount: DiscountedAmountUZS,
    posted_total: PostedPaymentTotalUZS,
) -> None:
    if status in {
        DebtStatus.PENDING,
        DebtStatus.REJECTED,
        DebtStatus.CANCELLED,
        DebtStatus.EXPIRED,
    }:
        if posted_total.value != _ZERO_UZS:
            raise IncoherentPaymentLedgerError(
                "Debt status cannot carry posted payments"
            )
        return
    if status is DebtStatus.ACTIVE:
        if posted_total.value >= discounted_amount.value:
            raise IncoherentPaymentLedgerError(
                "Active debt must retain a discounted-basis balance"
            )
        return
    if status is DebtStatus.OVERDUE:
        if posted_total.value >= original_amount.value:
            raise IncoherentPaymentLedgerError(
                "Overdue debt must retain an original-basis balance"
            )
        return
    if posted_total.value not in {
        discounted_amount.value,
        original_amount.value,
    }:
        raise IncoherentPaymentLedgerError(
            "Paid debt total does not match a lawful payoff basis"
        )


def _require_bounded_whole_uzs(value: Decimal, *, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal")
    if not value.is_finite() or value.as_tuple().exponent != 0:
        raise ValueError(f"{field_name} must be whole UZS")
    if not MIN_PAYMENT_AMOUNT_UZS <= value <= MAX_PAYMENT_AMOUNT_UZS:
        raise ValueError(f"{field_name} is outside allowed bounds")


def _require_nonnegative_whole_uzs(value: Decimal, *, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal")
    if not value.is_finite() or value.as_tuple().exponent != 0:
        raise ValueError(f"{field_name} must be whole UZS")
    if value < Decimal("0"):
        raise ValueError(f"{field_name} cannot be negative")
