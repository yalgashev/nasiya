"""Exact locked-source predicate for the bounded on-time-payment bonus."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from app.debt.business_time import (
    normalize_payment_created_at,
    tashkent_business_date,
)
from app.debt.enums import DebtStatus
from app.debt.values import DebtRevision, OriginalAmountUZS
from app.payment.values import PaymentAmountUZS, RemainingDueUZS
from app.rating.enums import PositiveRatingDecision
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "MIN_ON_TIME_RATING_ORIGINAL_AMOUNT_UZS",
    "OnTimePaidEligibilityFacts",
    "OnTimePaidEligibilityResult",
    "PositiveDailyCapKey",
    "evaluate_on_time_paid_eligibility",
)

MIN_ON_TIME_RATING_ORIGINAL_AMOUNT_UZS: Final = Decimal("100000")


@dataclass(frozen=True, slots=True, repr=False)
class PositiveDailyCapKey:
    shop_customer_id: ShopCustomerId = field(repr=False)
    business_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Positive daily-cap ShopCustomer is invalid")
        if not isinstance(self.business_date, date) or isinstance(
            self.business_date, datetime
        ):
            raise ValueError("Positive daily-cap business date is invalid")

    def __repr__(self) -> str:
        return "PositiveDailyCapKey(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class OnTimePaidEligibilityFacts:
    """Facts captured after the inherited Customer/Debt lock and payment clock."""

    shop_customer_id: ShopCustomerId = field(repr=False)
    pre_status: DebtStatus
    post_status: DebtStatus
    payment_amount: PaymentAmountUZS = field(repr=False)
    discounted_remaining: RemainingDueUZS = field(repr=False)
    original_amount: OriginalAmountUZS = field(repr=False)
    accepted_at: datetime
    payment_created_at: datetime
    due_date: date
    overdue_at: datetime | None = field(default=None, repr=False)
    overdue_revision: DebtRevision | None = field(default=None, repr=False)
    is_completed_replay: bool = False
    daily_cap_already_used: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("On-time eligibility ShopCustomer is invalid")
        if not isinstance(self.pre_status, DebtStatus) or not isinstance(
            self.post_status, DebtStatus
        ):
            raise ValueError("On-time eligibility Debt status is invalid")
        if not isinstance(self.payment_amount, PaymentAmountUZS):
            raise ValueError("On-time eligibility payment amount is invalid")
        if not isinstance(self.discounted_remaining, RemainingDueUZS):
            raise ValueError("On-time eligibility remaining amount is invalid")
        if not isinstance(self.original_amount, OriginalAmountUZS):
            raise ValueError("On-time eligibility original amount is invalid")
        accepted_at = _normalize_aware_utc(
            self.accepted_at,
            field_name="Debt accepted at",
        )
        payment_created_at = normalize_payment_created_at(self.payment_created_at)
        if not isinstance(self.due_date, date) or isinstance(self.due_date, datetime):
            raise ValueError("On-time eligibility due date is invalid")
        if self.overdue_at is not None:
            object.__setattr__(
                self,
                "overdue_at",
                _normalize_aware_utc(
                    self.overdue_at,
                    field_name="Debt overdue at",
                ),
            )
        if self.overdue_revision is not None and not isinstance(
            self.overdue_revision, DebtRevision
        ):
            raise ValueError("On-time eligibility overdue revision is invalid")
        if not isinstance(self.is_completed_replay, bool):
            raise ValueError("On-time eligibility replay state is invalid")
        if not isinstance(self.daily_cap_already_used, bool):
            raise ValueError("On-time eligibility daily-cap state is invalid")
        object.__setattr__(self, "accepted_at", accepted_at)
        object.__setattr__(self, "payment_created_at", payment_created_at)

    def __repr__(self) -> str:
        return "OnTimePaidEligibilityFacts(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class OnTimePaidEligibilityResult:
    decision: PositiveRatingDecision
    cap_key: PositiveDailyCapKey | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.decision, PositiveRatingDecision):
            raise ValueError("Positive rating decision is invalid")
        if self.decision is PositiveRatingDecision.NO_BONUS:
            if self.cap_key is not None:
                raise ValueError("Ineligible positive result cannot carry a cap key")
        elif not isinstance(self.cap_key, PositiveDailyCapKey):
            raise ValueError("Eligible positive result requires a cap key")

    @property
    def awards_bonus(self) -> bool:
        return self.decision is PositiveRatingDecision.AWARD

    def __repr__(self) -> str:
        return "OnTimePaidEligibilityResult(<redacted>)"


def evaluate_on_time_paid_eligibility(
    facts: OnTimePaidEligibilityFacts,
) -> OnTimePaidEligibilityResult:
    if not isinstance(facts, OnTimePaidEligibilityFacts):
        raise ValueError("On-time eligibility facts are invalid")
    accepted_date = tashkent_business_date(facts.accepted_at)
    payment_date = tashkent_business_date(facts.payment_created_at)
    eligible_source = (
        facts.pre_status is DebtStatus.ACTIVE
        and facts.payment_amount.value == facts.discounted_remaining.value
        and facts.post_status is DebtStatus.PAID
        and facts.overdue_at is None
        and facts.overdue_revision is None
        and facts.original_amount.value >= MIN_ON_TIME_RATING_ORIGINAL_AMOUNT_UZS
        and accepted_date < payment_date
        and payment_date <= facts.due_date
        and not facts.is_completed_replay
    )
    if not eligible_source:
        return OnTimePaidEligibilityResult(decision=PositiveRatingDecision.NO_BONUS)

    cap_key = PositiveDailyCapKey(
        shop_customer_id=facts.shop_customer_id,
        business_date=payment_date,
    )
    if facts.daily_cap_already_used:
        return OnTimePaidEligibilityResult(
            decision=PositiveRatingDecision.DAILY_CAP_ALREADY_USED,
            cap_key=cap_key,
        )
    return OnTimePaidEligibilityResult(
        decision=PositiveRatingDecision.AWARD,
        cap_key=cap_key,
    )


def _normalize_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be an aware datetime")
    return value.astimezone(UTC)
