"""Read-only current rating composition over ordered scalar source facts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.debt.overdue_ports import (
    LockedCustomerGlobalHardBlockReadPort,
    require_hard_block_business_date,
)
from app.debt.repository import (
    LockedCustomerHardBlockScope,
    locked_customer_global_hard_block_reader_factory,
    validate_locked_customer_hard_block_scope,
)
from app.debt.values import CustomerId
from app.rating.contracts import numeric_risk_band
from app.rating.enums import RatingEventType, RiskBand, rating_event_delta
from app.rating.ports import LockedRatingCustomerScope
from app.rating.repository import (
    OrderedRatingEventTuple,
    read_ordered_locked_event_tuples,
)

__all__ = (
    "CurrentRatingState",
    "CurrentRiskBandProjection",
    "IncoherentScalarRatingHistoryError",
    "derive_current_rating_state",
    "read_locked_current_rating_state",
    "read_locked_current_risk_band",
)

_INITIAL_SCORE = 60
_MIN_SCORE = 0
_MAX_SCORE = 100

HardBlockReaderFactory = Callable[
    [Session, LockedCustomerHardBlockScope],
    LockedCustomerGlobalHardBlockReadPort,
]


class IncoherentScalarRatingHistoryError(ValueError):
    """Identifier-free fail-closed error for contradictory persisted facts."""


@dataclass(frozen=True, slots=True, repr=False)
class CurrentRatingState:
    """Internal transient fold result; it is neither stored nor Shop output."""

    current_score: int
    has_history: bool
    band: RiskBand

    def __post_init__(self) -> None:
        if (
            not isinstance(self.current_score, int)
            or isinstance(self.current_score, bool)
            or not _MIN_SCORE <= self.current_score <= _MAX_SCORE
        ):
            raise ValueError("Current rating score is invalid")
        if not isinstance(self.has_history, bool):
            raise ValueError("Current rating history state is invalid")
        if not isinstance(self.band, RiskBand):
            raise ValueError("Current risk band is invalid")
        if not self.has_history and self.band not in {RiskBand.NEW, RiskBand.BLOCKED}:
            raise ValueError("Empty current rating state is invalid")
        if self.has_history and self.band is RiskBand.NEW:
            raise ValueError("Current rating history cannot remain NEW")
        if self.band is not RiskBand.BLOCKED:
            expected_band = (
                RiskBand.NEW
                if not self.has_history
                else numeric_risk_band(self.current_score)
            )
            if self.band is not expected_band:
                raise ValueError("Current risk band does not match score")

    def __repr__(self) -> str:
        return "CurrentRatingState(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CurrentRiskBandProjection:
    """Only the band may cross a Shop-safe current-rating read boundary."""

    band: RiskBand

    def __post_init__(self) -> None:
        if not isinstance(self.band, RiskBand):
            raise ValueError("Current risk band is invalid")

    def __repr__(self) -> str:
        return "CurrentRiskBandProjection(<safe>)"


def derive_current_rating_state(
    rows: Iterable[OrderedRatingEventTuple],
    *,
    global_hard_block: bool,
) -> CurrentRatingState:
    """Sequentially fold already-ordered scalar facts with a per-event clamp."""

    if not isinstance(global_hard_block, bool):
        raise ValueError("Global hard-block state is invalid")
    try:
        received = tuple(rows)
    except TypeError:
        raise IncoherentScalarRatingHistoryError(
            "Rating history is incoherent"
        ) from None

    score = _INITIAL_SCORE
    previous_key: tuple[datetime, int, str, int] | None = None
    by_debt: dict[UUID, list[RatingEventType]] = {}
    for row in received:
        occurred_at, debt_id, event_type, delta = _validate_scalar_event_row(row)
        order_key = (occurred_at, debt_id.int, event_type.value, delta)
        if previous_key is not None and order_key <= previous_key:
            raise IncoherentScalarRatingHistoryError("Rating history is incoherent")
        by_debt.setdefault(debt_id, []).append(event_type)
        previous_key = order_key
        score = min(_MAX_SCORE, max(_MIN_SCORE, score + delta))

    allowed_chains = {
        (RatingEventType.ON_TIME_PAID,),
        (RatingEventType.OVERDUE,),
        (RatingEventType.OVERDUE, RatingEventType.WRITTEN_OFF),
        (
            RatingEventType.OVERDUE,
            RatingEventType.WRITTEN_OFF,
            RatingEventType.WRITTEN_OFF_SETTLED,
        ),
    }
    if any(tuple(chain) not in allowed_chains for chain in by_debt.values()):
        raise IncoherentScalarRatingHistoryError("Rating history is incoherent")

    has_history = bool(received)
    if global_hard_block:
        band = RiskBand.BLOCKED
    elif not has_history:
        band = RiskBand.NEW
    else:
        band = numeric_risk_band(score)
    return CurrentRatingState(
        current_score=score,
        has_history=has_history,
        band=band,
    )


def read_locked_current_rating_state(
    session: Session,
    *,
    locked_customer: LockedCustomerHardBlockScope,
    as_of_business_date: date,
    global_hard_block_reader: LockedCustomerGlobalHardBlockReadPort | None = None,
    hard_block_reader_factory: HardBlockReaderFactory = (
        locked_customer_global_hard_block_reader_factory
    ),
) -> CurrentRatingState:
    """Compose scalar history and M15's effective hard-block overlay, read-only."""

    locked = validate_locked_customer_hard_block_scope(session, locked_customer)
    business_date = require_hard_block_business_date(as_of_business_date)
    if global_hard_block_reader is None:
        if not callable(hard_block_reader_factory):
            raise TypeError("hard_block_reader_factory must be callable")
        reader = hard_block_reader_factory(session, locked)
    elif isinstance(global_hard_block_reader, LockedCustomerGlobalHardBlockReadPort):
        reader = global_hard_block_reader
    else:
        raise TypeError("global_hard_block_reader must implement locked Customer port")

    customer_id = CustomerId(locked._customer.id)
    rows = read_ordered_locked_event_tuples(
        session,
        locked_customer=LockedRatingCustomerScope(
            customer_id=customer_id,
            _session=session,
        ),
    )
    hard_block = reader.read_global_hard_block(
        customer_id=customer_id,
        as_of_business_date=business_date,
    )
    state = derive_current_rating_state(
        rows,
        global_hard_block=hard_block.is_blocked,
    )
    return state


