"""Session-owned, tenant-scoped persistence primitives for M13 Debt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.customer.models import Customer
from app.debt.contracts import DebtAggregate, DebtReason
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.overdue_ports import require_hard_block_business_date
from app.debt.policy import (
    GlobalHardBlockProjection,
    OpenDebtCount,
    OpenDebtExposure,
)
from app.debt.values import (
    CustomerId,
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
    UserId,
)
from app.shop.repository import get_shop, list_shops_by_ids
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import (
    _LockedShopCustomer,
    _validate_locked_shop_customer,
)

__all__ = (
    "LockedDebtPredecessor",
    "LockedCustomerHardBlockScope",
    "SqlAlchemyLockedCustomerGlobalHardBlockReader",
    "SqlAlchemyDebtOpenSetReader",
    "CustomerOwnedDebtRow",
    "discover_debt_candidates",
    "debt_aggregate_from_row",
    "get_customer_owned_debt",
    "get_customer_owned_debt_with_shop",
    "get_tenant_debt",
    "insert_debt",
    "list_customer_owned_debts",
    "list_customer_owned_debts_with_shops",
    "list_tenant_debts",
    "lock_debts_in_id_order",
    "lock_customer_hard_block_scope",
    "mark_debt_predecessor_locked",
    "update_locked_debt",
    "validate_locked_debt_predecessor",
    "validate_locked_customer_hard_block_scope",
)


@dataclass(frozen=True, slots=True, repr=False)
class LockedDebtPredecessor:
    shop_customer_id: UUID
    customer_id: UUID
    _session: Session

    def __repr__(self) -> str:
        return "LockedDebtPredecessor(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class LockedCustomerHardBlockScope:
    """Proof that this Session locked the authoritative Customer row."""

    _customer: Customer
    _session: Session

    def __repr__(self) -> str:
        return "LockedCustomerHardBlockScope(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerOwnedDebtRow:
    """Trusted repository result; the service emits the redacted public view."""

    debt: Debt
    shop_name: str

    def __repr__(self) -> str:
        return "CustomerOwnedDebtRow(<redacted>)"


def debt_aggregate_from_row(row: Debt) -> DebtAggregate:
    if not isinstance(row, Debt):
        raise TypeError("row must be a Debt")
    return DebtAggregate(
        id=DebtId(row.id),
        shop_customer_id=ShopCustomerId(row.shop_customer_id),
        created_by_user_id=UserId(row.created_by_user_id),
        original_amount=OriginalAmountUZS(row.original_amount_uzs),
        discount_basis_points=DiscountBasisPoints(row.discount_basis_points),
        discounted_amount=DiscountedAmountUZS(row.discounted_amount_uzs),
        due_date=row.due_date,
        pending_expires_at=row.pending_expires_at,
        status=DebtStatus(row.status),
        revision=DebtRevision(row.revision),
        created_at=row.created_at,
        updated_at=row.updated_at,
        accepted_at=row.accepted_at,
        rejected_at=row.rejected_at,
        cancelled_at=row.cancelled_at,
        expired_at=row.expired_at,
        paid_at=row.paid_at,
        overdue_at=row.overdue_at,
        overdue_revision=(
            None if row.overdue_revision is None else DebtRevision(row.overdue_revision)
        ),
        rejection_reason=(
            None if row.rejection_reason is None else DebtReason(row.rejection_reason)
        ),
        cancellation_reason=(
            None
            if row.cancellation_reason is None
            else DebtReason(row.cancellation_reason)
        ),
    )


def mark_debt_predecessor_locked(
    session: Session, *, locked_shop_customer: _LockedShopCustomer
) -> LockedDebtPredecessor:
    locked = _validate_locked_shop_customer(session, locked_shop_customer)
    return LockedDebtPredecessor(
        shop_customer_id=locked.row.id,
        customer_id=locked.row.customer_id,
        _session=session,
    )


def lock_customer_hard_block_scope(
    session: Session, *, customer_id: CustomerId
) -> LockedCustomerHardBlockScope | None:
    """Lock one Customer before any cross-shop hard-block read."""

    if not isinstance(customer_id, UUID):
        raise TypeError("customer_id must be a CustomerId")
    customer = session.scalar(
        select(Customer).where(Customer.id == customer_id).with_for_update()
    )
    if customer is None:
        return None
    return LockedCustomerHardBlockScope(_customer=customer, _session=session)


class SqlAlchemyLockedCustomerGlobalHardBlockReader:
    """Cross-shop boolean reader behind an authoritative Customer lock token."""

    def __init__(
        self, session: Session, *, locked_customer: LockedCustomerHardBlockScope
    ) -> None:
        self._session = session
        self._locked_customer = validate_locked_customer_hard_block_scope(
            session, locked_customer
        )

    def read_global_hard_block(
        self,
        *,
        customer_id: CustomerId,
        as_of_business_date: date,
    ) -> GlobalHardBlockProjection:
        token = validate_locked_customer_hard_block_scope(
            self._session, self._locked_customer
        )
        if not isinstance(customer_id, UUID):
            raise TypeError("customer_id must be a CustomerId")
        if customer_id != token._customer.id:
            raise ValueError("Hard-block customer is not the locked Customer")
        business_date = require_hard_block_business_date(as_of_business_date)
        blocked = self._session.scalar(
            select(
                exists()
                .where(ShopCustomer.customer_id == token._customer.id)
                .where(Debt.shop_customer_id == ShopCustomer.id)
                .where(
                    or_(
                        Debt.status == DebtStatus.OVERDUE.value,
                        (
                            (Debt.status == DebtStatus.ACTIVE.value)
                            & (Debt.due_date < business_date)
                        ),
                    )
                )
            )
        )
        return GlobalHardBlockProjection(is_blocked=bool(blocked))


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


def list_customer_owned_debts_with_shops(
    session: Session, *, customer_id: UUID
) -> list[CustomerOwnedDebtRow]:
    """Traverse Customer -> ShopCustomer -> Debt; never accept a shop locator."""

    statement = (
        select(Debt, ShopCustomer.shop_id)
        .select_from(Customer)
        .join(ShopCustomer, ShopCustomer.customer_id == Customer.id)
        .join(Debt, Debt.shop_customer_id == ShopCustomer.id)
        .where(Customer.id == customer_id)
        .order_by(Debt.created_at.desc(), Debt.id)
    )
    rows = list(session.execute(statement))
    shops = list_shops_by_ids(
        session,
        shop_ids={ShopId(shop_id) for _debt, shop_id in rows},
    )
    shop_names = {shop.id: shop.name for shop in shops}
    return [
        CustomerOwnedDebtRow(debt=debt, shop_name=shop_names[shop_id])
        for debt, shop_id in rows
    ]


def get_customer_owned_debt_with_shop(
    session: Session, *, customer_id: UUID, debt_id: DebtId
) -> CustomerOwnedDebtRow | None:
    """Resolve one opaque Debt locator exclusively through the own Customer path."""

    statement = (
        select(Debt, ShopCustomer.shop_id)
        .select_from(Customer)
        .join(ShopCustomer, ShopCustomer.customer_id == Customer.id)
        .join(Debt, Debt.shop_customer_id == ShopCustomer.id)
        .where(Customer.id == customer_id, Debt.id == debt_id.as_uuid())
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        return None
    debt, shop_id = row
    shop = get_shop(session, shop_id=ShopId(shop_id))
    if shop is None:
        return None
    return CustomerOwnedDebtRow(debt=debt, shop_name=shop.name)


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
        paid_at=debt.paid_at,
        overdue_at=debt.overdue_at,
        overdue_revision=(
            None if debt.overdue_revision is None else debt.overdue_revision.value
        ),
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
        ("paid_at", debt.paid_at),
        ("overdue_at", debt.overdue_at),
        (
            "overdue_revision",
            None if debt.overdue_revision is None else debt.overdue_revision.value,
        ),
        ("updated_at", debt.updated_at),
    ):
        setattr(row, name, value)
    # A second mutation may legitimately receive the same injected microsecond.
    # Force the domain timestamp into SQL instead of letting the model's generic
    # ``onupdate`` clock replace an equal value with a different instant.
    flag_modified(row, "updated_at")
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


def validate_locked_debt_predecessor(
    session: Session, token: object
) -> LockedDebtPredecessor:
    """Validate the bounded lock token for cross-package persistence adapters."""

    return _validate_predecessor(session, token)


def validate_locked_customer_hard_block_scope(
    session: Session, token: object
) -> LockedCustomerHardBlockScope:
    if not isinstance(token, LockedCustomerHardBlockScope):
        raise TypeError("locked_customer must come from lock_customer_hard_block_scope")
    if token._session is not session:
        raise RuntimeError("locked Customer belongs to a different session")
    if session.get(Customer, token._customer.id) is not token._customer:
        raise RuntimeError("locked Customer is not attached to this session")
    return token


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Debt timestamp must be timezone-aware")
    value.astimezone(UTC)
