from datetime import UTC, datetime
from decimal import Decimal
from inspect import getsource

import pytest

from app.auth.error_codes import ErrorCode
from app.payment.service import (
    PaymentAmountOutcome,
    PaymentMutationRejected,
    decide_locked_payment_amount,
    record_debt_payment,
)
from app.payment.values import PaymentAmountUZS, RemainingDueUZS


@pytest.mark.parametrize(
    ("amount", "remaining", "expected"),
    (
        ("1", "2", PaymentAmountOutcome.PARTIAL),
        ("999999999999", "1000000000000", PaymentAmountOutcome.PARTIAL),
        ("1", "1", PaymentAmountOutcome.FULL),
        ("1000000000000", "1000000000000", PaymentAmountOutcome.FULL),
    ),
)
def test_locked_amount_decision_has_exact_partial_full_and_max_boundaries(
    amount: str, remaining: str, expected: PaymentAmountOutcome
) -> None:
    assert (
        decide_locked_payment_amount(
            amount=PaymentAmountUZS(Decimal(amount)),
            remaining=RemainingDueUZS(Decimal(remaining)),
        )
        is expected
    )


def test_locked_amount_decision_rejects_only_overpayment_with_stable_error() -> None:
    with pytest.raises(PaymentMutationRejected) as captured:
        decide_locked_payment_amount(
            amount=PaymentAmountUZS(Decimal("2")),
            remaining=RemainingDueUZS(Decimal("1")),
        )

    assert captured.value.error is ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE
    assert str(captured.value) == ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE.value


def test_coordinator_source_freezes_gate_and_write_order() -> None:
    source = getsource(record_debt_payment)

    ordered_symbols = (
        "find_completed_key(",
        "lock_tenant_payment_predecessors(",
        "insert_or_resolve_key(",
        "lock_tenant_payment_debt(",
        "capture_payment_server_now(clock())",
        "evaluate_locked_debt_payability(",
        "command.expected_revision != debt.revision",
        "command.expected_balance_basis is not payability.balance_basis",
        "read_locked_payment_balance(",
        "decide_locked_payment_amount(",
        "insert_payment(",
        "update_locked_debt(",
        "append_payment_recorded_audit(",
        "append_debt_paid_audit(",
    )
    positions = tuple(source.index(symbol) for symbol in ordered_symbols)
    assert positions == tuple(sorted(positions))
    assert "commit(" not in source
    assert "rollback(" not in source
    assert "sleep(" not in source
    assert "display" not in source.casefold()


def test_payment_rejection_contains_no_mutation_values() -> None:
    rejected = PaymentMutationRejected(ErrorCode.DEBT_CHANGED)
    assert repr(rejected) == "PaymentMutationRejected(error='DEBT_CHANGED')"
    assert datetime(2026, 8, 9, tzinfo=UTC).isoformat() not in repr(rejected)
