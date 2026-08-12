"""Detached discovery and forward locks for M15 overdue materialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.debt.business_time import tashkent_business_date
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.overdue_ports import require_hard_block_business_date
from app.debt.rating_ports import (
    LockedOverdueRatingSource,
    mark_locked_overdue_rating_source,
)
from app.debt.repository import (
    LockedCustomerHardBlockScope,
    lock_customer_hard_block_scope,
    validate_locked_customer_hard_block_scope,
)
from app.debt.values import CustomerId, DebtId
from app.shop.repository import _LockedShop, lock_shop_for_update
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "LockedOverdueDebt",
    "MAX_OVERDUE_BATCH_SIZE",
    "OverdueDiscoveryBatch",
    "OverdueCandidateLocator",
    "discover_overdue_batch",
    "discover_overdue_candidates",
    "locked_overdue_debt_row",
    "locked_overdue_rating_source",
    "resolve_and_lock_overdue_candidate",
)

MAX_OVERDUE_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True, repr=False)
class OverdueCandidateLocator:
    """Detached scalar locator; never carries an ORM row or parent authority."""

    debt_id: DebtId = field(repr=False)
    shop_customer_id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    shop_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.debt_id, DebtId):
            raise TypeError("candidate debt_id must be a DebtId")
        for value in (self.shop_customer_id, self.customer_id, self.shop_id):
            if not isinstance(value, UUID):
                raise TypeError("candidate chain identifiers must be UUIDs")

    def __repr__(self) -> str:
        return "OverdueCandidateLocator(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class OverdueDiscoveryBatch:
    """Detached service page with one normalized instant for every locator."""

    normalized_now: datetime
    candidates: tuple[OverdueCandidateLocator, ...] = field(repr=False)

    def __post_init__(self) -> None:
        normalized = _normalize_now(self.normalized_now)
        if self.normalized_now.tzinfo is not UTC or normalized != self.normalized_now:
            raise ValueError("Overdue discovery time must be normalized UTC")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, OverdueCandidateLocator) for item in self.candidates
        ):
            raise TypeError("Overdue discovery candidates are invalid")
        if len(self.candidates) > MAX_OVERDUE_BATCH_SIZE:
            raise ValueError("Overdue discovery page is too large")

    def __repr__(self) -> str:
        return (
            "OverdueDiscoveryBatch("
            f"normalized_now={self.normalized_now!r}, "
            f"candidate_count={len(self.candidates)})"
        )


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
    _require_batch_size(limit)
    rows = session.execute(
        select(
            Debt.id,
            ShopCustomer.id,
            ShopCustomer.customer_id,
            ShopCustomer.shop_id,
        )
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(
            Debt.status == DebtStatus.ACTIVE.value,
            Debt.due_date < business_date,
        )
        .order_by(Debt.due_date, Debt.id)
        .limit(limit)
    )
    return tuple(
        OverdueCandidateLocator(
            debt_id=DebtId(debt_id),
            shop_customer_id=shop_customer_id,
            customer_id=customer_id,
            shop_id=shop_id,
        )
        for debt_id, shop_customer_id, customer_id, shop_id in rows
    )


def discover_overdue_batch(
    session: Session, *, now: datetime, batch_size: int
) -> OverdueDiscoveryBatch:
    """Normalize one trusted instant and return only detached scalar locators."""

    normalized_now = _normalize_now(now)
    _require_batch_size(batch_size)
    return OverdueDiscoveryBatch(
        normalized_now=normalized_now,
        candidates=discover_overdue_candidates(
            session,
            as_of_business_date=tashkent_business_date(normalized_now),
            limit=batch_size,
        ),
    )


def resolve_and_lock_overdue_candidate(
    session: Session, *, candidate: OverdueCandidateLocator
) -> LockedOverdueDebt | None:
    """Resolve detached scalars in forward global lock-class order."""

    if not isinstance(candidate, OverdueCandidateLocator):
        raise TypeError("candidate must come from discover_overdue_candidates")
    locked_shop = lock_shop_for_update(session, shop_id=ShopId(candidate.shop_id))
    if locked_shop is None:
        return None
    locked_customer = lock_customer_hard_block_scope(
        session, customer_id=CustomerId(candidate.customer_id)
    )
    if locked_customer is None:
        return None
    customer = validate_locked_customer_hard_block_scope(
        session, locked_customer
    )._customer
    if customer.id != candidate.customer_id:
        return None
    shop_customer = session.scalar(
        select(ShopCustomer)
        .where(
            ShopCustomer.id == candidate.shop_customer_id,
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
            Debt.id == candidate.debt_id.as_uuid(),
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


def locked_overdue_rating_source(
    session: Session, token: object
) -> LockedOverdueRatingSource:
    """Derive a debt-local rating proof without acquiring another lock."""

    locked = _validate_locked_overdue_debt(session, token)
    return mark_locked_overdue_rating_source(
        session,
        customer_id=locked._locked_customer._customer.id,
        shop_customer_id=ShopCustomerId(locked._shop_customer.id),
        debt_id=DebtId(locked._debt.id),
    )


def _validate_locked_overdue_debt(session: Session, token: object) -> LockedOverdueDebt:
    if not isinstance(token, LockedOverdueDebt):
        raise TypeError("locked debt must come from overdue target resolver")
    if token._session is not session:
        raise RuntimeError("locked overdue debt belongs to a different session")
    validate_locked_customer_hard_block_scope(session, token._locked_customer)
    if inspect(token._shop_customer).session is not session:
        raise RuntimeError("locked ShopCustomer is not attached to this session")
    if session.get(Debt, token._debt.id) is not token._debt:
        raise RuntimeError("locked Debt is not attached to this session")
    return token


def _require_batch_size(batch_size: int) -> None:
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or not 1 <= batch_size <= MAX_OVERDUE_BATCH_SIZE
    ):
        raise ValueError("Overdue batch size must be between 1 and 100")


def _normalize_now(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Overdue batch time must be timezone-aware")
    return value.astimezone(UTC)
