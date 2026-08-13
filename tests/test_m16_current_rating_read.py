from datetime import UTC, datetime
from inspect import getsource
from uuid import UUID

import pytest

from app.rating.current_read_service import (
    CurrentRiskBandProjection,
    IncoherentScalarRatingHistoryError,
    derive_current_rating_state,
)
from app.rating.enums import RiskBand


def _row(
    number: int,
    *,
    event_type: str,
    occurred_at: datetime | None = None,
) -> tuple[datetime, UUID, str, int]:
    return (
        occurred_at or datetime(2026, 8, number, tzinfo=UTC),
        UUID(int=number),
        event_type,
        {
            "on_time_paid": 5,
            "overdue": -15,
            "written_off": -40,
        }[event_type],
    )


def test_scalar_history_fold_has_score_boolean_and_hard_block_overlay() -> None:
    empty = derive_current_rating_state((), global_hard_block=False)
    blocked_empty = derive_current_rating_state((), global_hard_block=True)
    negative = derive_current_rating_state(
        (_row(1, event_type="overdue"),),
        global_hard_block=False,
    )
    blocked_history = derive_current_rating_state(
        (_row(1, event_type="overdue"),),
        global_hard_block=True,
    )

    assert (empty.current_score, empty.has_history, empty.band) == (
        60,
        False,
        RiskBand.NEW,
    )
    assert blocked_empty.band is RiskBand.BLOCKED
    assert (negative.current_score, negative.has_history, negative.band) == (
        45,
        True,
        RiskBand.RED,
    )
    assert blocked_history.band is RiskBand.BLOCKED
    assert blocked_history.current_score == negative.current_score


def test_scalar_history_is_sequential_and_corruption_fails_closed() -> None:
    positive_rows = tuple(
        _row(number, event_type="on_time_paid") for number in range(1, 9)
    )
    counterexample = (*positive_rows, _row(9, event_type="overdue"))

    assert (
        derive_current_rating_state(
            counterexample,
            global_hard_block=False,
        ).current_score
        == 85
    )
    for corrupt in (
        (_row(1, event_type="overdue"), _row(1, event_type="on_time_paid")),
        (_row(2, event_type="overdue"), _row(1, event_type="on_time_paid")),
        ((datetime(2026, 8, 1, tzinfo=UTC), UUID(int=1), "overdue", 5),),
    ):
        with pytest.raises(
            IncoherentScalarRatingHistoryError,
            match="Rating history is incoherent",
        ) as caught:
            derive_current_rating_state(corrupt, global_hard_block=False)
        assert "00000000" not in str(caught.value)


def test_safe_current_projection_carries_band_only() -> None:
    projection = CurrentRiskBandProjection(band=RiskBand.YELLOW)

    assert projection.band is RiskBand.YELLOW
    assert not hasattr(projection, "current_score")
    assert not hasattr(projection, "has_history")
    assert repr(projection) == "CurrentRiskBandProjection(<safe>)"


def test_written_off_chain_folds_sequentially_and_remains_hard_blocked() -> None:
    debt_id = UUID(int=17)
    rows = (
        (datetime(2026, 8, 1, tzinfo=UTC), debt_id, "overdue", -15),
        (datetime(2026, 8, 2, tzinfo=UTC), debt_id, "written_off", -40),
    )
    numeric = derive_current_rating_state(rows, global_hard_block=False)
    blocked = derive_current_rating_state(rows, global_hard_block=True)

    assert numeric.current_score == 5
    assert numeric.band is RiskBand.RED
    assert blocked.current_score == 5
    assert blocked.band is RiskBand.BLOCKED

    corrupt = (rows[1],)
    with pytest.raises(IncoherentScalarRatingHistoryError):
        derive_current_rating_state(corrupt, global_hard_block=True)


def test_current_rating_read_uses_no_write_or_cache_surface() -> None:
    from app.rating import current_read_service, repository

    source = getsource(current_read_service)
    query = getsource(repository.read_ordered_locked_event_tuples)

    for forbidden in (
        "session.add(",
        "session.flush(",
        "session.commit(",
        "session.rollback(",
        "session.close(",
        "session.execute(update",
        "session.execute(delete",
        "cache",
        "func.sum",
        "SUM(",
    ):
        assert forbidden not in source
    assert "RatingEvent.occurred_at" in query
    assert "RatingEvent.debt_id" in query
    assert "RatingEvent.event_type" in query
    assert "RatingEvent.delta" in query
    assert "RatingEvent(" not in query
    assert "func.sum" not in query.casefold()
