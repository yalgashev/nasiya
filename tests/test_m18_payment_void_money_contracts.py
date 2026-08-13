from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.enums import DebtBalanceBasis
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountedAmountUZS,
    OriginalAmountUZS,
)
from app.payment.contracts import (
    PaymentLedgerFact,
    PaymentLedgerInvariantError,
    PaymentNotVoidableError,
    calculate_non_voided_posted_total,
    latest_non_voided_payment,
    require_latest_non_voided_payment,
)
from app.payment.values import (
    IncoherentPaymentLedgerError,
    PaymentAmountUZS,
    PaymentId,
    PaymentVoidMoney,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
    calculate_payment_void_money,
)


def _fact(
    *,
    debt_id: DebtId,
    revision: int,
    amount: str,
    void_revision: int | None = None,
) -> PaymentLedgerFact:
    return PaymentLedgerFact(
        payment_id=PaymentId(uuid4()),
        debt_id=debt_id,
        amount=PaymentAmountUZS(Decimal(amount)),
        debt_revision_after=DebtRevision(revision),
        void_debt_revision_after=(
            None if void_revision is None else DebtRevision(void_revision)
        ),
    )


def test_current_anti_join_and_revision_as_of_predicate_are_exact() -> None:
    debt_id = DebtId(uuid4())
    first = _fact(debt_id=debt_id, revision=2, amount="200")
    voided = _fact(
        debt_id=debt_id,
        revision=3,
        amount="300",
        void_revision=4,
    )
    latest = _fact(debt_id=debt_id, revision=5, amount="400")
    facts = (latest, voided, first)

    assert calculate_non_voided_posted_total(facts) == PostedPaymentTotalUZS(
        Decimal("600")
    )
    assert {
        revision: calculate_non_voided_posted_total(
            facts, as_of_revision=DebtRevision(revision)
        ).value
        for revision in range(1, 6)
    } == {
        1: Decimal("0"),
        2: Decimal("200"),
        3: Decimal("500"),
        4: Decimal("200"),
        5: Decimal("600"),
    }
    assert not voided.is_currently_non_voided
    assert voided.is_non_voided_at(DebtRevision(3))
    assert not voided.is_non_voided_at(DebtRevision(4))


def test_latest_is_maximum_non_voided_revision_and_denial_is_generic() -> None:
    debt_id = DebtId(uuid4())
    earlier = _fact(debt_id=debt_id, revision=2, amount="100")
    already_voided = _fact(
        debt_id=debt_id,
        revision=4,
        amount="200",
        void_revision=5,
    )
    latest = _fact(debt_id=debt_id, revision=6, amount="300")
    facts = (latest, earlier, already_voided)

    assert latest_non_voided_payment(facts) is latest
    assert (
        require_latest_non_voided_payment(facts, target_payment_id=latest.payment_id)
        is latest
    )
    for denied in (earlier.payment_id, already_voided.payment_id, PaymentId(uuid4())):
        with pytest.raises(PaymentNotVoidableError, match="Payment is not voidable"):
            require_latest_non_voided_payment(facts, target_payment_id=denied)
    assert latest_non_voided_payment((already_voided,)) is None


@pytest.mark.parametrize(
    "facts",
    (
        lambda debt: (
            _fact(debt_id=debt, revision=2, amount="1"),
            _fact(debt_id=debt, revision=2, amount="2"),
        ),
        lambda debt: (
            _fact(debt_id=debt, revision=2, amount="1", void_revision=4),
            _fact(debt_id=debt, revision=3, amount="2", void_revision=4),
        ),
        lambda debt: (
            _fact(debt_id=debt, revision=2, amount="1", void_revision=3),
            _fact(debt_id=debt, revision=3, amount="2"),
        ),
        lambda debt: (
            _fact(debt_id=debt, revision=2, amount="1", void_revision=5),
            _fact(debt_id=debt, revision=4, amount="2"),
        ),
        lambda debt: (
            _fact(debt_id=debt, revision=2, amount="1"),
            _fact(debt_id=DebtId(uuid4()), revision=3, amount="2"),
        ),
    ),
)
def test_ledger_duplicate_revision_and_cross_debt_corruption_fail_closed(
    facts: object,
) -> None:
    with pytest.raises(PaymentLedgerInvariantError):
        calculate_non_voided_posted_total(facts(DebtId(uuid4())))  # type: ignore[operator]


