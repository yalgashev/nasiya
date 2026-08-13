from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.commands import WriteOffDebtFailure, WriteOffDebtMutationResult
from app.debt.contracts import DebtLifecycleError, WriteOffReason
from app.debt.values import DebtRevision
from app.debt.write_off_core import (
    OverdueWriteOffSourceFacts,
    materialize_persisted_overdue_write_off,
)
from app.idempotency.contracts import IdempotencyOutcome
from tests.test_m17_debt_recovery_contracts import _overdue

NOW = datetime(2026, 1, 7, tzinfo=UTC)


def test_core_returns_typed_write_off_effect_without_persistence() -> None:
    debt = _overdue()
    actor_id = uuid4()
    pending = materialize_persisted_overdue_write_off(
        debt=debt,
        expected_revision=DebtRevision(3),
        actor_user_id=actor_id,
        reason=WriteOffReason.COLLECTION_EXHAUSTED,
        occurred_at=NOW,
        event_id=uuid4(),
        source=OverdueWriteOffSourceFacts(
            posted_total_uzs=Decimal("0"),
            has_unique_overdue_rating=True,
            has_exact_overdue_audit_pair=True,
        ),
    )
    assert pending.debt.written_off_at == NOW
    assert pending.rating_effect.written_off_at == NOW
    assert pending.audit_payload.written_off_revision == DebtRevision(4)
    assert "redacted" in repr(pending)


@pytest.mark.parametrize(
    ("rating", "audits"),
    ((False, True), (True, False), (False, False)),
)
def test_core_rejects_incomplete_overdue_source(rating: bool, audits: bool) -> None:
    with pytest.raises(DebtLifecycleError, match="source"):
        materialize_persisted_overdue_write_off(
            debt=_overdue(),
            expected_revision=DebtRevision(3),
            actor_user_id=uuid4(),
            reason=WriteOffReason.FRAUD_OR_ABUSE,
            occurred_at=NOW,
            event_id=uuid4(),
            source=OverdueWriteOffSourceFacts(
                posted_total_uzs=Decimal("0"),
                has_unique_overdue_rating=rating,
                has_exact_overdue_audit_pair=audits,
            ),
        )


def test_write_off_result_and_failures_are_identifier_redacted() -> None:
    result = WriteOffDebtMutationResult(
        outcome=IdempotencyOutcome.NEW,
        debt_id=_overdue().id,
    )
    assert str(result.debt_id.as_uuid()) not in repr(result)
    assert WriteOffDebtFailure.NOT_WRITABLE_OFF.value == "DEBT_NOT_WRITABLE_OFF"
