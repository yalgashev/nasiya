from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.debt.rating_ports import PendingWrittenOffRatingEffect
from app.debt.values import DebtId
from app.payment.rating_ports import PendingWrittenOffSettledRatingEffect
from app.rating.contracts import (
    create_on_time_paid_rating_event,
    create_overdue_rating_event,
    create_written_off_rating_event,
    create_written_off_settled_rating_event,
)
from app.rating.enums import RatingEventType, RatingRecordingSource
from app.rating.service import IncoherentRatingHistoryError, derive_rating_snapshot
from app.rating.values import RatingEventId
from app.shop_customer.values import ShopCustomerId


def _chain(*types: RatingEventType):
    debt_id = DebtId(uuid4())
    shop_customer_id = ShopCustomerId(uuid4())
    base = datetime(2026, 1, 1, tzinfo=UTC)
    factories = {
        RatingEventType.ON_TIME_PAID: lambda: create_on_time_paid_rating_event(
            event_id=RatingEventId(uuid4()),
            shop_customer_id=shop_customer_id,
            debt_id=debt_id,
            payment_created_at=base,
            recording_source=RatingRecordingSource.LIVE,
        ),
        RatingEventType.OVERDUE: lambda: create_overdue_rating_event(
            event_id=RatingEventId(uuid4()),
            shop_customer_id=shop_customer_id,
            debt_id=debt_id,
            overdue_at=base,
            recording_source=RatingRecordingSource.LIVE,
        ),
        RatingEventType.WRITTEN_OFF: lambda: create_written_off_rating_event(
            event_id=RatingEventId(uuid4()),
            shop_customer_id=shop_customer_id,
            debt_id=debt_id,
            written_off_at=base,
        ),
        RatingEventType.WRITTEN_OFF_SETTLED: (
            lambda: create_written_off_settled_rating_event(
                event_id=RatingEventId(uuid4()),
                shop_customer_id=shop_customer_id,
                debt_id=debt_id,
                written_off_settled_at=base,
            )
        ),
    }
    return tuple(factories[event_type]() for event_type in types)


@pytest.mark.parametrize(
    "types, score",
    [
        ((RatingEventType.ON_TIME_PAID,), 65),
        ((RatingEventType.OVERDUE,), 45),
        ((RatingEventType.OVERDUE, RatingEventType.WRITTEN_OFF), 5),
        (
            (
                RatingEventType.OVERDUE,
                RatingEventType.WRITTEN_OFF,
                RatingEventType.WRITTEN_OFF_SETTLED,
            ),
            15,
        ),
    ],
)
def test_exact_legal_per_debt_source_chains(types, score: int) -> None:
    snapshot = derive_rating_snapshot(_chain(*types), global_hard_block=False)
    assert snapshot.current_score == score


@pytest.mark.parametrize(
    "types",
    [
        (RatingEventType.WRITTEN_OFF,),
        (RatingEventType.WRITTEN_OFF_SETTLED,),
        (RatingEventType.OVERDUE, RatingEventType.WRITTEN_OFF_SETTLED),
        (RatingEventType.ON_TIME_PAID, RatingEventType.OVERDUE),
    ],
)
def test_missing_or_mixed_predecessor_chains_fail_closed(types) -> None:
    with pytest.raises(IncoherentRatingHistoryError):
        derive_rating_snapshot(_chain(*types), global_hard_block=False)


def test_m17_factories_and_pending_effects_use_exact_source_instant() -> None:
    instant = datetime(2026, 1, 1, 6, tzinfo=UTC)
    debt_id = DebtId(uuid4())
    shop_customer_id = ShopCustomerId(uuid4())
    written_off = create_written_off_rating_event(
        event_id=RatingEventId(uuid4()),
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        written_off_at=instant,
    )
    settled = create_written_off_settled_rating_event(
        event_id=RatingEventId(uuid4()),
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        written_off_settled_at=instant,
    )
    assert (written_off.delta, settled.delta) == (-40, 10)
    assert written_off.recording_source is RatingRecordingSource.LIVE
    assert settled.recording_source is RatingRecordingSource.LIVE
    effect = PendingWrittenOffRatingEffect(
        event_id=uuid4(),
        debt_id=debt_id,
        shop_customer_id=shop_customer_id,
        written_off_at=instant,
    )
    settlement_effect = PendingWrittenOffSettledRatingEffect(
        event_id=uuid4(),
        debt_id=debt_id,
        shop_customer_id=shop_customer_id,
        payment_created_at=instant,
    )
    assert effect.written_off_at == instant
    assert settlement_effect.payment_created_at == instant
    assert "redacted" in repr(effect)
    assert "redacted" in repr(settlement_effect)


def test_local_ports_do_not_import_concrete_rating_implementation() -> None:
    for path in ("app/debt/rating_ports.py", "app/payment/rating_ports.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "app.rating.models" not in source
        assert "app.rating.repository" not in source
        assert "SqlAlchemyLockedRatingAppendAdapter" not in source
        assert "NoOp" not in source
        assert "lock_customer" not in source
