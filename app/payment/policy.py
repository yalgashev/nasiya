"""Pure locked-Debt payability policy for M14 payment creation.

The eventual transaction coordinator owns replay resolution and row locks.  This
module intentionally has neither a Session nor a repository dependency so it
can only decide whether the freshly locked Debt may proceed to the later
balance, revision, and amount stages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.auth.error_codes import ErrorCode
from app.debt.business_time import (
    is_payment_due_date_payable,
    normalize_payment_created_at,
)
from app.debt.contracts import DebtAggregate
from app.debt.enums import DebtStatus

__all__ = (
    "CapturedPaymentServerNow",
    "PaymentPayabilityDecision",
    "capture_payment_server_now",
    "evaluate_locked_debt_payability",
)


@dataclass(frozen=True, slots=True)
class CapturedPaymentServerNow:
    """One normalized UTC server instant, captured after the Debt row is locked."""

    value: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", normalize_payment_created_at(self.value))


@dataclass(frozen=True, slots=True)
class PaymentPayabilityDecision:
    """A side-effect-free result whose sole denial is intentionally stable."""

    payment_created_at: datetime
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payment_created_at",
            normalize_payment_created_at(self.payment_created_at),
        )
        if self.error not in {None, ErrorCode.DEBT_NOT_PAYABLE}:
            raise ValueError("Payment payability error is invalid")

    @property
    def is_payable(self) -> bool:
        return self.error is None


def capture_payment_server_now(now: datetime) -> CapturedPaymentServerNow:
    """Validate and normalize the coordinator's injected server clock exactly once."""

    return CapturedPaymentServerNow(now)


def evaluate_locked_debt_payability(
    *, debt: DebtAggregate, captured_now: CapturedPaymentServerNow
) -> PaymentPayabilityDecision:
    """Apply the fixed active-then-inclusive-Tashkent-due-date gate.

    The caller resolves a completed idempotency replay before it invokes this
    function.  A new command invokes it immediately after obtaining its Debt
    lock, before loading payment totals or considering revision and amount.
    """

    if not isinstance(debt, DebtAggregate):
        raise TypeError("debt must be a DebtAggregate")
    if not isinstance(captured_now, CapturedPaymentServerNow):
        raise TypeError("captured_now must come from capture_payment_server_now")

    # Status deliberately wins: no due-date predicate runs for any other state.
    if debt.status is not DebtStatus.ACTIVE:
        return PaymentPayabilityDecision(
            payment_created_at=captured_now.value,
            error=ErrorCode.DEBT_NOT_PAYABLE,
        )
    if not is_payment_due_date_payable(
        payment_created_at=captured_now.value,
        due_date=debt.due_date,
    ):
        return PaymentPayabilityDecision(
            payment_created_at=captured_now.value,
            error=ErrorCode.DEBT_NOT_PAYABLE,
        )
    return PaymentPayabilityDecision(payment_created_at=captured_now.value)
