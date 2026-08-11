"""One authoritative sequential score fold and hard-block-first band policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from app.rating.contracts import RatingEvent, RatingSnapshot, numeric_risk_band
from app.rating.enums import RiskBand

__all__ = (
    "INITIAL_RATING_SCORE",
    "IncoherentRatingHistoryError",
    "derive_rating_snapshot",
)

INITIAL_RATING_SCORE: Final = 60
_MIN_SCORE: Final = 0
_MAX_SCORE: Final = 100


class IncoherentRatingHistoryError(ValueError):
    """Raised without identifiers when one Debt has impossible source history."""


def derive_rating_snapshot(
    events: Iterable[RatingEvent],
    *,
    global_hard_block: bool,
) -> RatingSnapshot:
    if not isinstance(global_hard_block, bool):
        raise ValueError("Global hard-block state is invalid")
    ordered = _ordered_coherent_events(events)
    score = INITIAL_RATING_SCORE
    for event in ordered:
        score = min(_MAX_SCORE, max(_MIN_SCORE, score + event.delta))

    if global_hard_block:
        band = RiskBand.BLOCKED
    elif not ordered:
        band = RiskBand.NEW
    else:
        band = numeric_risk_band(score)
    return RatingSnapshot(
        band=band,
        current_score=score,
        event_count=len(ordered),
    )


def _ordered_coherent_events(
    events: Iterable[RatingEvent],
) -> tuple[RatingEvent, ...]:
    try:
        received = tuple(events)
    except TypeError:
        raise ValueError("Rating events must be iterable") from None
    if any(not isinstance(event, RatingEvent) for event in received):
        raise ValueError("Rating history contains an invalid event")

    source_keys = {event.source_key for event in received}
    debt_ids = {event.debt_id for event in received}
    if len(source_keys) != len(received) or len(debt_ids) != len(received):
        raise IncoherentRatingHistoryError("Rating history is incoherent")
    return tuple(sorted(received, key=lambda event: event.order_key.as_sort_key()))
