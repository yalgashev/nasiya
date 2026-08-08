"""Tenant-bounded, append-only persistence primitives for M14 Payment."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.policy import GlobalHardBlockProjection, OpenDebtCount, OpenDebtExposure
from app.debt.repository import (
    LockedDebtPredecessor,
    validate_locked_debt_predecessor,
)
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
)
from app.payment.contracts import PaymentAggregate
from app.payment.enums import PaymentMethod
from app.payment.models import Payment
from app.payment.values import (
    PaymentAmountUZS,
    PaymentId,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
    calculate_payment_exposure,
    calculate_remaining_due,
    open_debt_count_contribution,
)
from app.shop.repository import list_shops_by_ids
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer

__all__ = (
    "ScopedPaymentRow",
    "SqlAlchemyPaymentOpenSetReader",
    "get_customer_owned_payment",
    "get_tenant_payment",
    "historical_balance_after",
    "insert_payment",
    "list_customer_owned_debt_payments",
    "list_tenant_debt_payments",
    "payment_aggregate_from_row",
    "posted_payment_total",
    "remaining_due",
)


@dataclass(frozen=True, slots=True, repr=False)
class ScopedPaymentRow:
    """Trusted joined result; opaque identifiers stay out of diagnostics."""

    payment: Payment
    debt: Debt
    shop_name: str

    def __repr__(self) -> str:
        return "ScopedPaymentRow(<redacted>)"


def payment_aggregate_from_row(row: Payment) -> PaymentAggregate:
    if not isinstance(row, Payment):
        raise TypeError("row must be a Payment")
    return PaymentAggregate(
        id=PaymentId(row.id),
        debt_id=DebtId(row.debt_id),
        recorded_by_user_id=row.recorded_by_user_id,
        amount=PaymentAmountUZS(row.amount_uzs),
        method=PaymentMethod(row.method),
        debt_revision_after=DebtRevision(row.debt_revision_after),
        created_at=row.created_at,
    )


def insert_payment(
    session: Session, *, locked_debt: Debt, payment: PaymentAggregate
) -> Payment:
    """Append one payment after the caller has acquired the matching Debt row."""

    if (
        not isinstance(locked_debt, Debt)
        or session.get(Debt, locked_debt.id) is not locked_debt
    ):
        raise RuntimeError("locked_debt must be attached to this session")
    if not isinstance(payment, PaymentAggregate):
        raise TypeError("payment must be a PaymentAggregate")
    if payment.debt_id.as_uuid() != locked_debt.id:
        raise ValueError("Payment does not belong to locked Debt")
    row = Payment(
        id=payment.id.as_uuid(),
        debt_id=payment.debt_id.as_uuid(),
        recorded_by_user_id=payment.recorded_by_user_id,
        amount_uzs=payment.amount.value,
        method=payment.method.value,
        debt_revision_after=payment.debt_revision_after.value,
        created_at=payment.created_at,
    )
    session.add(row)
    session.flush()
    return row


def list_tenant_debt_payments(
    session: Session, *, shop_id: ShopId, debt_id: DebtId
) -> tuple[ScopedPaymentRow, ...]:
    statement = (
        _scoped_payment_statement()
        .where(
            ShopCustomer.shop_id == shop_id,
            Debt.id == debt_id.as_uuid(),
        )
        .order_by(Payment.debt_revision_after, Payment.id)
    )
    return _scoped_rows(session, session.execute(statement))


def get_tenant_payment(
    session: Session, *, shop_id: ShopId, payment_id: PaymentId
) -> ScopedPaymentRow | None:
    statement = _scoped_payment_statement().where(
        ShopCustomer.shop_id == shop_id,
        Payment.id == payment_id.as_uuid(),
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        return None
    return _scoped_rows(session, (row,))[0]


def list_customer_owned_debt_payments(
    session: Session, *, customer_id: UUID, debt_id: DebtId
) -> tuple[ScopedPaymentRow, ...]:
    statement = (
        _scoped_payment_statement()
        .where(
            ShopCustomer.customer_id == customer_id,
            Debt.id == debt_id.as_uuid(),
        )
        .order_by(Payment.debt_revision_after, Payment.id)
    )
    return _scoped_rows(session, session.execute(statement))


def get_customer_owned_payment(
    session: Session, *, customer_id: UUID, payment_id: PaymentId
) -> ScopedPaymentRow | None:
    statement = _scoped_payment_statement().where(
        ShopCustomer.customer_id == customer_id,
        Payment.id == payment_id.as_uuid(),
    )
    row = session.execute(statement).one_or_none()
    if row is None:
        return None
    return _scoped_rows(session, (row,))[0]


def posted_payment_total(
    session: Session, *, debt_id: DebtId, through_revision: int | None = None
) -> PostedPaymentTotalUZS:
    statement = select(func.coalesce(func.sum(Payment.amount_uzs), Decimal("0"))).where(
        Payment.debt_id == debt_id.as_uuid()
    )
    if through_revision is not None:
        if (
            not isinstance(through_revision, int)
            or isinstance(through_revision, bool)
            or through_revision < 1
        ):
            raise ValueError("Payment history revision must be positive")
        statement = statement.where(Payment.debt_revision_after <= through_revision)
    return PostedPaymentTotalUZS(Decimal(session.scalar(statement)))


def remaining_due(session: Session, *, debt: Debt) -> RemainingDueUZS:
    _require_attached_debt(session, debt)
    return calculate_remaining_due(
        discounted_amount=DiscountedAmountUZS(debt.discounted_amount_uzs),
        posted_total=posted_payment_total(session, debt_id=DebtId(debt.id)),
    )


def historical_balance_after(
    session: Session, *, debt: Debt, payment: Payment
) -> RemainingDueUZS:
    _require_attached_debt(session, debt)
    if (
        not isinstance(payment, Payment)
        or session.get(Payment, payment.id) is not payment
    ):
        raise RuntimeError("payment must be attached to this session")
    if payment.debt_id != debt.id:
        raise ValueError("Payment does not belong to Debt")
    return calculate_remaining_due(
        discounted_amount=DiscountedAmountUZS(debt.discounted_amount_uzs),
        posted_total=posted_payment_total(
            session,
            debt_id=DebtId(debt.id),
            through_revision=payment.debt_revision_after,
        ),
    )


class SqlAlchemyPaymentOpenSetReader:
    """Payment-aware open-set reads behind the locked ShopCustomer token."""

    def __init__(
        self, session: Session, *, locked_predecessor: LockedDebtPredecessor
    ) -> None:
        self._session = session
        self._predecessor = validate_locked_debt_predecessor(
            session, locked_predecessor
        )

    def read_open_debt_exposure(
        self, *, shop_customer_id: ShopCustomerId
    ) -> OpenDebtExposure:
        identifier = self._require_parent(shop_customer_id)
        total = Decimal("0")
        for debt, posted in self._open_rows(identifier):
            total += calculate_payment_exposure(
                status=DebtStatus(debt.status),
                original_amount=OriginalAmountUZS(debt.original_amount_uzs),
                discounted_amount=DiscountedAmountUZS(debt.discounted_amount_uzs),
                posted_total=PostedPaymentTotalUZS(Decimal(posted)),
            ).value
        return OpenDebtExposure(total)

    def read_open_debt_count(
        self, *, shop_customer_id: ShopCustomerId
    ) -> OpenDebtCount:
        identifier = self._require_parent(shop_customer_id)
        count = sum(
            open_debt_count_contribution(DebtStatus(debt.status))
            for debt, _posted in self._open_rows(identifier)
        )
        return OpenDebtCount(count)

    def read_global_hard_block(self, *, customer_id: UUID) -> GlobalHardBlockProjection:
        if customer_id != self._predecessor.customer_id:
            raise ValueError("Hard-block customer is not locked predecessor")
        return GlobalHardBlockProjection(is_blocked=False)

    def _open_rows(self, shop_customer_id: UUID) -> list[tuple[Debt, Decimal]]:
        statement = (
            select(
                Debt,
                func.coalesce(func.sum(Payment.amount_uzs), Decimal("0")),
            )
            .outerjoin(Payment, Payment.debt_id == Debt.id)
            .where(
                Debt.shop_customer_id == shop_customer_id,
                Debt.status.in_((DebtStatus.PENDING.value, DebtStatus.ACTIVE.value)),
            )
            .group_by(Debt.id)
            .order_by(Debt.id)
        )
        return [
            (debt, Decimal(posted)) for debt, posted in self._session.execute(statement)
        ]

    def _require_parent(self, shop_customer_id: ShopCustomerId) -> UUID:
        if not isinstance(shop_customer_id, ShopCustomerId):
            raise TypeError("shop_customer_id must be a ShopCustomerId")
        if shop_customer_id.as_uuid() != self._predecessor.shop_customer_id:
            raise ValueError("Open-set ShopCustomer is not locked predecessor")
        return self._predecessor.shop_customer_id


def _scoped_payment_statement():
    return (
        select(Payment, Debt, ShopCustomer.shop_id)
        .select_from(Payment)
        .join(Debt, Debt.id == Payment.debt_id)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
    )


def _scoped_rows(session: Session, rows) -> tuple[ScopedPaymentRow, ...]:
    materialized = tuple(rows)
    shops = list_shops_by_ids(
        session,
        shop_ids={ShopId(shop_id) for _payment, _debt, shop_id in materialized},
    )
    shop_names = {shop.id: shop.name for shop in shops}
    return tuple(
        ScopedPaymentRow(
            payment=payment,
            debt=debt,
            shop_name=shop_names[shop_id],
        )
        for payment, debt, shop_id in materialized
    )


def _require_attached_debt(session: Session, debt: Debt) -> None:
    if not isinstance(debt, Debt) or session.get(Debt, debt.id) is not debt:
        raise RuntimeError("debt must be attached to this session")
