from datetime import UTC, date, datetime

import pytest

from app.debt.enums import M15_PERSISTED_STATUSES, M17_PERSISTED_STATUSES, DebtStatus
from app.rating.contracts import RatingEventValue
from app.rating.enums import (
    RatingEventType,
    RatingRecordingSource,
    rating_event_allowed_recording_sources,
    rating_event_delta,
)


def test_m17_persisted_debt_status_family_adds_only_written_off_states() -> None:
    assert M17_PERSISTED_STATUSES == frozenset(
        {
            *M15_PERSISTED_STATUSES,
            DebtStatus.WRITTEN_OFF,
            DebtStatus.WRITTEN_OFF_SETTLED,
        }
    )
    assert M15_PERSISTED_STATUSES < M17_PERSISTED_STATUSES


def test_m17_rating_vocabulary_and_deltas_are_exact_and_exhaustive() -> None:
    assert tuple(RatingEventType) == (
        RatingEventType.ON_TIME_PAID,
        RatingEventType.OVERDUE,
        RatingEventType.WRITTEN_OFF,
        RatingEventType.WRITTEN_OFF_SETTLED,
    )
    assert {
        event_type: rating_event_delta(event_type) for event_type in RatingEventType
    } == {
        RatingEventType.ON_TIME_PAID: 5,
        RatingEventType.OVERDUE: -15,
        RatingEventType.WRITTEN_OFF: -40,
        RatingEventType.WRITTEN_OFF_SETTLED: 10,
    }

    for invalid in ("written_off", -40, None, True):
        with pytest.raises(ValueError, match="Rating event type is invalid"):
            rating_event_delta(invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("event_type", "allowed_sources"),
    (
        (
            RatingEventType.ON_TIME_PAID,
            frozenset(
                {
                    RatingRecordingSource.LIVE,
                    RatingRecordingSource.HISTORICAL_RECONCILIATION,
                }
            ),
        ),
        (
            RatingEventType.OVERDUE,
            frozenset(
                {
                    RatingRecordingSource.LIVE,
                    RatingRecordingSource.HISTORICAL_RECONCILIATION,
                }
            ),
        ),
        (RatingEventType.WRITTEN_OFF, frozenset({RatingRecordingSource.LIVE})),
        (
            RatingEventType.WRITTEN_OFF_SETTLED,
            frozenset({RatingRecordingSource.LIVE}),
        ),
    ),
)
def test_m17_rating_recording_sources_are_closed_per_event_type(
    event_type: RatingEventType,
    allowed_sources: frozenset[RatingRecordingSource],
) -> None:
    assert rating_event_allowed_recording_sources(event_type) == allowed_sources

    occurred_at = datetime(2026, 8, 12, tzinfo=UTC)
    for source in RatingRecordingSource:
        if source in allowed_sources:
            event = RatingEventValue.from_occurred_at(
                event_type=event_type,
                recording_source=source,
                occurred_at=occurred_at,
            )
            assert event.delta == rating_event_delta(event_type)
            assert event.business_date == date(2026, 8, 12)
        else:
            with pytest.raises(ValueError, match="invalid for event type"):
                RatingEventValue.from_occurred_at(
                    event_type=event_type,
                    recording_source=source,
                    occurred_at=occurred_at,
                )


def test_m17_vocabulary_excludes_reversal_compensation_and_override() -> None:
    values = {event_type.value for event_type in RatingEventType}

    assert not values & {
        "written_off_reversed",
        "compensation",
        "override",
        "voided",
        "notification",
    }
