from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.audit.contracts import (
    AuditEventType,
    DebtWrittenOffAuditPayload,
    DebtWrittenOffSettledAuditPayload,
    PaymentRecordedAuditPayload,
    create_debt_written_off_audit_event,
    create_debt_written_off_settled_audit_event,
)
from app.audit.redaction import redact_audit_payload
from app.debt.contracts import DebtAggregate, WriteOffReason
from app.debt.enums import DebtBalanceBasis, DebtStatus
from app.debt.policy import is_unresolved_persisted_hard_block_status
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
)
from app.idempotency.contracts import IdempotencyEndpoint, IdempotencyResultType
from app.payment.enums import PaymentMethod
from app.payment.policy import CapturedPaymentServerNow, evaluate_locked_debt_payability
from app.payment.values import (
    PaymentAmountUZS,
    PostedPaymentTotalUZS,
    calculate_overdue_remaining_due,
    calculate_payment_exposure,
    open_debt_count_contribution,
)


def _written_off() -> DebtAggregate:
    return DebtAggregate(
        id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=uuid4(),
        original_amount=OriginalAmountUZS(Decimal("100000")),
        discount_basis_points=DiscountBasisPoints(1000),
        discounted_amount=DiscountedAmountUZS(Decimal("90000")),
        due_date=date(2026, 1, 5),
        pending_expires_at=datetime(2026, 1, 4, tzinfo=UTC),
        status=DebtStatus.WRITTEN_OFF,
        revision=DebtRevision(4),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 7, tzinfo=UTC),
        accepted_at=datetime(2026, 1, 2, tzinfo=UTC),
        overdue_at=datetime(2026, 1, 6, tzinfo=UTC),
        overdue_revision=DebtRevision(3),
        written_off_at=datetime(2026, 1, 7, tzinfo=UTC),
        written_off_revision=DebtRevision(4),
        written_off_reason=WriteOffReason.CUSTOMER_UNREACHABLE,
        written_off_by_user_id=uuid4(),
    )


def test_written_off_payment_uses_original_basis_and_preserves_tt_open_set() -> None:
    debt = _written_off()
    decision = evaluate_locked_debt_payability(
        debt=debt,
        captured_now=CapturedPaymentServerNow(datetime(2026, 1, 8, tzinfo=UTC)),
    )
    assert decision.balance_basis is DebtBalanceBasis.ORIGINAL
    remaining = calculate_overdue_remaining_due(
        original_amount=debt.original_amount,
        posted_total=PostedPaymentTotalUZS(Decimal("40000")),
    )
    assert remaining.value == Decimal("60000")
    assert (
        calculate_payment_exposure(
            status=DebtStatus.WRITTEN_OFF,
            original_amount=debt.original_amount,
            discounted_amount=debt.discounted_amount,
            posted_total=PostedPaymentTotalUZS(Decimal("40000")),
        ).value
        == 0
    )
    assert open_debt_count_contribution(DebtStatus.WRITTEN_OFF) == 0


def test_hard_block_is_debt_status_only_and_settlement_removes_this_overlay() -> None:
    assert is_unresolved_persisted_hard_block_status(DebtStatus.OVERDUE)
    assert is_unresolved_persisted_hard_block_status(DebtStatus.WRITTEN_OFF)
    assert not is_unresolved_persisted_hard_block_status(DebtStatus.WRITTEN_OFF_SETTLED)
    assert not is_unresolved_persisted_hard_block_status(DebtStatus.PAID)


def test_payment_recorded_supports_partial_and_full_recovery_only() -> None:
    partial = PaymentRecordedAuditPayload(
        amount=PaymentAmountUZS(Decimal("1")),
        method=PaymentMethod.CASH,
        from_status=DebtStatus.WRITTEN_OFF,
        to_status=DebtStatus.WRITTEN_OFF,
        debt_revision_after=DebtRevision(5),
    )
    full = PaymentRecordedAuditPayload(
        amount=PaymentAmountUZS(Decimal("99999")),
        method=PaymentMethod.CASH,
        from_status=DebtStatus.WRITTEN_OFF,
        to_status=DebtStatus.WRITTEN_OFF_SETTLED,
        debt_revision_after=DebtRevision(5),
    )
    assert partial.as_candidate_metadata()["to_status"] == "written_off"
    assert full.as_candidate_metadata()["to_status"] == "written_off_settled"
    with pytest.raises(ValueError, match="target"):
        PaymentRecordedAuditPayload(
            amount=PaymentAmountUZS(Decimal("1")),
            method=PaymentMethod.CASH,
            from_status=DebtStatus.WRITTEN_OFF,
            to_status=DebtStatus.PAID,
            debt_revision_after=DebtRevision(5),
        )


def test_exact_audit_payloads_actor_object_time_and_revision_are_strict() -> None:
    actor_id = uuid4()
    debt_id = uuid4()
    instant = datetime(2026, 1, 7, tzinfo=UTC)
    writeoff_payload = DebtWrittenOffAuditPayload(DebtRevision(4))
    writeoff = create_debt_written_off_audit_event(
        actor_user_id=actor_id,
        debt_id=debt_id,
        occurred_at=instant,
        written_off_at=instant,
        current_revision=DebtRevision(4),
        payload=writeoff_payload,
    )
    assert writeoff.event_type is AuditEventType.DEBT_WRITTEN_OFF
    assert redact_audit_payload(writeoff) == {
        "reason_provided": True,
        "from_status": "overdue",
        "to_status": "written_off",
        "written_off_revision": 4,
    }
    settlement_payload = DebtWrittenOffSettledAuditPayload(DebtRevision(5))
    settlement = create_debt_written_off_settled_audit_event(
        actor_user_id=actor_id,
        debt_id=debt_id,
        occurred_at=instant,
        written_off_settled_at=instant,
        current_revision=DebtRevision(5),
        payload=settlement_payload,
    )
    assert settlement.event_type is AuditEventType.DEBT_WRITTEN_OFF_SETTLED
    assert redact_audit_payload(settlement) == {
        "source": "payment",
        "from_status": "written_off",
        "to_status": "written_off_settled",
        "debt_revision_after": 5,
    }
    with pytest.raises(ValueError, match="time"):
        create_debt_written_off_audit_event(
            actor_user_id=actor_id,
            debt_id=debt_id,
            occurred_at=instant,
            written_off_at=datetime(2026, 1, 8, tzinfo=UTC),
            current_revision=DebtRevision(4),
            payload=writeoff_payload,
        )


def test_exact_admin_idempotency_pair_contract() -> None:
    assert IdempotencyEndpoint.ADMIN_DEBTS_WRITE_OFF.value == "admin.debts.write_off"
    assert IdempotencyResultType.DEBT.value == "debt"