def test_void_revision_must_follow_payment_revision() -> None:
    with pytest.raises(PaymentLedgerInvariantError, match="must follow"):
        _fact(
            debt_id=DebtId(uuid4()),
            revision=3,
            amount="1",
            void_revision=3,
        )


@pytest.mark.parametrize(
    ("basis", "before", "target", "expected_after", "expected_remaining"),
    (
        (DebtBalanceBasis.DISCOUNTED, "900", "900", "0", "900"),
        (DebtBalanceBasis.DISCOUNTED, "600", "400", "200", "700"),
        (DebtBalanceBasis.ORIGINAL, "1000", "250", "750", "250"),
    ),
)
def test_void_money_is_exact_zero_inclusive_and_remaining_positive(
    basis: DebtBalanceBasis,
    before: str,
    target: str,
    expected_after: str,
    expected_remaining: str,
) -> None:
    result = calculate_payment_void_money(
        posted_total_before=PostedPaymentTotalUZS(Decimal(before)),
        target_amount=PaymentAmountUZS(Decimal(target)),
        resulting_balance_basis=basis,
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discounted_amount=DiscountedAmountUZS(Decimal("900")),
    )

    assert result.posted_total_after == PostedPaymentTotalUZS(Decimal(expected_after))
    assert result.remaining_due_after == RemainingDueUZS(Decimal(expected_remaining))
    assert repr(result) == "PaymentVoidMoney(<redacted>)"


def test_void_money_denies_underflow_zero_remaining_and_inexact_result() -> None:
    with pytest.raises(IncoherentPaymentLedgerError, match="exceeds pre-void"):
        calculate_payment_void_money(
            posted_total_before=PostedPaymentTotalUZS(Decimal("1")),
            target_amount=PaymentAmountUZS(Decimal("2")),
            resulting_balance_basis=DebtBalanceBasis.ORIGINAL,
            original_amount=OriginalAmountUZS(Decimal("1000")),
            discounted_amount=DiscountedAmountUZS(Decimal("900")),
        )
    with pytest.raises(IncoherentPaymentLedgerError):
        calculate_payment_void_money(
            posted_total_before=PostedPaymentTotalUZS(Decimal("1001")),
            target_amount=PaymentAmountUZS(Decimal("1")),
            resulting_balance_basis=DebtBalanceBasis.ORIGINAL,
            original_amount=OriginalAmountUZS(Decimal("1000")),
            discounted_amount=DiscountedAmountUZS(Decimal("900")),
        )
    with pytest.raises(IncoherentPaymentLedgerError, match="must equal"):
        PaymentVoidMoney(
            posted_total_before=PostedPaymentTotalUZS(Decimal("10")),
            target_amount=PaymentAmountUZS(Decimal("1")),
            posted_total_after=PostedPaymentTotalUZS(Decimal("8")),
            remaining_due_after=RemainingDueUZS(Decimal("1")),
        )
    with pytest.raises((TypeError, ValueError)):
        PaymentAmountUZS(Decimal("0"))
    with pytest.raises((TypeError, ValueError)):
        PostedPaymentTotalUZS(Decimal("-1"))


def test_ledger_fact_repr_hides_all_identifiers_and_money() -> None:
    fact = _fact(debt_id=DebtId(uuid4()), revision=2, amount="123456")

    rendered = repr(fact)
    assert rendered == "PaymentLedgerFact(<redacted>)"
    assert "123456" not in rendered
    assert str(datetime(2026, 1, 1, tzinfo=UTC)) not in rendered
