from pathlib import Path

from app.audit.contracts import AuditEventType
from app.debt.enums import M15_PERSISTED_STATUSES, M17_PERSISTED_STATUSES, DebtStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_m15_family_stays_frozen_while_m17_extends_current_persistence() -> None:
    assert DebtStatus.OVERDUE in M15_PERSISTED_STATUSES
    assert DebtStatus.WRITTEN_OFF not in M15_PERSISTED_STATUSES
    assert DebtStatus.WRITTEN_OFF_SETTLED not in M15_PERSISTED_STATUSES

    assert M17_PERSISTED_STATUSES == M15_PERSISTED_STATUSES | {
        DebtStatus.WRITTEN_OFF,
        DebtStatus.WRITTEN_OFF_SETTLED,
    }
    m15_migration = (
        PROJECT_ROOT / "alembic/versions/b6c7d8e9f0a1_add_overdue_persistence.py"
    ).read_text(encoding="utf-8")
    assert "written_off" not in m15_migration.casefold()


def test_m15_adds_only_the_two_overdue_audit_event_names() -> None:
    event_values = {event.value for event in AuditEventType}

    assert {"debt.overdue", "debt.clawback_applied"} <= event_values
    assert (
        not {
            "debt.clawback_reversed",
            "rating.changed",
            "notification.sent",
            "scheduler.job_run",
        }
        & event_values
    )


def test_m15_vocabulary_does_not_prebuild_future_persistence_or_orchestration() -> None:
    source = "\n".join(
        (PROJECT_ROOT / path).read_text(encoding="utf-8")
        for path in (
            "app/debt/enums.py",
            "app/audit/contracts.py",
        )
    ).casefold()

    for forbidden in (
        "rating.",
        "notification.",
        "scheduler.",
        "clawback_reversal",
        "clawback_reversed",
    ):
        assert forbidden not in source
