"""Safe current-tenant read projections for M13 debt web pages."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.debt.enums import DebtStatus
from app.debt.repository import get_tenant_debt, list_tenant_debts
from app.debt.values import DebtId, DiscountBasisPoints, OriginalAmountUZS
from app.shop.values import ShopId
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "TenantDebtDetailProjection",
    "TenantDebtListProjection",
    "get_tenant_debt_detail",
    "list_tenant_customer_debts",
)


@dataclass(frozen=True, slots=True, repr=False)
class TenantDebtListProjection:
    debt_id: DebtId = field(repr=False)
    status: DebtStatus
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount_uzs: str
    due_date: str
    pending_expires_at: str

    def __repr__(self) -> str:
        return "TenantDebtListProjection(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class TenantDebtDetailProjection:
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    expected_revision: int
    status: DebtStatus
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount_uzs: str
    due_date: str
    pending_expires_at: str
    accepted_at: str | None
    rejected_at: str | None
    cancelled_at: str | None
    expired_at: str | None
    decision_reason: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return "TenantDebtDetailProjection(<safe>)"


def list_tenant_customer_debts(
    session: Session, *, shop_id: ShopId, shop_customer_id: ShopCustomerId
) -> tuple[TenantDebtListProjection, ...]:
    return tuple(
        _list_projection(row)
        for row in list_tenant_debts(session, shop_id=shop_id)
        if row.shop_customer_id == shop_customer_id.as_uuid()
    )


def get_tenant_debt_detail(
    session: Session, *, shop_id: ShopId, debt_id: DebtId
) -> TenantDebtDetailProjection | None:
    row = get_tenant_debt(session, shop_id=shop_id, debt_id=debt_id)
    if row is None:
        return None
    status = DebtStatus(row.status)
    reason = (
        row.rejection_reason
        if status is DebtStatus.REJECTED
        else row.cancellation_reason
        if status is DebtStatus.CANCELLED
        else None
    )
    item = _list_projection(row)
    return TenantDebtDetailProjection(
        debt_id=item.debt_id,
        shop_customer_id=ShopCustomerId(row.shop_customer_id),
        expected_revision=row.revision,
        status=item.status,
        original_amount=item.original_amount,
        discount_basis_points=item.discount_basis_points,
        discounted_amount_uzs=item.discounted_amount_uzs,
        due_date=item.due_date,
        pending_expires_at=item.pending_expires_at,
        accepted_at=_iso(row.accepted_at),
        rejected_at=_iso(row.rejected_at),
        cancelled_at=_iso(row.cancelled_at),
        expired_at=_iso(row.expired_at),
        decision_reason=reason,
    )


def _list_projection(row) -> TenantDebtListProjection:
    return TenantDebtListProjection(
        debt_id=DebtId(row.id),
        status=DebtStatus(row.status),
        original_amount=OriginalAmountUZS(row.original_amount_uzs),
        discount_basis_points=DiscountBasisPoints(row.discount_basis_points),
        discounted_amount_uzs=str(row.discounted_amount_uzs),
        due_date=row.due_date.isoformat(),
        pending_expires_at=row.pending_expires_at.isoformat(),
    )


def _iso(value) -> str | None:
    return None if value is None else value.isoformat()
