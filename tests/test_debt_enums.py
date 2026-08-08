import pytest

from app.debt.enums import (
    M13_PERSISTED_STATUSES,
    DebtStatus,
    DebtTransitionEvent,
    parse_debt_status,
)


def test_full_debt_status_vocabulary_and_exact_parser_are_frozen() -> None:
    assert tuple(DebtStatus) == (
        DebtStatus.PENDING,
        DebtStatus.ACTIVE,
        DebtStatus.REJECTED,
        DebtStatus.CANCELLED,
        DebtStatus.EXPIRED,
        DebtStatus.PAID,
        DebtStatus.OVERDUE,
        DebtStatus.WRITTEN_OFF,
        DebtStatus.WRITTEN_OFF_SETTLED,
    )

    for status in DebtStatus:
        assert parse_debt_status(status.value) is status

    for malformed in ("PENDING", " pending", "pending ", "written-off", "", None):
        with pytest.raises(ValueError, match="Debt status is invalid"):
            parse_debt_status(malformed)  # type: ignore[arg-type]


def test_only_the_five_m13_statuses_are_persistable() -> None:
    assert M13_PERSISTED_STATUSES == frozenset(
        {
            DebtStatus.PENDING,
            DebtStatus.ACTIVE,
            DebtStatus.REJECTED,
            DebtStatus.CANCELLED,
            DebtStatus.EXPIRED,
        }
    )
    assert not ({DebtStatus.PAID, DebtStatus.OVERDUE} & M13_PERSISTED_STATUSES)
    assert not (
        {DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF_SETTLED}
        & M13_PERSISTED_STATUSES
    )


def test_m13_transition_events_match_the_exact_audit_vocabulary() -> None:
    assert tuple(DebtTransitionEvent) == (
        DebtTransitionEvent.CREATED,
        DebtTransitionEvent.ACCEPTED,
        DebtTransitionEvent.REJECTED,
        DebtTransitionEvent.CANCELLED,
        DebtTransitionEvent.EXPIRED,
    )
