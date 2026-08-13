"""Pure M13 creation eligibility, without payment or rating behavior."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.debt.enums import DebtStatus
from app.debt.values import CustomerId, OriginalAmountUZS, ShopCustomerId
from app.shop_customer.contracts import DebtlessShopCustomerPolicyProjection
from app.shop_customer.enums import ShopCustomerListStatus

__all__ = (
    "DebtCreationEligibilityDecision",
    "DebtCreationEligibilityInput",
    "GlobalHardBlockProjection",
    "GlobalHardBlockReadPort",
    "OpenDebtCount",
    "OpenDebtCountReadPort",
    "OpenDebtExposure",
    "OpenDebtExposureReadPort",
    "decide_debt_creation_eligibility",
    "is_unresolved_persisted_hard_block_status",
)


def is_unresolved_persisted_hard_block_status(status: DebtStatus) -> bool:
    """Return the debt-derived persisted overlay, never a score-derived block."""

    if not isinstance(status, DebtStatus):
        raise ValueError("Hard-block Debt status is invalid")
    return status in {DebtStatus.OVERDUE, DebtStatus.WRITTEN_OFF}


@dataclass(frozen=True, slots=True)
class OpenDebtExposure:
    """Full original-amount exposure of pending and active debts only."""

    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError("Open debt exposure must be a Decimal")
        if not self.value.is_finite() or self.value.as_tuple().exponent != 0:
            raise ValueError("Open debt exposure must be whole UZS")
        if self.value < Decimal("0"):
            raise ValueError("Open debt exposure cannot be negative")


@dataclass(frozen=True, slots=True)
class OpenDebtCount:
    value: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, int)
            or isinstance(self.value, bool)
            or self.value < 0
        ):
            raise ValueError("Open debt count cannot be negative")


@dataclass(frozen=True, slots=True)
class GlobalHardBlockProjection:
    is_blocked: bool

    def __post_init__(self) -> None:
        if not isinstance(self.is_blocked, bool):
            raise ValueError("Global hard-block state is invalid")


class DebtCreationEligibilityDecision(StrEnum):
    ALLOWED = "allowed"
    CUSTOMER_BLACKLISTED = "customer_blacklisted"
    CUSTOMER_RATING_BLOCKED = "customer_rating_blocked"
    CREDIT_LIMIT_EXCEEDED = "credit_limit_exceeded"
    MAX_OPEN_DEBTS = "max_open_debts"


@dataclass(frozen=True, slots=True)
class DebtCreationEligibilityInput:
    policy: DebtlessShopCustomerPolicyProjection
    open_exposure: OpenDebtExposure
    open_count: OpenDebtCount
    global_hard_block: GlobalHardBlockProjection
    original_amount: OriginalAmountUZS

    def __post_init__(self) -> None:
        if not isinstance(self.policy, DebtlessShopCustomerPolicyProjection):
            raise ValueError("Debt eligibility policy is invalid")
        if not isinstance(self.open_exposure, OpenDebtExposure):
            raise ValueError("Debt eligibility exposure is invalid")
        if not isinstance(self.open_count, OpenDebtCount):
            raise ValueError("Debt eligibility open count is invalid")
        if not isinstance(self.global_hard_block, GlobalHardBlockProjection):
            raise ValueError("Debt eligibility hard-block state is invalid")
        if not isinstance(self.original_amount, OriginalAmountUZS):
            raise ValueError("Debt eligibility original amount is invalid")


@runtime_checkable
class OpenDebtExposureReadPort(Protocol):
    def read_open_debt_exposure(
        self, *, shop_customer_id: ShopCustomerId
    ) -> OpenDebtExposure: ...


@runtime_checkable
class OpenDebtCountReadPort(Protocol):
    def read_open_debt_count(
        self, *, shop_customer_id: ShopCustomerId
    ) -> OpenDebtCount: ...


@runtime_checkable
class GlobalHardBlockReadPort(Protocol):
    def read_global_hard_block(
        self, *, customer_id: CustomerId
    ) -> GlobalHardBlockProjection: ...


def decide_debt_creation_eligibility(
    eligibility_input: DebtCreationEligibilityInput,
) -> DebtCreationEligibilityDecision:
    if not isinstance(eligibility_input, DebtCreationEligibilityInput):
        raise ValueError("Debt eligibility input is invalid")

    policy = eligibility_input.policy.policy
    if policy.list_status is ShopCustomerListStatus.BLACKLISTED:
        return DebtCreationEligibilityDecision.CUSTOMER_BLACKLISTED
    if eligibility_input.global_hard_block.is_blocked:
        return DebtCreationEligibilityDecision.CUSTOMER_RATING_BLOCKED
    if (
        eligibility_input.open_exposure.value + eligibility_input.original_amount.value
        > policy.credit_limit.value
    ):
        return DebtCreationEligibilityDecision.CREDIT_LIMIT_EXCEEDED
    if eligibility_input.open_count.value >= policy.max_open_debts.value:
        return DebtCreationEligibilityDecision.MAX_OPEN_DEBTS
    return DebtCreationEligibilityDecision.ALLOWED
