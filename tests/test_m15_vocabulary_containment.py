from pathlib import Path

from app.audit.contracts import AuditEventType
from app.debt.enums import M15_PERSISTED_STATUSES, DebtStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_m15_persists_only_overdue_not_future_lifecycle_statuses() -> None:
    assert DebtStatus.OVERDUE in M15_PERSISTED_STATUSES
    assert DebtStatus.WRITTEN_OFF not in M15_PERSISTED_STATUSES
    assert DebtStatus.WRITTEN_OFF_SETTLED not in M15_PERSISTED_STATUSES

    debt_model_source = (PROJECT_ROOT / "app/debt/models.py").read_text(
        encoding="utf-8"
    )
    assert "DebtStatus.WRITTEN_OFF" not in debt_model_source
    assert "DebtStatus.WRITTEN_OFF_SETTLED" not in debt_model_source


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
