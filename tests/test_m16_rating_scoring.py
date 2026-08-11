from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.debt.values import DebtId
from app.rating.contracts import (
    RatingEvent,
    RatingEventOrderKey,
    RatingSnapshot,
    create_on_time_paid_rating_event,
    create_overdue_rating_event,
    numeric_risk_band,
)
from app.rating.enums import RatingEventType, RatingRecordingSource, RiskBand
from app.rating.service import (
    INITIAL_RATING_SCORE,
    IncoherentRatingHistoryError,
    derive_rating_snapshot,
)
from app.rating.values import RatingEventId
from app.shop_customer.values import ShopCustomerId

_PAIR_ID = ShopCustomerId(UUID(int=1))


def _event(
    event_number: int,
    event_type: RatingEventType,
    *,
    debt_number: int | None = None,
    occurred_at: datetime | None = None,
) -> RatingEvent:
    debt_id = DebtId(UUID(int=debt_number or event_number))
    time = occurred_at or datetime(2026, 5, event_number, tzinfo=UTC)
    kwargs = {
        "event_id": RatingEventId(UUID(int=10_000 + event_number)),
        "shop_customer_id": _PAIR_ID,
        "debt_id": debt_id,
        "recording_source": RatingRecordingSource.LIVE,
    }
    if event_type is RatingEventType.ON_TIME_PAID:
        return create_on_time_paid_rating_event(
            **kwargs,
            payment_created_at=time,
        )
    return create_overdue_rating_event(
        **kwargs,
        overdue_at=time,
    )


def test_no_event_and_first_event_band_matrix_is_exact() -> None:
    assert INITIAL_RATING_SCORE == 60
    empty = derive_rating_snapshot((), global_hard_block=False)
    blocked_empty = derive_rating_snapshot((), global_hard_block=True)
    first_positive = derive_rating_snapshot(
        (_event(1, RatingEventType.ON_TIME_PAID),),
        global_hard_block=False,
    )
    first_negative = derive_rating_snapshot(
        (_event(1, RatingEventType.OVERDUE),),
        global_hard_block=False,
    )

    assert (empty.current_score, empty.event_count, empty.band) == (
        60,
        0,
        RiskBand.NEW,
    )
    assert blocked_empty.band is RiskBand.BLOCKED
    assert (first_positive.current_score, first_positive.band) == (
        65,
        RiskBand.YELLOW,
    )
    assert (first_negative.current_score, first_negative.band) == (
        45,
        RiskBand.RED,
    )


def test_sequential_clamp_hits_exact_zero_and_hundred_edges() -> None:
    positives = tuple(
        _event(number, RatingEventType.ON_TIME_PAID) for number in range(1, 11)
    )
    negatives = tuple(
        _event(number, RatingEventType.OVERDUE) for number in range(20, 30)
    )

    assert (
        derive_rating_snapshot(positives[:3], global_hard_block=False).band
        is RiskBand.GREEN
    )
    assert (
        derive_rating_snapshot(positives[:8], global_hard_block=False).current_score
        == 100
    )
    assert (
        derive_rating_snapshot(positives, global_hard_block=False).current_score == 100
    )
    assert (
        derive_rating_snapshot(negatives[:4], global_hard_block=False).current_score
        == 0
    )
    assert derive_rating_snapshot(negatives, global_hard_block=False).current_score == 0


def test_every_integer_score_has_one_exact_numeric_band_and_float_is_rejected() -> None:
    for score in range(101):
        expected = (
            RiskBand.GREEN
            if score >= 75
            else RiskBand.YELLOW
            if score >= 50
            else RiskBand.RED
        )
        assert numeric_risk_band(score) is expected

    for invalid in (-1, 101, 75.0, True):
        with pytest.raises(ValueError, match="Numeric rating score"):
            numeric_risk_band(invalid)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="does not match score"):
        RatingSnapshot(
            band=RiskBand.GREEN,
            current_score=50,
            event_count=1,
        )


def test_final_sum_clamp_counterexample_proves_per_event_clamp() -> None:
    positives = [
        _event(number, RatingEventType.ON_TIME_PAID) for number in range(1, 10)
    ]
    final_negative = _event(20, RatingEventType.OVERDUE)
    sequential = derive_rating_snapshot(
        (*positives, final_negative),
        global_hard_block=False,
    )
    reverse = derive_rating_snapshot(
        (
            _event(1, RatingEventType.OVERDUE),
            *(_event(number, RatingEventType.ON_TIME_PAID) for number in range(2, 11)),
        ),
        global_hard_block=False,
    )

    assert sequential.current_score == 85
    assert min(100, max(0, 60 + (9 * 5) - 15)) == 90
    assert reverse.current_score == 90


def test_total_order_uses_time_then_debt_then_event_type() -> None:
    tied_time = datetime(2026, 5, 1, tzinfo=UTC)
    positive_first = (
        _event(
            1,
            RatingEventType.ON_TIME_PAID,
            debt_number=1,
            occurred_at=tied_time,
        ),
        _event(
            2,
            RatingEventType.OVERDUE,
            debt_number=2,
            occurred_at=tied_time,
        ),
    )
    prefix = tuple(
        _event(
            number,
            RatingEventType.ON_TIME_PAID,
            debt_number=100 + number,
            occurred_at=datetime(2026, 4, number, tzinfo=UTC),
        )
        for number in range(1, 9)
    )

    assert (
        derive_rating_snapshot(
            (*reversed(positive_first), *prefix),
            global_hard_block=False,
        ).current_score
        == 85
    )
    same_source_time = datetime(2026, 6, 1, tzinfo=UTC)
    positive_key = RatingEventOrderKey(
        occurred_at=same_source_time,
        debt_id=DebtId(UUID(int=50)),
        event_type=RatingEventType.ON_TIME_PAID,
    )
    overdue_key = RatingEventOrderKey(
        occurred_at=same_source_time,
        debt_id=DebtId(UUID(int=50)),
        event_type=RatingEventType.OVERDUE,
    )
    assert positive_key.as_sort_key() < overdue_key.as_sort_key()


def test_hard_block_overlay_and_unblock_preserve_numeric_history() -> None:
    history = tuple(
        _event(number, RatingEventType.ON_TIME_PAID) for number in range(1, 4)
    )
    blocked = derive_rating_snapshot(history, global_hard_block=True)
    unblocked = derive_rating_snapshot(history, global_hard_block=False)

    assert blocked.band is RiskBand.BLOCKED
    assert unblocked.band is RiskBand.GREEN
    assert blocked.current_score == unblocked.current_score == 75
    assert blocked.event_count == unblocked.event_count == 3
    assert "75" not in repr(blocked)


def test_duplicate_or_cross_type_debt_history_fails_closed_without_identifiers() -> (
    None
):
    event = _event(1, RatingEventType.ON_TIME_PAID)
    with pytest.raises(
        IncoherentRatingHistoryError,
        match="Rating history is incoherent",
    ) as duplicate:
        derive_rating_snapshot((event, event), global_hard_block=False)
    cross_type = _event(
        2,
        RatingEventType.OVERDUE,
        debt_number=1,
        occurred_at=datetime(2026, 5, 2, tzinfo=UTC),
    )
    with pytest.raises(IncoherentRatingHistoryError) as impossible:
        derive_rating_snapshot((event, cross_type), global_hard_block=False)

    assert str(event.debt_id.as_uuid()) not in str(duplicate.value)
    assert str(event.debt_id.as_uuid()) not in str(impossible.value)
