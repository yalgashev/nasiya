"""Session-owned, tenant-scoped persistence primitives for M13 Debt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.debt.contracts import DebtAggregate
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.policy import (
    GlobalHardBlockProjection,
    OpenDebtCount,
    OpenDebtExposure,
)
from app.debt.values import DebtId, ShopCustomerId
from app.shop_customer.models import ShopCustomer

__all__ = (
    "LockedDebtPredecessor",
    "SqlAlchemyDebtOpenSetReader",
    "discover_debt_candidates",
    "get_customer_owned_debt",
    "get_tenant_debt",
    "insert_debt",
    "list_customer_owned_debts",
    "list_tenant_debts",
    "lock_debts_in_id_order",
    "mark_debt_predecessor_locked",
    "update_locked_debt",
)


@dataclass(frozen=True, slots=True, repr=False)
class LockedDebtPredecessor:
    shop_customer_id: UUID
    customer_id: UUID
    _session: Session

    def __repr__(self) -> str:
        return "LockedDebtPredecessor(<redacted>)"


def mark_debt_predecessor_locked(
    session: Session, *, shop_customer: ShopCustomer
) -> LockedDebtPredecessor:
    if not isinstance(shop_customer, ShopCustomer):
        raise TypeError("shop_customer must be a locked ShopCustomer")
    return LockedDebtPredecessor(
        shop_customer_id=shop_customer.id,
        customer_id=shop_customer.customer_id,
        _session=session,
    )


def list_tenant_debts(session: Session, *, shop_id: UUID) -> list[Debt]:
    return list(
        session.scalars(
            select(Debt)
            .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
            .where(ShopCustomer.shop_id == shop_id)
            .order_by(Debt.created_at.desc(), Debt.id)
        )
    )


def get_tenant_debt(session: Session, *, shop_id: UUID, debt_id: DebtId) -> Debt | None:
    return session.scalar(
        select(Debt)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(ShopCustomer.shop_id == shop_id, Debt.id == debt_id.as_uuid())
    )


def list_customer_owned_debts(session: Session, *, customer_id: UUID) -> list[Debt]:
    return list(
        session.scalars(
            select(Debt)
            .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
            .where(ShopCustomer.customer_id == customer_id)
            .order_by(Debt.created_at.desc(), Debt.id)
        )
    )


def get_customer_owned_debt(
    session: Session, *, customer_id: UUID, debt_id: DebtId
) -> Debt | None:
    return session.scalar(
        select(Debt)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .where(ShopCustomer.customer_id == customer_id, Debt.id == debt_id.as_uuid())
    )


def discover_debt_candidates(
    session: Session, *, now: datetime, limit: int
) -> list[UUID]:
    _require_aware(now)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("Debt candidate limit is invalid")
    return list(
        session.scalars(
            select(Debt.id)
            .where(
                Debt.status == DebtStatus.PENDING.value, Debt.pending_expires_at <= now
            )
            .order_by(Debt.pending_expires_at, Debt.id)
            .limit(limit)
        )
    )


def lock_debts_in_id_order(
    session: Session, *, debt_ids: tuple[DebtId, ...]
) -> list[Debt]:
    identifiers = tuple(item.as_uuid() for item in debt_ids)
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Debt lock IDs must be unique")
    return list(
        session.scalars(
            select(Debt)
            .where(Debt.id.in_(identifiers))
            .order_by(Debt.id)
            .with_for_update()
        )
    )


def insert_debt(
    session: Session, *, locked_predecessor: LockedDebtPredecessor, debt: DebtAggregate
) -> Debt:
    _validate_predecessor(session, locked_predecessor)
    if debt.shop_customer_id.as_uuid() != locked_predecessor.shop_customer_id:
        raise ValueError("Debt does not belong to locked ShopCustomer")
    row = Debt(
        id=debt.id.as_uuid(),
        shop_customer_id=debt.shop_customer_id.as_uuid(),
        created_by_user_id=debt.created_by_user_id,
        original_amount_uzs=debt.original_amount.value,
        discount_basis_points=debt.discount_basis_points.value,
        discounted_amount_uzs=debt.discounted_amount.value,
        due_date=debt.due_date,
        pending_expires_at=debt.pending_expires_at,
        status=debt.status.value,
        revision=debt.revision.value,
        rejection_reason=None
        if debt.rejection_reason is None
        else debt.rejection_reason.value,
        cancellation_reason=None
        if debt.cancellation_reason is None
        else debt.cancellation_reason.value,
        accepted_at=debt.accepted_at,
        rejected_at=debt.rejected_at,
        cancelled_at=debt.cancelled_at,
        expired_at=debt.expired_at,
        created_at=debt.created_at,
        updated_at=debt.updated_at,
    )
    session.add(row)
    session.flush()
    return row


def update_locked_debt(session: Session, *, row: Debt, debt: DebtAggregate) -> Debt:
    if session.get(Debt, row.id) is not row:
        raise RuntimeError("Debt row is not attached to this session")
    for name, value in (
        ("status", debt.status.value),
        ("revision", debt.revision.value),
        (
            "rejection_reason",
            None if debt.rejection_reason is None else debt.rejection_reason.value,
        ),
        (
            "cancellation_reason",
            None
            if debt.cancellation_reason is None
            else debt.cancellation_reason.value,
        ),
        ("accepted_at", debt.accepted_at),
        ("rejected_at", debt.rejected_at),
        ("cancelled_at", debt.cancelled_at),
        ("expired_at", debt.expired_at),
        ("updated_at", debt.updated_at),
    ):
        setattr(row, name, value)
    session.flush()
    return row


class SqlAlchemyDebtOpenSetReader:
    """Reads M13 open exposure/count only after a locked parent token."""

    def __init__(
        self, session: Session, *, locked_predecessor: LockedDebtPredecessor
    ) -> None:
        self._session = session
        self._predecessor = _validate_predecessor(session, locked_predecessor)

    def read_open_debt_exposure(
        self, *, shop_customer_id: ShopCustomerId
    ) -> OpenDebtExposure:
        identifier = self._require_parent(shop_customer_id)
        value = self._session.scalar(
            select(
                func.coalesce(func.sum(Debt.original_amount_uzs), Decimal("0"))
            ).where(
                Debt.shop_customer_id == identifier,
                Debt.status.in_(("pending", "active")),
            )
        )
        return OpenDebtExposure(Decimal(value))

    def read_open_debt_count(
        self, *, shop_customer_id: ShopCustomerId
    ) -> OpenDebtCount:
        identifier = self._require_parent(shop_customer_id)
        value = self._session.scalar(
            select(func.count())
            .select_from(Debt)
            .where(
                Debt.shop_customer_id == identifier,
                Debt.status.in_(("pending", "active")),
            )
        )
        return OpenDebtCount(int(value))

    def read_global_hard_block(self, *, customer_id: UUID) -> GlobalHardBlockProjection:
        if customer_id != self._predecessor.customer_id:
            raise ValueError("Hard-block customer is not locked predecessor")
        return GlobalHardBlockProjection(is_blocked=False)

    def _require_parent(self, shop_customer_id: ShopCustomerId) -> UUID:
        if shop_customer_id.as_uuid() != self._predecessor.shop_customer_id:
            raise ValueError("Open-set ShopCustomer is not locked predecessor")
        return self._predecessor.shop_customer_id


def _validate_predecessor(session: Session, token: object) -> LockedDebtPredecessor:
    if not isinstance(token, LockedDebtPredecessor):
        raise TypeError(
            "locked_predecessor must come from mark_debt_predecessor_locked"
        )
    if token._session is not session:
        raise RuntimeError(
            "locked_predecessor was created by a different SQLAlchemy session"
        )
    return token


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Debt timestamp must be timezone-aware")
    value.astimezone(UTC)
