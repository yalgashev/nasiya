from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.debt.enums import DebtStatus
from app.debt.values import DebtId, DebtRevision, UserId
from app.payment.contracts import (
    PaymentAggregate,
    PaymentHistoryItem,
    PaymentProjection,
    PaymentReceiptProjection,
)
from app.payment.enums import PaymentMethod
from app.payment.values import PaymentAmountUZS, PaymentId, RemainingDueUZS


def _payment_aggregate(
    *, created_at: datetime = datetime(2026, 5, 1, tzinfo=UTC)
) -> PaymentAggregate:
    return PaymentAggregate(
        id=PaymentId(uuid4()),
        debt_id=DebtId(uuid4()),
        recorded_by_user_id=UserId(uuid4()),
        amount=PaymentAmountUZS(Decimal("250")),
        method=PaymentMethod.CASH,
        debt_revision_after=DebtRevision(3),
        created_at=created_at,
    )


def test_payment_aggregate_is_immutable_redacted_and_exactly_bounded() -> None:
    aggregate = _payment_aggregate()

    assert tuple(field.name for field in fields(aggregate)) == (
        "id",
        "debt_id",
        "recorded_by_user_id",
        "amount",
        "method",
        "debt_revision_after",
        "created_at",
    )
    assert aggregate.created_at == datetime(2026, 5, 1, tzinfo=UTC)
    assert str(aggregate.id.as_uuid()) not in repr(aggregate)
    assert "250" not in repr(aggregate)
    with pytest.raises(AttributeError):
        aggregate.method = PaymentMethod.CARD  # type: ignore[misc]

    for forbidden in ("note", "reference", "voided_at", "status", "updated_at"):
        assert forbidden not in {field.name for field in fields(aggregate)}


def test_payment_aggregate_rejects_invalid_private_fields_and_non_utc_time() -> None:
    aggregate = _payment_aggregate()
    with pytest.raises(ValueError, match="Payment ID"):
        PaymentAggregate(
            id=uuid4(),  # type: ignore[arg-type]
            debt_id=aggregate.debt_id,
            recorded_by_user_id=aggregate.recorded_by_user_id,
            amount=aggregate.amount,
            method=aggregate.method,
            debt_revision_after=aggregate.debt_revision_after,
            created_at=aggregate.created_at,
        )
    for invalid_time in (
        datetime(2026, 5, 1),
        datetime(2026, 5, 1, tzinfo=timezone(timedelta(hours=5))),
    ):
        with pytest.raises(ValueError, match="Payment created at"):
            _payment_aggregate(created_at=invalid_time)


def test_payment_projection_and_history_keep_identifiers_safe_and_ordered() -> None:
    aggregate = _payment_aggregate()
    projection = aggregate.to_projection()
    history = aggregate.to_history_item()

    assert isinstance(projection, PaymentProjection)
    assert projection.payment_id == aggregate.id
    assert projection.debt_id == aggregate.debt_id
    assert not hasattr(projection, "recorded_by_user_id")
    assert tuple(field.name for field in fields(history)) == (
        "payment_id",
        "amount",
        "method",
        "debt_revision_after",
        "created_at",
    )
    assert isinstance(history, PaymentHistoryItem)
    assert history.debt_revision_after == DebtRevision(3)
    assert str(aggregate.id.as_uuid()) not in repr(projection)
    assert str(aggregate.id.as_uuid()) not in repr(history)


def test_payment_receipt_projection_has_only_safe_fact_and_balance_fields() -> None:
    receipt = PaymentReceiptProjection(
        amount=PaymentAmountUZS(Decimal("250")),
        method=PaymentMethod.TRANSFER,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        historical_balance_after=RemainingDueUZS(Decimal("750")),
        current_balance=RemainingDueUZS(Decimal("500")),
        current_debt_status=DebtStatus.ACTIVE,
        shop_display_name="  Nasiya   Shop  ",
    )

    assert tuple(field.name for field in fields(receipt)) == (
        "amount",
        "method",
        "created_at",
        "historical_balance_after",
        "current_balance",
        "current_debt_status",
        "shop_display_name",
    )
    assert receipt.shop_display_name == "Nasiya Shop"
    assert "250" not in repr(receipt)
    assert "Nasiya Shop" not in repr(receipt)


def test_payment_receipt_rejects_future_statuses_and_unsafe_fields() -> None:
    kwargs = {
        "amount": PaymentAmountUZS(Decimal("250")),
        "method": PaymentMethod.CARD,
        "created_at": datetime(2026, 5, 1, tzinfo=UTC),
        "historical_balance_after": RemainingDueUZS(Decimal("750")),
        "current_balance": RemainingDueUZS(Decimal("500")),
        "current_debt_status": DebtStatus.ACTIVE,
        "shop_display_name": "Nasiya Shop",
    }
    for future_status in (
        DebtStatus.OVERDUE,
        DebtStatus.WRITTEN_OFF,
        DebtStatus.WRITTEN_OFF_SETTLED,
    ):
        with pytest.raises(ValueError, match="outside the M14 persisted subset"):
            PaymentReceiptProjection(
                **(kwargs | {"current_debt_status": future_status})
            )
    for unsafe_name in ("", "A", "x" * 121, "Nasiya\x00Shop"):
        with pytest.raises(ValueError, match="shop display name"):
            PaymentReceiptProjection(**(kwargs | {"shop_display_name": unsafe_name}))