def read_locked_current_risk_band(
    session: Session,
    *,
    locked_customer: LockedCustomerHardBlockScope,
    as_of_business_date: date,
    global_hard_block_reader: LockedCustomerGlobalHardBlockReadPort | None = None,
    hard_block_reader_factory: HardBlockReaderFactory = (
        locked_customer_global_hard_block_reader_factory
    ),
) -> CurrentRiskBandProjection:
    """Return only the safe current band for a caller that cannot use score."""

    state = read_locked_current_rating_state(
        session,
        locked_customer=locked_customer,
        as_of_business_date=as_of_business_date,
        global_hard_block_reader=global_hard_block_reader,
        hard_block_reader_factory=hard_block_reader_factory,
    )
    return CurrentRiskBandProjection(band=state.band)


def _validate_scalar_event_row(
    row: object,
) -> tuple[datetime, UUID, RatingEventType, int]:
    if not isinstance(row, tuple) or len(row) != 4:
        raise IncoherentScalarRatingHistoryError("Rating history is incoherent")
    occurred_at, debt_id, event_type_value, delta = row
    if (
        not isinstance(occurred_at, datetime)
        or occurred_at.tzinfo is None
        or occurred_at.utcoffset() is None
        or not isinstance(debt_id, UUID)
        or not isinstance(event_type_value, str)
        or not isinstance(delta, int)
        or isinstance(delta, bool)
    ):
        raise IncoherentScalarRatingHistoryError("Rating history is incoherent")
    try:
        event_type = RatingEventType(event_type_value)
    except ValueError:
        raise IncoherentScalarRatingHistoryError(
            "Rating history is incoherent"
        ) from None
    if delta != rating_event_delta(event_type):
        raise IncoherentScalarRatingHistoryError("Rating history is incoherent")
    return occurred_at.astimezone(UTC), debt_id, event_type, delta
