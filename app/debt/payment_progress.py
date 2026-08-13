"""Payment-neutral, safe progress contract injected into M13 debt reads.

The debt package deliberately does not import :mod:`app.payment`; M14 supplies
these values through a read-only adapter after the debt rows have been scoped.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.debt.enums import DebtBalanceBasis, DebtStatus

__all__ = ("DebtPaymentProgressProjection", "DebtWebPaymentProgressReader")


@dataclass(frozen=True, slots=True, repr=False)
class DebtPaymentProgressProjection:
    """Identifier-free, server-derived inputs for localized amount rendering."""

    posted_total_uzs: Decimal
    remaining_due_uzs: Decimal
    status: DebtStatus
    paid_at: str | None
    is_payable: bool
    balance_basis: DebtBalanceBasis = DebtBalanceBasis.DISCOUNTED
    is_effectively_overdue: bool = False
    written_off_settled_at: str | None = None

    def __post_init__(self) -> None:
        for value in (self.posted_total_uzs, self.remaining_due_uzs):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError("Payment progress amount is invalid")
            if value != value.to_integral_value():
                raise ValueError("Payment progress amount must be whole UZS")
        if not isinstance(self.status, DebtStatus):
            raise ValueError("Payment progress status is invalid")
        if self.paid_at is not None and not isinstance(self.paid_at, str):
            raise ValueError("Payment progress paid timestamp is invalid")
        if self.written_off_settled_at is not None and not isinstance(
            self.written_off_settled_at, str
        ):
            raise ValueError("Payment progress settlement timestamp is invalid")
        if self.status is DebtStatus.WRITTEN_OFF_SETTLED:
            if (
                self.paid_at is not None
                or self.written_off_settled_at is None
                or self.remaining_due_uzs != 0
                or self.is_payable
            ):
                raise ValueError("Settled write-off progress is incoherent")
        elif self.written_off_settled_at is not None:
            raise ValueError("Only settled write-off may expose settlement time")
        if not isinstance(self.is_payable, bool):
            raise ValueError("Payment progress payability is invalid")
        if not isinstance(self.balance_basis, DebtBalanceBasis):
            raise ValueError("Payment progress balance basis is invalid")
        if not isinstance(self.is_effectively_overdue, bool):
            raise ValueError("Payment progress effective overdue state is invalid")
        if self.is_effectively_overdue and (
            self.status not in {DebtStatus.ACTIVE, DebtStatus.OVERDUE}
            or self.balance_basis is not DebtBalanceBasis.ORIGINAL
        ):
            raise ValueError("Payment progress effective overdue state is incoherent")
        if self.status is DebtStatus.OVERDUE and (
            not self.is_effectively_overdue
            or self.balance_basis is not DebtBalanceBasis.ORIGINAL
        ):
            raise ValueError("Persisted overdue payment progress is incoherent")
        if self.status in {DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF_SETTLED} and (
            self.is_effectively_overdue
            or self.balance_basis is not DebtBalanceBasis.ORIGINAL
        ):
            raise ValueError("Written-off payment progress is incoherent")

    def __repr__(self) -> str:
        return "DebtPaymentProgressProjection(<safe>)"


@dataclass(frozen=True, slots=True)
class DebtWebPaymentProgressReader:
    """Application-composed read adapter for existing Debt SSR routes.

    The Debt package owns only this narrow callable surface.  The concrete
    Payment read implementation is wired in ``app.main`` so the Debt router
    never imports the Payment package directly.
    """

    list_tenant_customer_debts: Callable[..., tuple[object, ...]]
    get_tenant_debt_detail: Callable[..., object | None]
    list_customer_debt_web_items: Callable[..., tuple[object, ...]]
    get_customer_debt_web_detail: Callable[..., object]
    list_payment_progress_for_debts: Callable[..., dict[object, object]]

    def __post_init__(self) -> None:
        if not all(
            callable(value)
            for value in (
                self.list_tenant_customer_debts,
                self.get_tenant_debt_detail,
                self.list_customer_debt_web_items,
                self.get_customer_debt_web_detail,
                self.list_payment_progress_for_debts,
            )
        ):
            raise ValueError("Debt web payment progress reader is invalid")
