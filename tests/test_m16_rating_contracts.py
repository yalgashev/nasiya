from dataclasses import fields
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.rating.contracts import RatingEventValue, RiskBandDisclosureProjection
from app.rating.enums import (
    RatingEventType,
    RatingRecordingSource,
    RiskBand,
    RiskBandDisclosurePurpose,
    parse_rating_event_type,
    parse_rating_recording_source,
    parse_risk_band,
    parse_risk_band_disclosure_purpose,
    rating_event_delta,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_m16_vocabulary_is_preserved_when_m18_extends_rating_events() -> None:
    assert tuple(RatingEventType)[:4] == (
        RatingEventType.ON_TIME_PAID,
        RatingEventType.OVERDUE,
        RatingEventType.WRITTEN_OFF,
        RatingEventType.WRITTEN_OFF_SETTLED,
    )
    assert tuple(RatingRecordingSource) == (
        RatingRecordingSource.LIVE,
        RatingRecordingSource.HISTORICAL_RECONCILIATION,
    )
    assert tuple(RiskBand) == (
        RiskBand.NEW,
        RiskBand.GREEN,
        RiskBand.YELLOW,
        RiskBand.RED,
        RiskBand.BLOCKED,
    )
    assert tuple(RiskBandDisclosurePurpose) == (
        RiskBandDisclosurePurpose.DEBT_PROPOSAL_REVIEW,
        RiskBandDisclosurePurpose.CREDIT_LIMIT_REVIEW,
        RiskBandDisclosurePurpose.EXISTING_DEBT_REVIEW,
    )


def test_m16_vocabulary_parsers_accept_only_canonical_values() -> None:
    for value in RatingEventType:
        assert parse_rating_event_type(value.value) is value
    for value in RatingRecordingSource:
        assert parse_rating_recording_source(value.value) is value
    for value in RiskBand:
        assert parse_risk_band(value.value) is value
    for value in RiskBandDisclosurePurpose:
        assert parse_risk_band_disclosure_purpose(value.value) is value

    parsers = (
        (parse_rating_event_type, "Rating event type"),
        (parse_rating_recording_source, "Rating recording source"),
        (parse_risk_band, "Risk band"),
        (parse_risk_band_disclosure_purpose, "Risk band disclosure purpose"),
    )
    for parser, message in parsers:
        for malformed in (
            "",
            "OVERDUE",
            " override",
            "written-off",
            None,
            True,
        ):
            with pytest.raises(ValueError, match=message):
                parser(malformed)  # type: ignore[arg-type]


def test_rating_event_value_pairs_delta_and_tashkent_business_date() -> None:
    occurred_at = datetime(2026, 1, 1, 19, tzinfo=UTC)
    positive = RatingEventValue.from_occurred_at(
        event_type=RatingEventType.ON_TIME_PAID,
        recording_source=RatingRecordingSource.LIVE,
        occurred_at=occurred_at,
    )
    negative = RatingEventValue.from_occurred_at(
        event_type=RatingEventType.OVERDUE,
        recording_source=RatingRecordingSource.HISTORICAL_RECONCILIATION,
        occurred_at=occurred_at,
    )

    assert positive.delta == rating_event_delta(RatingEventType.ON_TIME_PAID) == 5
    assert negative.delta == rating_event_delta(RatingEventType.OVERDUE) == -15
    assert positive.business_date == negative.business_date == date(2026, 1, 2)
    assert positive.occurred_at.tzinfo is UTC
    assert "on_time_paid" not in repr(positive)
    assert "2026" not in repr(positive)


def test_rating_event_value_rejects_incoherent_or_naive_time_facts() -> None:
    occurred_at = datetime(2026, 5, 1, tzinfo=UTC)
    valid = {
        "event_type": RatingEventType.ON_TIME_PAID,
        "recording_source": RatingRecordingSource.LIVE,
        "delta": 5,
        "occurred_at": occurred_at,
        "business_date": date(2026, 5, 1),
    }

    for invalid in (
        valid | {"delta": -15},
        valid | {"business_date": date(2026, 5, 2)},
        valid | {"occurred_at": datetime(2026, 5, 1)},
        valid | {"business_date": datetime(2026, 5, 1, tzinfo=UTC)},
    ):
        with pytest.raises(ValueError):
            RatingEventValue(**invalid)

    canonical = RatingEventValue.from_occurred_at(
        event_type=RatingEventType.OVERDUE,
        recording_source=RatingRecordingSource.LIVE,
        occurred_at=datetime(
            2026,
            5,
            1,
            5,
            tzinfo=timezone(timedelta(hours=5)),
        ),
    )
    assert canonical.occurred_at == datetime(2026, 5, 1, tzinfo=UTC)


def test_safe_band_projection_contains_only_band_purpose_and_aware_view_time() -> None:
    projection = RiskBandDisclosureProjection(
        band=RiskBand.BLOCKED,
        purpose=RiskBandDisclosurePurpose.EXISTING_DEBT_REVIEW,
        viewed_at=datetime(2026, 5, 1, 5, tzinfo=timezone(timedelta(hours=5))),
    )

    assert tuple(field.name for field in fields(projection)) == (
        "band",
        "purpose",
        "viewed_at",
    )
    assert projection.viewed_at == datetime(2026, 5, 1, tzinfo=UTC)
    assert "blocked" not in repr(projection)
    assert "2026" not in repr(projection)
    with pytest.raises(ValueError, match="Risk band disclosure viewed at"):
        RiskBandDisclosureProjection(
            band=RiskBand.NEW,
            purpose=RiskBandDisclosurePurpose.DEBT_PROPOSAL_REVIEW,
            viewed_at=datetime(2026, 5, 1),
        )


def test_rating_contract_sources_exclude_unrelated_future_vocabulary() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8").casefold()
        for path in sorted((PROJECT_ROOT / "app/rating").glob("*.py"))
    )

    for forbidden in (
        "override",
        "notification",
    ):
        assert forbidden not in source
