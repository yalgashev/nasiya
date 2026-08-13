"""Closed M16 rating and disclosure vocabulary."""

from enum import StrEnum

__all__ = (
    "PositiveRatingDecision",
    "RatingEventAppendOutcome",
    "RatingEventType",
    "RatingRecordingSource",
    "RiskBand",
    "RiskBandDisclosurePurpose",
    "parse_rating_event_type",
    "parse_rating_recording_source",
    "parse_risk_band",
    "parse_risk_band_disclosure_purpose",
    "rating_event_allowed_recording_sources",
    "rating_event_delta",
)


class RatingEventType(StrEnum):
    ON_TIME_PAID = "on_time_paid"
    OVERDUE = "overdue"
    WRITTEN_OFF = "written_off"
    WRITTEN_OFF_SETTLED = "written_off_settled"


class RatingRecordingSource(StrEnum):
    LIVE = "live"
    HISTORICAL_RECONCILIATION = "historical_reconciliation"


class RiskBand(StrEnum):
    NEW = "new"
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    BLOCKED = "blocked"


class RiskBandDisclosurePurpose(StrEnum):
    DEBT_PROPOSAL_REVIEW = "debt_proposal_review"
    CREDIT_LIMIT_REVIEW = "credit_limit_review"
    EXISTING_DEBT_REVIEW = "existing_debt_review"


class PositiveRatingDecision(StrEnum):
    AWARD = "award"
    NO_BONUS = "no_bonus"
    DAILY_CAP_ALREADY_USED = "daily_cap_already_used"


class RatingEventAppendOutcome(StrEnum):
    APPENDED = "appended"
    SOURCE_ALREADY_EXISTS = "source_already_exists"
    POSITIVE_DAILY_CAP_ALREADY_USED = "positive_daily_cap_already_used"


def rating_event_delta(event_type: RatingEventType) -> int:
    if not isinstance(event_type, RatingEventType):
        raise ValueError("Rating event type is invalid")
    return {
        RatingEventType.ON_TIME_PAID: 5,
        RatingEventType.OVERDUE: -15,
        RatingEventType.WRITTEN_OFF: -40,
        RatingEventType.WRITTEN_OFF_SETTLED: 10,
    }[event_type]


def rating_event_allowed_recording_sources(
    event_type: RatingEventType,
) -> frozenset[RatingRecordingSource]:
    if not isinstance(event_type, RatingEventType):
        raise ValueError("Rating event type is invalid")
    if event_type in {
        RatingEventType.WRITTEN_OFF,
        RatingEventType.WRITTEN_OFF_SETTLED,
    }:
        return frozenset({RatingRecordingSource.LIVE})
    return frozenset(
        {
            RatingRecordingSource.LIVE,
            RatingRecordingSource.HISTORICAL_RECONCILIATION,
        }
    )


def parse_rating_event_type(value: str) -> RatingEventType:
    return _parse(value, RatingEventType, "Rating event type")


def parse_rating_recording_source(value: str) -> RatingRecordingSource:
    return _parse(value, RatingRecordingSource, "Rating recording source")


def parse_risk_band(value: str) -> RiskBand:
    return _parse(value, RiskBand, "Risk band")


def parse_risk_band_disclosure_purpose(value: str) -> RiskBandDisclosurePurpose:
    return _parse(value, RiskBandDisclosurePurpose, "Risk band disclosure purpose")


def _parse[T: StrEnum](value: str, enum_type: type[T], field_name: str) -> T:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} is invalid") from None
