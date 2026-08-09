from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.debt.enums import DebtOverdueSource
from app.debt.overdue_service import (
    OverdueBatchResult,
    OverdueTransitionOutcome,
    materialize_overdue_candidate,
)
from app.debt.overdue_targeting import (
    MAX_OVERDUE_BATCH_SIZE,
    OverdueCandidateLocator,
    OverdueDiscoveryBatch,
    discover_overdue_batch,
)
from app.debt.values import DebtId

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 9, 20, tzinfo=timezone(timedelta(hours=5)))


class _ScalarDiscoverySession:
    def __init__(self, rows: tuple[tuple[UUID, UUID, UUID, UUID], ...]) -> None:
        self.rows = rows
        self.executed = 0

    def execute(self, _statement):
        self.executed += 1
        return self.rows


class _ZeroPostedReader:
    def read_posted_total_uzs(self, *, debt_id: DebtId) -> Decimal:
        assert isinstance(debt_id, DebtId)
        return Decimal("0")


@pytest.mark.parametrize("batch_size", (1, MAX_OVERDUE_BATCH_SIZE))
def test_discovery_normalizes_one_now_and_returns_only_detached_scalars(
    batch_size: int,
) -> None:
    identifiers = tuple(UUID(int=value) for value in range(1, 5))
    session = _ScalarDiscoverySession((identifiers,))

    batch = discover_overdue_batch(session, now=NOW, batch_size=batch_size)  # type: ignore[arg-type]

    assert batch.normalized_now == datetime(2026, 8, 9, 15, tzinfo=UTC)
    assert batch.candidates == (
        OverdueCandidateLocator(
            debt_id=DebtId(identifiers[0]),
            shop_customer_id=identifiers[1],
            customer_id=identifiers[2],
            shop_id=identifiers[3],
        ),
    )
    assert session.executed == 1
    assert "candidate_count=1" in repr(batch)
    assert all(str(identifier) not in repr(batch) for identifier in identifiers)


@pytest.mark.parametrize("batch_size", (0, 101, True, 1.5))
def test_discovery_rejects_out_of_range_batch_before_query(batch_size: object) -> None:
    session = _ScalarDiscoverySession(())

    with pytest.raises(ValueError, match="between 1 and 100"):
        discover_overdue_batch(session, now=NOW, batch_size=batch_size)  # type: ignore[arg-type]

    assert session.executed == 0


def test_discovery_batch_requires_canonical_utc_not_an_equal_offset_instant() -> None:
    with pytest.raises(ValueError, match="normalized UTC"):
        OverdueDiscoveryBatch(normalized_now=NOW, candidates=())


def test_missing_target_is_safe_no_op_without_ledger_read(monkeypatch) -> None:
    candidate = OverdueCandidateLocator(
        debt_id=DebtId(UUID(int=1)),
        shop_customer_id=UUID(int=2),
        customer_id=UUID(int=3),
        shop_id=UUID(int=4),
    )
    monkeypatch.setattr(
        "app.debt.overdue_service.resolve_and_lock_overdue_candidate",
        lambda _session, *, candidate: None,
    )

    result = materialize_overdue_candidate(
        object(),  # type: ignore[arg-type]
        candidate=candidate,
        now=NOW,
        source=DebtOverdueSource.BATCH,
        posted_total_reader=_ZeroPostedReader(),
    )

    assert result.outcome is OverdueTransitionOutcome.NO_OP


@pytest.mark.parametrize(
    "values",
    (
        (1, 1, 1),
        (2, 1, 1),
        (-1, 0, -1),
        (True, 1, 0),
    ),
)
def test_batch_result_exposes_only_consistent_counts(
    values: tuple[object, object, object],
) -> None:
    considered, transitioned, no_op = values
    if values == (1, 1, 1) or values == (-1, 0, -1) or values == (True, 1, 0):
        with pytest.raises(ValueError):
            OverdueBatchResult(considered, transitioned, no_op)  # type: ignore[arg-type]
    else:
        result = OverdueBatchResult(considered, transitioned, no_op)  # type: ignore[arg-type]
        assert result.candidates_considered == 2


def test_overdue_sources_own_no_transaction_or_scheduling_surface() -> None:
    service = (PROJECT_ROOT / "app/debt/overdue_service.py").read_text(encoding="utf-8")
    targeting = (PROJECT_ROOT / "app/debt/overdue_targeting.py").read_text(
        encoding="utf-8"
    )
    discovery_body = targeting.split("def discover_overdue_candidates", 1)[1].split(
        "def discover_overdue_batch", 1
    )[0]

    assert "with_for_update" not in discovery_body
    assert "app.payment" not in service
    for forbidden in (
        "SKIP LOCKED",
        "job_run",
        "scheduler",
        "cron",
        "worker",
        "retry",
        ".commit(",
        ".rollback(",
        ".close(",
    ):
        assert forbidden not in service
