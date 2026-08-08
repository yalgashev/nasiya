from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.debt.contracts import DebtAggregate
from app.debt.models import Debt
from app.debt.repository import debt_aggregate_from_row, update_locked_debt
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
    UserId,
)

CREATED_AT = datetime(2026, 8, 8, 8, tzinfo=UTC)
ACCEPTED_AT = CREATED_AT + timedelta(hours=1)
PAID_AT = ACCEPTED_AT + timedelta(hours=1)


def _paid_debt() -> DebtAggregate:
    pending = DebtAggregate.create_pending(
        debt_id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=UserId(uuid4()),
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discount_basis_points=DiscountBasisPoints(1000),
        discounted_amount=DiscountedAmountUZS(Decimal("900")),
        due_date=date(2026, 8, 12),
        created_at=CREATED_AT,
    )
    active = pending.accept(now=ACCEPTED_AT)
    return active.record_payment(
        payment_amount_uzs=Decimal("900"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=DebtRevision(2),
        payment_created_at=PAID_AT,
    )


def _row_from(debt: DebtAggregate) -> Debt:
    return Debt(
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
        accepted_at=debt.accepted_at,
        rejected_at=debt.rejected_at,
        cancelled_at=debt.cancelled_at,
        expired_at=debt.expired_at,
        paid_at=debt.paid_at,
        rejection_reason=None,
        cancellation_reason=None,
        created_at=debt.created_at,
        updated_at=debt.updated_at,
    )


def test_paid_at_round_trips_between_debt_row_and_aggregate() -> None:
    debt = _paid_debt()

    restored = debt_aggregate_from_row(_row_from(debt))

    assert restored.status is debt.status
    assert restored.revision == debt.revision
    assert restored.paid_at == PAID_AT


def test_locked_update_persists_paid_at_revision_and_updated_at() -> None:
    debt = _paid_debt()
    row = _row_from(
        DebtAggregate.create_pending(
            debt_id=debt.id,
            shop_customer_id=debt.shop_customer_id,
            created_by_user_id=debt.created_by_user_id,
            original_amount=debt.original_amount,
            discount_basis_points=debt.discount_basis_points,
            discounted_amount=debt.discounted_amount,
            due_date=debt.due_date,
            created_at=CREATED_AT,
        )
    )
    session = _AttachedSession(row)

    updated = update_locked_debt(session, row=row, debt=debt)  # type: ignore[arg-type]

    assert updated is row
    assert row.status == "paid"
    assert row.revision == 3
    assert row.paid_at == PAID_AT
    assert row.updated_at == PAID_AT
    assert session.flushed is True


class _AttachedSession:
    def __init__(self, row: Debt) -> None:
        self._row = row
        self.flushed = False

    def get(self, model: type[Debt], identifier: object) -> Debt | None:
        assert model is Debt
        assert identifier == self._row.id
        return self._row

    def flush(self) -> None:
        self.flushed = True
