"""Typed rating facts and strictly band-only Shop-safe projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.debt.business_time import tashkent_business_date
from app.debt.values import DebtId
from app.rating.enums import (
    RatingEventAppendOutcome,
    RatingEventType,
    RatingRecordingSource,
    RiskBand,
    RiskBandDisclosurePurpose,
    rating_event_allowed_recording_sources,
    rating_event_delta,
)
from app.rating.values import RatingEventId
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "RatingEvent",
    "RatingEventAppendResult",
    "RatingEventOrderKey",
    "RatingSnapshot",
    "RatingEventValue",
    "RiskBandDisclosureProjection",
    "create_on_time_paid_rating_event",
    "create_overdue_rating_event",
    "create_written_off_rating_event",
    "create_written_off_settled_rating_event",
    "numeric_risk_band",
)


@dataclass(frozen=True, slots=True, repr=False)
class RatingEventValue:
    """Immutable semantic fact without source identity or persistence behavior."""

    event_type: RatingEventType
    recording_source: RatingRecordingSource
    delta: int
    occurred_at: datetime
    business_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, RatingEventType):
            raise ValueError("Rating event type is invalid")
        if not isinstance(self.recording_source, RatingRecordingSource):
            raise ValueError("Rating recording source is invalid")
        if self.recording_source not in rating_event_allowed_recording_sources(
            self.event_type
        ):
            raise ValueError("Rating recording source is invalid for event type")
        if (
            not isinstance(self.delta, int)
            or isinstance(self.delta, bool)
            or self.delta != rating_event_delta(self.event_type)
        ):
            raise ValueError("Rating event delta does not match event type")

        occurred_at = _normalize_aware_utc(
            self.occurred_at,
            field_name="Rating event occurred at",
        )
        if not isinstance(self.business_date, date) or isinstance(
            self.business_date, datetime
        ):
            raise ValueError("Rating event business date is invalid")
        if self.business_date != tashkent_business_date(occurred_at):
            raise ValueError(
                "Rating event business date must match Tashkent occurred date"
            )
        object.__setattr__(self, "occurred_at", occurred_at)

    @classmethod
    def from_occurred_at(
        cls,
        *,
        event_type: RatingEventType,
        recording_source: RatingRecordingSource,
        occurred_at: datetime,
    ) -> RatingEventValue:
        normalized_occurred_at = _normalize_aware_utc(
            occurred_at,
            field_name="Rating event occurred at",
        )
        return cls(
            event_type=event_type,
            recording_source=recording_source,
            delta=rating_event_delta(event_type),
            occurred_at=normalized_occurred_at,
            business_date=tashkent_business_date(normalized_occurred_at),
        )

    def __repr__(self) -> str:
        return "RatingEventValue(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RatingEventOrderKey:
    occurred_at: datetime
    debt_id: DebtId = field(repr=False)
    event_type: RatingEventType

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "occurred_at",
            _normalize_aware_utc(
                self.occurred_at,
                field_name="Rating event order time",
            ),
        )
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Rating event order Debt is invalid")
        if not isinstance(self.event_type, RatingEventType):
            raise ValueError("Rating event order type is invalid")

    def as_sort_key(self) -> tuple[datetime, int, str]:
        return (
            self.occurred_at,
            self.debt_id.as_uuid().int,
            self.event_type.value,
        )

    def __repr__(self) -> str:
        return "RatingEventOrderKey(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RatingEvent:
    """Debt-linked immutable source event; no mutation methods are exposed."""

    id: RatingEventId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    event_type: RatingEventType
    delta: int
    occurred_at: datetime
    business_date: date
    recording_source: RatingRecordingSource

    def __post_init__(self) -> None:
        if not isinstance(self.id, RatingEventId):
            raise ValueError("Rating event identity is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Rating event ShopCustomer is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Rating event Debt is invalid")
        value = RatingEventValue(
            event_type=self.event_type,
            recording_source=self.recording_source,
            delta=self.delta,
            occurred_at=self.occurred_at,
            business_date=self.business_date,
        )
        object.__setattr__(self, "occurred_at", value.occurred_at)

    @property
    def order_key(self) -> RatingEventOrderKey:
        return RatingEventOrderKey(
            occurred_at=self.occurred_at,
            debt_id=self.debt_id,
            event_type=self.event_type,
        )

    @property
    def source_key(self) -> tuple[DebtId, RatingEventType]:
        return (self.debt_id, self.event_type)

    def __repr__(self) -> str:
        return "RatingEvent(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RatingSnapshot:
    """Internal transient fold result; never a Shop-safe projection or cache."""

    band: RiskBand
    current_score: int
    event_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.band, RiskBand):
            raise ValueError("Rating snapshot band is invalid")
        if (
            not isinstance(self.current_score, int)
            or isinstance(self.current_score, bool)
            or not 0 <= self.current_score <= 100
        ):
            raise ValueError("Rating snapshot score is invalid")
        if (
            not isinstance(self.event_count, int)
            or isinstance(self.event_count, bool)
            or self.event_count < 0
        ):
            raise ValueError("Rating snapshot event count is invalid")
        if self.event_count == 0 and self.band not in {
            RiskBand.NEW,
            RiskBand.BLOCKED,
        }:
            raise ValueError("Empty rating history must be NEW or BLOCKED")
        if self.event_count > 0 and self.band is RiskBand.NEW:
            raise ValueError("Rating history cannot remain NEW")
        if self.band is not RiskBand.BLOCKED:
            expected_band = (
                RiskBand.NEW
                if self.event_count == 0
                else numeric_risk_band(self.current_score)
            )
            if self.band is not expected_band:
                raise ValueError("Rating snapshot band does not match score")

    def __repr__(self) -> str:
        return "RatingSnapshot(<redacted>)"


@dataclass(frozen=True, slots=True)
class RatingEventAppendResult:
    outcome: RatingEventAppendOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, RatingEventAppendOutcome):
            raise ValueError("Rating append outcome is invalid")

    @property
    def appended(self) -> bool:
        return self.outcome is RatingEventAppendOutcome.APPENDED


def create_on_time_paid_rating_event(
    *,
    event_id: RatingEventId,
    shop_customer_id: ShopCustomerId,
    debt_id: DebtId,
    payment_created_at: datetime,
    recording_source: RatingRecordingSource,
) -> RatingEvent:
    return _create_source_event(
        event_id=event_id,
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        event_type=RatingEventType.ON_TIME_PAID,
        source_occurred_at=payment_created_at,
        recording_source=recording_source,
    )


def create_overdue_rating_event(
    *,
    event_id: RatingEventId,
    shop_customer_id: ShopCustomerId,
    debt_id: DebtId,
    overdue_at: datetime,
    recording_source: RatingRecordingSource,
) -> RatingEvent:
    return _create_source_event(
        event_id=event_id,
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        event_type=RatingEventType.OVERDUE,
        source_occurred_at=overdue_at,
        recording_source=recording_source,
    )


def create_written_off_rating_event(
    *,
    event_id: RatingEventId,
    shop_customer_id: ShopCustomerId,
    debt_id: DebtId,
    written_off_at: datetime,
) -> RatingEvent:
    return _create_source_event(
        event_id=event_id,
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        event_type=RatingEventType.WRITTEN_OFF,
        source_occurred_at=written_off_at,
        recording_source=RatingRecordingSource.LIVE,
    )


def create_written_off_settled_rating_event(
    *,
    event_id: RatingEventId,
    shop_customer_id: ShopCustomerId,
    debt_id: DebtId,
    written_off_settled_at: datetime,
) -> RatingEvent:
    return _create_source_event(
        event_id=event_id,
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        event_type=RatingEventType.WRITTEN_OFF_SETTLED,
        source_occurred_at=written_off_settled_at,
        recording_source=RatingRecordingSource.LIVE,
    )


def _create_source_event(
    *,
    event_id: RatingEventId,
    shop_customer_id: ShopCustomerId,
    debt_id: DebtId,
    event_type: RatingEventType,
    source_occurred_at: datetime,
    recording_source: RatingRecordingSource,
) -> RatingEvent:
    value = RatingEventValue.from_occurred_at(
        event_type=event_type,
        recording_source=recording_source,
        occurred_at=source_occurred_at,
    )
    return RatingEvent(
        id=event_id,
        shop_customer_id=shop_customer_id,
        debt_id=debt_id,
        event_type=value.event_type,
        delta=value.delta,
        occurred_at=value.occurred_at,
        business_date=value.business_date,
        recording_source=value.recording_source,
    )


@dataclass(frozen=True, slots=True, repr=False)
class RiskBandDisclosureProjection:
    """The complete Shop presentation boundary: band, purpose, and view time."""

    band: RiskBand
    purpose: RiskBandDisclosurePurpose
    viewed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.band, RiskBand):
            raise ValueError("Risk band is invalid")
        if not isinstance(self.purpose, RiskBandDisclosurePurpose):
            raise ValueError("Risk band disclosure purpose is invalid")
        object.__setattr__(
            self,
            "viewed_at",
            _normalize_aware_utc(
                self.viewed_at,
                field_name="Risk band disclosure viewed at",
            ),
        )

    def __repr__(self) -> str:
        return "RiskBandDisclosureProjection(<safe>)"


def _normalize_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be an aware datetime")
    return value.astimezone(UTC)


def numeric_risk_band(score: int) -> RiskBand:
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError("Numeric rating score is invalid")
    if score >= 75:
        return RiskBand.GREEN
    if score >= 50:
        return RiskBand.YELLOW
    return RiskBand.RED
