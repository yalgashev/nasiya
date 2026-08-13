"""Payment-local structural boundary for an optional on-time source effect."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.orm import Session

from app.debt.business_time import tashkent_business_date
from app.debt.enums import DebtStatus
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountedAmountUZS,
    OriginalAmountUZS,
)
from app.payment.values import (
    PaymentAmountUZS,
    PaymentId,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
)
from app.shop_customer.values import ShopCustomerId

if TYPE_CHECKING:
    from app.payment.targeting import LockedTenantPaymentDebt

__all__ = (
    "LockedPaymentRatingAppendPort",
    "LockedWrittenOffSettledRatingAppendPort",
    "PaymentRatingAppendOutcome",
    "PaymentRatingEligibility",
    "PaymentRatingEligibilityFacts",
    "PaymentVoidCompensationAppendOutcome",
    "PaymentVoidRatingAppendPort",
    "PaymentVoidRatingSourceFacts",
    "PaymentVoidRatingSourceReadPort",
    "PaymentRatingPositiveSource",
    "PreTransitionRatingSourceToken",
    "PendingOnTimePaidRatingEffect",
    "PendingWrittenOffSettledRatingEffect",
    "WrittenOffSettledRatingAppendOutcome",
)


class PaymentRatingAppendOutcome(StrEnum):
    APPENDED = "appended"
    DAILY_CAP_ALREADY_USED = "daily_cap_already_used"
    SOURCE_ALREADY_EXISTS = "source_already_exists"


class PaymentRatingEligibility(StrEnum):
    AWARD = "award"
    NO_BONUS = "no_bonus"
    DAILY_CAP_ALREADY_USED = "daily_cap_already_used"


class WrittenOffSettledRatingAppendOutcome(StrEnum):
    APPENDED = "appended"
    SOURCE_ALREADY_EXISTS = "source_already_exists"


class PaymentRatingPositiveSource(StrEnum):
    ON_TIME_PAID = "on_time_paid"
    WRITTEN_OFF_SETTLED = "written_off_settled"


class PaymentVoidCompensationAppendOutcome(StrEnum):
    APPENDED = "appended"
    SOURCE_ALREADY_EXISTS = "source_already_exists"


@dataclass(frozen=True, slots=True, repr=False)
class PreTransitionRatingSourceToken:
    """Terminal marker proof captured before a Payment void clears it."""

    payment_id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    positive_source: PaymentRatingPositiveSource
    terminal_status: DebtStatus
    source_revision: DebtRevision
    source_occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.payment_id, PaymentId):
            raise ValueError("Pre-transition source Payment is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Pre-transition source Debt is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Pre-transition source ShopCustomer is invalid")
        expected_status = {
            PaymentRatingPositiveSource.ON_TIME_PAID: DebtStatus.PAID,
            PaymentRatingPositiveSource.WRITTEN_OFF_SETTLED: (
                DebtStatus.WRITTEN_OFF_SETTLED
            ),
        }.get(self.positive_source)
        if expected_status is None or self.terminal_status is not expected_status:
            raise ValueError("Pre-transition rating source marker is incoherent")
        if not isinstance(self.source_revision, DebtRevision):
            raise ValueError("Pre-transition source revision is invalid")
        object.__setattr__(
            self,
            "source_occurred_at",
            _normalize_aware_utc(self.source_occurred_at),
        )

    def __repr__(self) -> str:
        return "PreTransitionRatingSourceToken(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentVoidRatingSourceFacts:
    """Payment-local scalar proof used by the rating composition adapter."""

    payment_id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    terminal_status: DebtStatus
    payment_amount: PaymentAmountUZS = field(repr=False)
    original_amount: OriginalAmountUZS = field(repr=False)
    discounted_amount: DiscountedAmountUZS = field(repr=False)
    as_of_payment_total: PostedPaymentTotalUZS = field(repr=False)
    accepted_at: datetime | None = field(repr=False)
    due_date: date
    overdue_revision: DebtRevision | None = field(repr=False)
    source_revision: DebtRevision
    source_occurred_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.payment_id, PaymentId):
            raise ValueError("Payment void rating source Payment is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Payment void rating source Debt is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Payment void rating source ShopCustomer is invalid")
        if not isinstance(self.terminal_status, DebtStatus):
            raise ValueError("Payment void rating source status is invalid")
        if not isinstance(self.payment_amount, PaymentAmountUZS):
            raise ValueError("Payment void rating source amount is invalid")
        if not isinstance(self.original_amount, OriginalAmountUZS):
            raise ValueError("Payment void rating source original amount is invalid")
        if not isinstance(self.discounted_amount, DiscountedAmountUZS):
            raise ValueError("Payment void rating source discounted amount is invalid")
        if not isinstance(self.as_of_payment_total, PostedPaymentTotalUZS):
            raise ValueError("Payment void rating source total is invalid")
        if not isinstance(self.due_date, date) or isinstance(self.due_date, datetime):
            raise ValueError("Payment void rating source due date is invalid")
        if self.overdue_revision is not None and not isinstance(
            self.overdue_revision, DebtRevision
        ):
            raise ValueError("Payment void rating overdue revision is invalid")
        if not isinstance(self.source_revision, DebtRevision):
            raise ValueError("Payment void rating source revision is invalid")
        if self.accepted_at is not None:
            object.__setattr__(
                self, "accepted_at", _normalize_aware_utc(self.accepted_at)
            )
        object.__setattr__(
            self, "source_occurred_at", _normalize_aware_utc(self.source_occurred_at)
        )

    def __repr__(self) -> str:
        return "PaymentVoidRatingSourceFacts(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentRatingEligibilityFacts:
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
    daily_cap_already_used: bool = False

    def __repr__(self) -> str:
        return "PaymentRatingEligibilityFacts(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PendingOnTimePaidRatingEffect:
    event_id: UUID = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    payment_created_at: datetime
    payment_business_date: date
    source_revision: DebtRevision

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID):
            raise ValueError("Pending on-time rating identity is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Pending on-time rating Debt is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Pending on-time rating ShopCustomer is invalid")
        if not isinstance(self.source_revision, DebtRevision):
            raise ValueError("Pending on-time rating source revision is invalid")
        occurred_at = _normalize_aware_utc(self.payment_created_at)
        if (
            not isinstance(self.payment_business_date, date)
            or isinstance(self.payment_business_date, datetime)
            or self.payment_business_date != tashkent_business_date(occurred_at)
        ):
            raise ValueError(
                "Pending on-time rating date must match Tashkent payment date"
            )
        object.__setattr__(self, "payment_created_at", occurred_at)

    def __repr__(self) -> str:
        return "PendingOnTimePaidRatingEffect(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PendingWrittenOffSettledRatingEffect:
    event_id: UUID = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    payment_created_at: datetime
    source_revision: DebtRevision

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID):
            raise ValueError("Pending settlement rating identity is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Pending settlement rating Debt is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Pending settlement rating ShopCustomer is invalid")
        if not isinstance(self.source_revision, DebtRevision):
            raise ValueError("Pending settlement rating source revision is invalid")
        occurred_at = _normalize_aware_utc(self.payment_created_at)
        tashkent_business_date(occurred_at)
        object.__setattr__(self, "payment_created_at", occurred_at)

    def __repr__(self) -> str:
        return "PendingWrittenOffSettledRatingEffect(<redacted>)"


@runtime_checkable
class LockedPaymentRatingAppendPort(Protocol):
    def evaluate_on_time_paid(
        self, facts: PaymentRatingEligibilityFacts
    ) -> PaymentRatingEligibility: ...

    def positive_daily_slot_used(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        payment_business_date: date,
    ) -> bool: ...

    def append_pending_on_time_paid(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        effect: PendingOnTimePaidRatingEffect,
    ) -> PaymentRatingAppendOutcome: ...


@runtime_checkable
class LockedWrittenOffSettledRatingAppendPort(Protocol):
    """M17-only settlement append surface; it does not widen the M16 port."""

    def has_coherent_written_off_source(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        written_off_at: datetime,
        written_off_revision: DebtRevision,
    ) -> bool: ...

    def append_pending_written_off_settled(
        self,
        session: Session,
        *,
        locked_debt: LockedTenantPaymentDebt,
        effect: PendingWrittenOffSettledRatingEffect,
    ) -> WrittenOffSettledRatingAppendOutcome: ...


@runtime_checkable
class PaymentVoidRatingSourceReadPort(Protocol):
    def read_pre_transition_source(
        self,
        session: Session,
        *,
        facts: PaymentVoidRatingSourceFacts,
    ) -> PreTransitionRatingSourceToken | None: ...


@runtime_checkable
class PaymentVoidRatingAppendPort(Protocol):
    def append_source_compensation(
        self,
        session: Session,
        *,
        source: PreTransitionRatingSourceToken,
        voided_at: datetime,
        completed_replay: bool,
    ) -> PaymentVoidCompensationAppendOutcome: ...


def _normalize_aware_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Pending on-time rating time must be aware")
    return value.astimezone(UTC)
