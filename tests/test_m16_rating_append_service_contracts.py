import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.debt.enums import DebtOverdueSource
from app.debt.overdue_service import (
    PendingOverdueTransitionEffect,
    materialize_locked_overdue_debt,
    materialize_overdue_candidate,
)
from app.debt.rating_ports import PendingOverdueRatingEffect
from app.debt.values import ClawbackIncreaseUZS, DebtId, DebtRevision
from app.payment.rating_ports import PendingOnTimePaidRatingEffect
from app.payment.service import record_debt_payment
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.shop_customer.values import ShopCustomerId


def test_payment_coordinator_requires_local_port_and_preserves_stage_order() -> None:
    signature = inspect.signature(record_debt_payment)
    source = inspect.getsource(record_debt_payment)

    assert signature.parameters["rating_append_port"].default is inspect.Parameter.empty
    assert "app.rating" not in inspect.getsource(inspect.getmodule(record_debt_payment))
    assert ".commit(" not in source
    assert source.index("update_locked_debt(") < source.index("insert_payment(")
    assert source.index("append_pending_overdue(") < source.index(
        "append_pending_overdue_audits("
    )
    assert source.index("append_pending_on_time_paid(") < source.index(
        "append_payment_recorded_audit("
    )


def test_overdue_core_returns_pending_fact_and_candidate_orders_side_effects() -> None:
    core = inspect.getsource(materialize_locked_overdue_debt)
    candidate = inspect.getsource(materialize_overdue_candidate)

    assert "append_audit" not in core
    assert "append_pending_overdue" not in core
    assert candidate.index("append_pending_overdue(") < candidate.index(
        "append_pending_overdue_audits("
    )
    assert ".commit(" not in candidate


def test_concrete_adapter_revalidates_without_customer_relock() -> None:
    source = inspect.getsource(SqlAlchemyLockedRatingAppendAdapter)

    assert "validate_locked_tenant_payment_debt" in source
    assert "validate_locked_overdue_rating_source" in source
    assert "append_locked_source_event" in source
    assert "with_for_update" not in source
    assert "lock_customer" not in source
    assert ".commit(" not in source


def test_pending_effects_are_identifier_redacted() -> None:
    occurred_at = datetime(2026, 8, 12, 8, tzinfo=UTC)
    overdue = PendingOverdueRatingEffect(
        event_id=UUID(int=1),
        debt_id=DebtId(UUID(int=2)),
        shop_customer_id=ShopCustomerId(UUID(int=3)),
        overdue_at=occurred_at,
    )
    transition = PendingOverdueTransitionEffect(
        rating_effect=overdue,
        source=DebtOverdueSource.BATCH,
        overdue_revision=DebtRevision(3),
        balance_increase_uzs=ClawbackIncreaseUZS(Decimal("1")),
        business_date=date(2026, 8, 12),
    )
    positive = PendingOnTimePaidRatingEffect(
        event_id=UUID(int=4),
        debt_id=DebtId(UUID(int=5)),
        shop_customer_id=ShopCustomerId(UUID(int=6)),
        payment_created_at=occurred_at,
        payment_business_date=date(2026, 8, 12),
    )

    for value in (overdue, transition, positive):
        rendered = repr(value)
        assert "00000000" not in rendered
        assert "<redacted>" in rendered
