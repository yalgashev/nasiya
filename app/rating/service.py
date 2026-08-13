"""One authoritative sequential score fold and hard-block-first band policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.customer.models import Customer
from app.debt.models import Debt
from app.debt.values import DebtRevision
from app.rating.contracts import (
    RatingEvent,
    RatingEventAppendResult,
    RatingSnapshot,
    numeric_risk_band,
)
from app.rating.disclosure_service import (
    DisclosureMutationRejected,
    DisclosurePersistenceError,
    read_risk_band_disclosure_snapshot,
    record_risk_band_disclosure,
)
from app.rating.enums import RatingEventType, RiskBand
from app.rating.ports import (
    LockedRatingSourceScope,
    RatingEventAppendError,
    validate_locked_rating_source_scope,
)
from app.rating.repository import append_locked_event
from app.shop_customer.models import ShopCustomer

__all__ = (
    "INITIAL_RATING_SCORE",
    "DisclosureMutationRejected",
    "DisclosurePersistenceError",
    "IncoherentRatingHistoryError",
    "append_locked_source_event",
    "derive_rating_snapshot",
    "read_risk_band_disclosure_snapshot",
    "record_risk_band_disclosure",
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
    if len(source_keys) != len(received):
        raise IncoherentRatingHistoryError("Rating history is incoherent")
    by_debt: dict[object, list[RatingEvent]] = {}
    for event in received:
        by_debt.setdefault(event.debt_id, []).append(event)
    for debt_events in by_debt.values():
        source_ordered = sorted(
            debt_events,
            key=lambda event: event.order_key.as_sort_key(),
        )
        _validate_debt_cycles(source_ordered)
    return tuple(sorted(received, key=lambda event: event.order_key.as_sort_key()))


def _validate_debt_cycles(events: list[RatingEvent]) -> None:
    positives: dict[tuple[RatingEventType, DebtRevision], bool] = {}
    overdue_seen = False
    written_off_seen = False
    open_settlement_revision: DebtRevision | None = None
    for event in events:
        event_type = event.event_type
        revision = event.source_revision
        if event_type is RatingEventType.ON_TIME_PAID:
            if overdue_seen:
                raise IncoherentRatingHistoryError("Rating history is incoherent")
            positives[(event_type, revision)] = False
            continue
        if event_type is RatingEventType.ON_TIME_PAID_VOIDED:
            key = (RatingEventType.ON_TIME_PAID, revision)
            if overdue_seen or key not in positives or positives[key]:
                raise IncoherentRatingHistoryError("Rating history is incoherent")
            positives[key] = True
            continue
        if event_type is RatingEventType.OVERDUE:
            if overdue_seen or any(
                not compensated for compensated in positives.values()
            ):
                raise IncoherentRatingHistoryError("Rating history is incoherent")
            overdue_seen = True
            continue
        if event_type is RatingEventType.WRITTEN_OFF:
            if not overdue_seen or written_off_seen:
                raise IncoherentRatingHistoryError("Rating history is incoherent")
            written_off_seen = True
            continue
        if event_type is RatingEventType.WRITTEN_OFF_SETTLED:
            if not written_off_seen or open_settlement_revision is not None:
                raise IncoherentRatingHistoryError("Rating history is incoherent")
            open_settlement_revision = revision
            continue
        if event_type is RatingEventType.WRITTEN_OFF_SETTLED_VOIDED:
            if open_settlement_revision != revision:
                raise IncoherentRatingHistoryError("Rating history is incoherent")
            open_settlement_revision = None
            continue
        raise IncoherentRatingHistoryError("Rating history is incoherent")
