"""One authoritative sequential score fold and hard-block-first band policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.customer.models import Customer
from app.debt.models import Debt
from app.rating.contracts import (
    RatingEvent,
    RatingEventAppendResult,
    RatingSnapshot,
    numeric_risk_band,
)
from app.rating.enums import RiskBand
from app.rating.ports import (
    LockedRatingSourceScope,
    RatingEventAppendError,
    validate_locked_rating_source_scope,
)
from app.rating.repository import append_locked_event
from app.shop_customer.models import ShopCustomer

__all__ = (
    "INITIAL_RATING_SCORE",
    "IncoherentRatingHistoryError",
    "derive_rating_snapshot",
    "append_locked_source_event",
)

INITIAL_RATING_SCORE: Final = 60
_MIN_SCORE: Final = 0
_MAX_SCORE: Final = 100


def append_locked_source_event(
    session: Session,
    *,
    locked_source: LockedRatingSourceScope,
    event: RatingEvent,
) -> RatingEventAppendResult:
    """Append one immutable source fact under an inherited Customer lock."""

    source = validate_locked_rating_source_scope(session, locked_source)
    if not isinstance(event, RatingEvent):
        raise TypeError("event must be a RatingEvent")
    if (
        event.debt_id != source.debt_id
        or event.shop_customer_id != source.shop_customer_id
    ):
        raise RatingEventAppendError()
    coherent = session.scalar(
        select(Debt.id)
        .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
        .join(Customer, Customer.id == ShopCustomer.customer_id)
        .where(
            Customer.id == source.customer_id,
            ShopCustomer.id == source.shop_customer_id.as_uuid(),
            Debt.id == source.debt_id.as_uuid(),
            Debt.shop_customer_id == source.shop_customer_id.as_uuid(),
        )
    )
    if coherent != source.debt_id.as_uuid():
        raise RatingEventAppendError()
    return append_locked_event(
        session,
        locked_customer=source.customer_scope,
        event=event,
    )


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
