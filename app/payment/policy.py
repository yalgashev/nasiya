"""Pure locked-Debt payability policy for M15 payment creation.

The eventual transaction coordinator owns replay resolution and row locks.  This
module intentionally has neither a Session nor a repository dependency so it
can only decide whether the freshly locked Debt may proceed to the later
balance, revision, and amount stages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.auth.error_codes import ErrorCode
from app.debt.business_time import is_effectively_overdue, normalize_payment_created_at
from app.debt.contracts import DebtAggregate
from app.debt.enums import DebtBalanceBasis, DebtStatus

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
    """Locked policy including the authoritative balance basis and rollover."""

    payment_created_at: datetime
    balance_basis: DebtBalanceBasis | None = None
    requires_overdue_transition: bool = False
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payment_created_at",
            normalize_payment_created_at(self.payment_created_at),
        )
        if self.error not in {None, ErrorCode.DEBT_NOT_PAYABLE}:
            raise ValueError("Payment payability error is invalid")
        if self.error is None:
            if not isinstance(self.balance_basis, DebtBalanceBasis):
                raise ValueError("Payable debt requires a balance basis")
        elif self.balance_basis is not None or self.requires_overdue_transition:
            raise ValueError("Denied payability cannot expose mutation policy")
        if self.requires_overdue_transition and (
            self.balance_basis is not DebtBalanceBasis.ORIGINAL
        ):
            raise ValueError("Rollover requires original balance basis")

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

    if debt.status is DebtStatus.OVERDUE:
        return PaymentPayabilityDecision(
            payment_created_at=captured_now.value,
            balance_basis=DebtBalanceBasis.ORIGINAL,
        )
    if debt.status is DebtStatus.WRITTEN_OFF:
        return PaymentPayabilityDecision(
            payment_created_at=captured_now.value,
            balance_basis=DebtBalanceBasis.ORIGINAL,
        )
    if debt.status is not DebtStatus.ACTIVE:
        return PaymentPayabilityDecision(
            payment_created_at=captured_now.value,
            error=ErrorCode.DEBT_NOT_PAYABLE,
        )
    if is_effectively_overdue(
        status=debt.status,
        due_date=debt.due_date,
        server_now=captured_now.value,
    ):
        return PaymentPayabilityDecision(
            payment_created_at=captured_now.value,
            balance_basis=DebtBalanceBasis.ORIGINAL,
            requires_overdue_transition=True,
        )
    return PaymentPayabilityDecision(
        payment_created_at=captured_now.value,
        balance_basis=DebtBalanceBasis.DISCOUNTED,
    )
