from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.auth.error_codes import ErrorCode
from app.debt.contracts import DebtAggregate, DebtReason
from app.debt.enums import DebtBalanceBasis, DebtExpirySource, DebtStatus
from app.debt.values import (
    DebtId,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
    UserId,
)
from app.payment.policy import (
    capture_payment_server_now,
    evaluate_locked_debt_payability,
)


def _pending_debt(*, due_date: date = date(2026, 8, 9)) -> DebtAggregate:
    return DebtAggregate.create_pending(
        debt_id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=UserId(uuid4()),
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discount_basis_points=DiscountBasisPoints(0),
        discounted_amount=DiscountedAmountUZS(Decimal("1000")),
        due_date=due_date,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _active_debt(*, due_date: date = date(2026, 8, 9)) -> DebtAggregate:
    return _pending_debt(due_date=due_date).accept(
        now=datetime(2026, 8, 1, 1, tzinfo=UTC)
    )


def test_active_debt_switches_to_original_basis_at_tashkent_midnight() -> None:
    debt = _active_debt()

    allowed = evaluate_locked_debt_payability(
        debt=debt,
        captured_now=capture_payment_server_now(
            datetime(2026, 8, 9, 18, 59, 59, 999999, tzinfo=UTC)
        ),
    )
    late = evaluate_locked_debt_payability(
        debt=debt,
        captured_now=capture_payment_server_now(datetime(2026, 8, 9, 19, tzinfo=UTC)),
    )

    assert allowed.is_payable and allowed.error is None
    assert allowed.payment_created_at == datetime(
        2026, 8, 9, 18, 59, 59, 999999, tzinfo=UTC
    )
    assert allowed.balance_basis is DebtBalanceBasis.DISCOUNTED
    assert late.is_payable and late.error is None
    assert late.balance_basis is DebtBalanceBasis.ORIGINAL
    assert late.requires_overdue_transition


@pytest.mark.parametrize(
    "status",
    (
        DebtStatus.PENDING,
        DebtStatus.REJECTED,
        DebtStatus.CANCELLED,
        DebtStatus.EXPIRED,
        DebtStatus.PAID,
    ),
)
def test_non_payable_status_is_denied(status: DebtStatus) -> None:
    debt = _pending_debt()
    if status is DebtStatus.PENDING:
        pass
    elif status is DebtStatus.PAID:
        active = debt.accept(now=datetime(2026, 8, 1, 1, tzinfo=UTC))
        debt = active.record_payment(
            payment_amount_uzs=Decimal("1000"),
            current_remaining_due_uzs=Decimal("1000"),
            expected_revision=active.revision,
            payment_created_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
    elif status is DebtStatus.REJECTED:
        debt = debt.reject(now=datetime(2026, 8, 1, 1, tzinfo=UTC))
    elif status is DebtStatus.CANCELLED:
        debt = debt.cancel(
            now=datetime(2026, 8, 1, 1, tzinfo=UTC), reason=DebtReason("reason")
        )
    else:
        debt = debt.expire(
            now=debt.pending_expires_at,
            source=DebtExpirySource.INLINE,
        )

    decision = evaluate_locked_debt_payability(
        debt=debt,
        captured_now=capture_payment_server_now(datetime(2026, 8, 9, tzinfo=UTC)),
    )

    assert decision.error is ErrorCode.DEBT_NOT_PAYABLE


def test_gate_has_no_mutation_inputs_or_side_effects() -> None:
    decision = evaluate_locked_debt_payability(
        debt=_active_debt(),
        captured_now=capture_payment_server_now(datetime(2026, 8, 9, tzinfo=UTC)),
    )

    assert decision.is_payable
    assert set(decision.__dataclass_fields__) == {
        "payment_created_at",
        "balance_basis",
        "requires_overdue_transition",
        "error",
    }
