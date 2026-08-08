from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.contracts import (
    DebtAggregate,
    DebtLifecycleError,
    DebtPaymentTransitionError,
    DebtReason,
)
from app.debt.enums import DebtExpirySource, DebtPaymentFailure, DebtStatus
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
    UserId,
)

CREATED_AT = datetime(2026, 5, 1, 8, tzinfo=UTC)


def _pending_debt() -> DebtAggregate:
    return DebtAggregate.create_pending(
        debt_id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=UserId(uuid4()),
        original_amount=OriginalAmountUZS(value=Decimal("1000")),
        discount_basis_points=DiscountBasisPoints(1000),
        discounted_amount=DiscountedAmountUZS(value=Decimal("900")),
        due_date=date(2026, 5, 4),
        created_at=CREATED_AT,
    )


def test_create_pending_freezes_financial_metadata_and_safe_projection() -> None:
    debt = _pending_debt()

    assert debt.status is DebtStatus.PENDING
    assert debt.revision == DebtRevision(1)
    assert debt.pending_expires_at == CREATED_AT + timedelta(hours=72)
    assert debt.created_at == debt.updated_at == CREATED_AT
    assert debt.paid_at is None
    assert debt.to_projection().paid_at is None
    assert debt.to_projection().status is DebtStatus.PENDING
    assert str(debt.id.as_uuid()) not in repr(debt)
    assert str(debt.shop_customer_id.as_uuid()) not in repr(debt)
    with pytest.raises(FrozenInstanceError):
        debt.due_date = date(2026, 5, 5)  # type: ignore[misc]


def test_each_pending_transition_is_immutable_and_increments_revision() -> None:
    debt = _pending_debt()
    transition_at = CREATED_AT + timedelta(hours=1)

    active = debt.accept(now=transition_at)
    rejected = debt.reject(now=transition_at, reason=DebtReason("not agreed"))
    cancelled = debt.cancel(now=transition_at, reason=DebtReason("operator correction"))
    expired = debt.expire(
        now=debt.pending_expires_at,
        source=DebtExpirySource.INLINE,
    )

    assert active.status is DebtStatus.ACTIVE and active.accepted_at == transition_at
    assert (
        rejected.status is DebtStatus.REJECTED and rejected.rejected_at == transition_at
    )
    assert (
        cancelled.status is DebtStatus.CANCELLED
        and cancelled.cancelled_at == transition_at
    )
    assert (
        expired.status is DebtStatus.EXPIRED
        and expired.expired_at == debt.pending_expires_at
    )
    for transitioned in (active, rejected, cancelled, expired):
        assert transitioned.revision == DebtRevision(2)
        assert transitioned.original_amount == debt.original_amount
        assert transitioned.discounted_amount == debt.discounted_amount
        assert transitioned.due_date == debt.due_date


