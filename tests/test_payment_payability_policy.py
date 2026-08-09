from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.auth.error_codes import ErrorCode
from app.debt.contracts import DebtAggregate, DebtReason
from app.debt.enums import DebtExpirySource, DebtStatus
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


def test_active_debt_is_payable_through_last_tashkent_microsecond() -> None:
    debt = _active_debt()

    allowed = evaluate_locked_debt_payability(
        debt=debt,
        captured_now=capture_payment_server_now(
            datetime(2026, 8, 9, 18, 59, 59, 999999, tzinfo=UTC)
        ),
    )
    denied = evaluate_locked_debt_payability(
        debt=debt,
        captured_now=capture_payment_server_now(datetime(2026, 8, 9, 19, tzinfo=UTC)),
    )

    assert allowed.is_payable and allowed.error is None
    assert allowed.payment_created_at == datetime(
        2026, 8, 9, 18, 59, 59, 999999, tzinfo=UTC
    )
    assert not denied.is_payable
    assert denied.error is ErrorCode.DEBT_NOT_PAYABLE


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
def test_non_active_status_is_denied_before_due_date_predicate(
    monkeypatch: pytest.MonkeyPatch, status: DebtStatus
) -> None:
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

    monkeypatch.setattr(
        "app.payment.policy.is_payment_due_date_payable",
        lambda **_kwargs: pytest.fail(
            "due-date predicate must not run for non-active Debt"
        ),
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
    assert set(decision.__dataclass_fields__) == {"payment_created_at", "error"}
