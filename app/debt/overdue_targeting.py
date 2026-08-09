"""Detached discovery and forward locks for M15 overdue materialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.customer.models import Customer
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.overdue_ports import require_hard_block_business_date
from app.debt.repository import (
    LockedCustomerHardBlockScope,
    lock_customer_hard_block_scope,
    validate_locked_customer_hard_block_scope,
)
from app.debt.values import CustomerId, DebtId
from app.shop.repository import _LockedShop, lock_shop_for_update
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer

__all__ = (
    "LockedOverdueDebt",
    "OverdueCandidateLocator",
    "discover_overdue_candidates",
    "locked_overdue_debt_row",
    "resolve_and_lock_overdue_candidate",
)


@dataclass(frozen=True, slots=True, repr=False)
class OverdueCandidateLocator:
    """Detached scalar locator; never carries an ORM row or parent authority."""

    debt_id: DebtId = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.debt_id, DebtId):
            raise TypeError("candidate debt_id must be a DebtId")

    def __repr__(self) -> str:
        return "OverdueCandidateLocator(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _DiscoveredOverdueChain:
    debt_id: UUID = field(repr=False)
    shop_customer_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)

    def __repr__(self) -> str:
        return "_DiscoveredOverdueChain(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedOverdueDebt:
    """Complete `Shop -> Customer -> ShopCustomer -> Debt` lock proof."""

    _locked_shop: _LockedShop = field(repr=False)
    _locked_customer: LockedCustomerHardBlockScope = field(repr=False)
    _shop_customer: ShopCustomer = field(repr=False)
    _debt: Debt = field(repr=False)
    _session: Session = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "LockedOverdueDebt(<redacted>)"


def discover_overdue_candidates(
    session: Session, *, as_of_business_date: date, limit: int
) -> tuple[OverdueCandidateLocator, ...]:
    """Return non-locking scalar locators ordered by `(due_date, id)`."""

    business_date = require_hard_block_business_date(as_of_business_date)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Overdue candidate limit must be between 1 and 100")
    identifiers = session.scalars(
        select(Debt.id)
        .where(
            Debt.status == DebtStatus.ACTIVE.value,
            Debt.due_date < business_date,
        )
        .order_by(Debt.due_date, Debt.id)
        .limit(limit)
    )
    return tuple(
        OverdueCandidateLocator(DebtId(identifier)) for identifier in identifiers
    )


def resolve_and_lock_overdue_candidate(
    session: Session, *, candidate: OverdueCandidateLocator
) -> LockedOverdueDebt | None:
    """Rediscover scalars, then lock only in forward global class order."""

    if not isinstance(candidate, OverdueCandidateLocator):
        raise TypeError("candidate must come from discover_overdue_candidates")
    discovered = _discover_chain(session, debt_id=candidate.debt_id)
    if discovered is None:
        return None

    locked_shop = lock_shop_for_update(session, shop_id=ShopId(discovered.shop_id))
    if locked_shop is None:
        return None
    locked_customer = lock_customer_hard_block_scope(
        session, customer_id=CustomerId(discovered.customer_id)
    )
    if locked_customer is None:
        return None
    customer = validate_locked_customer_hard_block_scope(
        session, locked_customer
    )._customer
    if customer.id != discovered.customer_id:
        return None
    shop_customer = session.scalar(
        select(ShopCustomer)
        .where(
            ShopCustomer.id == discovered.shop_customer_id,
            ShopCustomer.shop_id == locked_shop.shop.id,
            ShopCustomer.customer_id == customer.id,
        )
        .with_for_update()
    )
    if shop_customer is None:
        return None
    debt = session.scalar(
        select(Debt)
        .where(
            Debt.id == discovered.debt_id,
            Debt.shop_customer_id == shop_customer.id,
        )
        .with_for_update()
    )
    if debt is None:
        return None
    return LockedOverdueDebt(
        _locked_shop=locked_shop,
        _locked_customer=locked_customer,
        _shop_customer=shop_customer,
        _debt=debt,
        _session=session,
    )


def locked_overdue_debt_row(session: Session, token: object) -> Debt:
    locked = _validate_locked_overdue_debt(session, token)
    return locked._debt


def _discover_chain(
    session: Session, *, debt_id: DebtId
) -> _DiscoveredOverdueChain | None:
    row = session.execute(
        select(Debt.id, ShopCustomer.id, Customer.id, ShopCustomer.shop_id)
        .select_from(Debt)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .where(Debt.id == debt_id.as_uuid())
    ).one_or_none()
    if row is None:
        return None
    return _DiscoveredOverdueChain(
        debt_id=row[0],
        shop_customer_id=row[1],
        customer_id=row[2],
        shop_id=row[3],
    )


def _validate_locked_overdue_debt(session: Session, token: object) -> LockedOverdueDebt:
    if not isinstance(token, LockedOverdueDebt):
        raise TypeError("locked debt must come from overdue target resolver")
    if token._session is not session:
        raise RuntimeError("locked overdue debt belongs to a different session")
    validate_locked_customer_hard_block_scope(session, token._locked_customer)
    if session.get(ShopCustomer, token._shop_customer.id) is not token._shop_customer:
        raise RuntimeError("locked ShopCustomer is not attached to this session")
    if session.get(Debt, token._debt.id) is not token._debt:
        raise RuntimeError("locked Debt is not attached to this session")
    return token