def test_reasons_and_terminal_timestamps_follow_the_five_status_contract() -> None:
    debt = _pending_debt()

    assert debt.reject(now=CREATED_AT + timedelta(hours=1)).rejection_reason is None
    assert DebtReason("  canonical reason  ").value == "canonical reason"
    assert "canonical reason" not in repr(DebtReason("canonical reason"))
    with pytest.raises(ValueError, match="1 to 500"):
        DebtReason("  ")
    with pytest.raises(ValueError, match="1 to 500"):
        DebtReason("x" * 501)
    with pytest.raises(ValueError, match="control character"):
        DebtReason("line\nbreak")
    with pytest.raises(ValueError, match="cancellation reason is required"):
        debt.cancel(now=CREATED_AT + timedelta(hours=1), reason=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Active debt requires"):
        replace(
            debt,
            status=DebtStatus.ACTIVE,
            revision=DebtRevision(2),
            updated_at=CREATED_AT + timedelta(hours=1),
        )


def test_expiry_wins_pending_actions_and_terminal_debts_cannot_transition_again() -> (
    None
):
    debt = _pending_debt()
    with pytest.raises(DebtLifecycleError, match="has expired"):
        debt.accept(now=debt.pending_expires_at)
    with pytest.raises(DebtLifecycleError, match="has not expired"):
        debt.expire(
            now=debt.pending_expires_at - timedelta(microseconds=1),
            source=DebtExpirySource.BATCH,
        )
    with pytest.raises(DebtLifecycleError, match="not pending"):
        debt.accept(now=CREATED_AT + timedelta(hours=1)).reject(
            now=CREATED_AT + timedelta(hours=2)
        )


def test_record_payment_partial_and_exact_full_are_immutable() -> None:
    active = _pending_debt().accept(now=CREATED_AT + timedelta(hours=1))
    payment_time = CREATED_AT + timedelta(hours=2)

    partial = active.record_payment(
        payment_amount_uzs=Decimal("100"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=DebtRevision(2),
        payment_created_at=payment_time,
    )
    full = active.record_payment(
        payment_amount_uzs=Decimal("900"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=DebtRevision(2),
        payment_created_at=payment_time,
    )

    assert active.status is DebtStatus.ACTIVE and active.revision == DebtRevision(2)
    assert partial.status is DebtStatus.ACTIVE
    assert partial.revision == DebtRevision(3)
    assert partial.updated_at == payment_time
    assert partial.paid_at is None
    assert full.status is DebtStatus.PAID
    assert full.revision == DebtRevision(3)
    assert full.updated_at == full.paid_at == payment_time
    assert full.accepted_at == active.accepted_at
    assert full.to_projection().paid_at == payment_time


def test_paid_metadata_requires_only_acceptance_and_ordered_payment_time() -> None:
    active = _pending_debt().accept(now=CREATED_AT + timedelta(hours=1))
    paid = active.record_payment(
        payment_amount_uzs=Decimal("900"),
        current_remaining_due_uzs=Decimal("900"),
        expected_revision=active.revision,
        payment_created_at=CREATED_AT + timedelta(hours=2),
    )

    with pytest.raises(ValueError, match="acceptance and payment timestamps"):
        replace(paid, paid_at=None)
    with pytest.raises(ValueError, match="Only paid debt"):
        replace(
            active,
            updated_at=CREATED_AT + timedelta(hours=2),
            paid_at=CREATED_AT + timedelta(hours=2),
        )
    with pytest.raises(ValueError, match="cannot precede acceptance"):
        replace(
            paid,
            updated_at=CREATED_AT + timedelta(hours=1),
            paid_at=CREATED_AT,
        )


@pytest.mark.parametrize(
    ("status_case", "payment_time", "remaining", "revision", "amount", "failure"),
    (
        (
            "pending",
            datetime(2026, 5, 5, tzinfo=UTC),
            "0",
            999,
            "999",
            DebtPaymentFailure.NOT_PAYABLE,
        ),
        (
            "active",
            datetime(2026, 5, 4, 19, tzinfo=UTC),
            "0",
            999,
            "999",
            DebtPaymentFailure.NOT_PAYABLE,
        ),
        (
            "active",
            datetime(2026, 5, 4, 18, 59, tzinfo=UTC),
            "0",
            999,
            "999",
            DebtPaymentFailure.NOT_PAYABLE,
        ),
        (
            "active",
            datetime(2026, 5, 4, 18, 59, tzinfo=UTC),
            "900.0",
            999,
            "999",
            DebtPaymentFailure.NOT_PAYABLE,
        ),
        (
            "active",
            datetime(2026, 5, 4, 18, 59, tzinfo=UTC),
            "901",
            999,
            "999",
            DebtPaymentFailure.NOT_PAYABLE,
        ),
        (
            "active",
            datetime(2026, 5, 4, 18, 59, tzinfo=UTC),
            "900",
            999,
            "999",
            DebtPaymentFailure.CHANGED,
        ),
        (
            "active",
            datetime(2026, 5, 4, 18, 59, tzinfo=UTC),
            "900",
            2,
            "901",
            DebtPaymentFailure.AMOUNT_EXCEEDS_BALANCE,
        ),
    ),
)
def test_record_payment_failure_precedence_is_frozen(
    status_case: str,
    payment_time: datetime,
    remaining: str,
    revision: int,
    amount: str,
    failure: DebtPaymentFailure,
) -> None:
    debt = _pending_debt()
    if status_case == "active":
        debt = debt.accept(now=CREATED_AT + timedelta(hours=1))

    with pytest.raises(DebtPaymentTransitionError) as captured:
        debt.record_payment(
            payment_amount_uzs=Decimal(amount),
            current_remaining_due_uzs=Decimal(remaining),
            expected_revision=DebtRevision(revision),
            payment_created_at=payment_time,
        )

    assert captured.value.failure is failure
    assert debt.revision in {DebtRevision(1), DebtRevision(2)}


def test_every_non_active_m14_status_is_not_payable() -> None:
    pending = _pending_debt()
    transition_at = CREATED_AT + timedelta(hours=1)
    active = pending.accept(now=transition_at)
    debts = (
        pending,
        pending.reject(now=transition_at),
        pending.cancel(now=transition_at, reason=DebtReason("cancelled")),
        pending.expire(now=pending.pending_expires_at, source=DebtExpirySource.INLINE),
        active.record_payment(
            payment_amount_uzs=Decimal("900"),
            current_remaining_due_uzs=Decimal("900"),
            expected_revision=active.revision,
            payment_created_at=CREATED_AT + timedelta(hours=2),
        ),
    )

    for debt in debts:
        with pytest.raises(DebtPaymentTransitionError) as captured:
            debt.record_payment(
                payment_amount_uzs=Decimal("1"),
                current_remaining_due_uzs=Decimal("1"),
                expected_revision=debt.revision,
                payment_created_at=CREATED_AT + timedelta(hours=2),
            )
        assert captured.value.failure is DebtPaymentFailure.NOT_PAYABLE
