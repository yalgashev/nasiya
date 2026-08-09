"""Atomic M15 overdue transition and bounded route-agnostic batch service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtClawbackAppliedAuditPayload,
    DebtOverdueAuditPayload,
)
from app.audit.repository import append_audit_event
from app.debt.business_time import is_effectively_overdue, tashkent_business_date
from app.debt.enums import DebtOverdueSource, DebtStatus
from app.debt.overdue_ports import LockedDebtPostedTotalReadPort
from app.debt.overdue_targeting import (
    OverdueCandidateLocator,
    discover_overdue_batch,
    locked_overdue_debt_row,
    resolve_and_lock_overdue_candidate,
)
from app.debt.repository import (
    LockedDebtTransitionScope,
    debt_aggregate_from_row,
    mark_locked_debt_transition_scope,
    update_locked_debt,
    validate_locked_debt_transition_scope,
)
from app.debt.values import ClawbackIncreaseUZS, DebtId

__all__ = (
    "OverdueBatchResult",
    "OverdueBatchTransitionError",
    "OverdueTransitionOutcome",
    "OverdueTransitionResult",
    "materialize_locked_overdue_debt",
    "materialize_overdue_candidate",
    "materialize_overdue_debts",
)

PostedTotalReaderFactory = Callable[[Session], LockedDebtPostedTotalReadPort]


class OverdueTransitionOutcome(StrEnum):
    TRANSITIONED = "transitioned"
    NO_OP = "no_op"


@dataclass(frozen=True, slots=True)
class OverdueTransitionResult:
    outcome: OverdueTransitionOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, OverdueTransitionOutcome):
            raise ValueError("Overdue transition outcome is invalid")


@dataclass(frozen=True, slots=True)
class OverdueBatchResult:
    candidates_considered: int
    transitioned_count: int
    no_op_count: int

    def __post_init__(self) -> None:
        values = (
            self.candidates_considered,
            self.transitioned_count,
            self.no_op_count,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in values
        ):
            raise ValueError("Overdue batch counts are invalid")
        if self.transitioned_count + self.no_op_count != self.candidates_considered:
            raise ValueError("Overdue batch counts are inconsistent")


class OverdueBatchTransitionError(RuntimeError):
    """Identifier-free failure from one caller-owned candidate transaction."""


def materialize_overdue_candidate(
    session: Session,
    *,
    candidate: OverdueCandidateLocator,
    now: datetime,
    source: DebtOverdueSource,
    posted_total_reader: LockedDebtPostedTotalReadPort,
) -> OverdueTransitionResult:
    """Forward-lock/recheck one detached candidate and transition if still lawful."""

    if not isinstance(candidate, OverdueCandidateLocator):
        raise TypeError("candidate must be an OverdueCandidateLocator")
    locked = resolve_and_lock_overdue_candidate(session, candidate=candidate)
    if locked is None:
        return OverdueTransitionResult(OverdueTransitionOutcome.NO_OP)
    locked_row = locked_overdue_debt_row(session, locked)
    return materialize_locked_overdue_debt(
        session,
        locked_debt=mark_locked_debt_transition_scope(session, locked_row=locked_row),
        now=now,
        source=source,
        posted_total_reader=posted_total_reader,
    )


def materialize_locked_overdue_debt(
    session: Session,
    *,
    locked_debt: LockedDebtTransitionScope,
    now: datetime,
    source: DebtOverdueSource,
    posted_total_reader: LockedDebtPostedTotalReadPort,
) -> OverdueTransitionResult:
    """Apply one Debt revision and one SYSTEM audit pair without owning Session."""

    normalized_now = _normalize_now(now)
    if not isinstance(source, DebtOverdueSource):
        raise TypeError("source must be a DebtOverdueSource")
    if not isinstance(posted_total_reader, LockedDebtPostedTotalReadPort):
        raise TypeError(
            "posted_total_reader must implement LockedDebtPostedTotalReadPort"
        )
    row = validate_locked_debt_transition_scope(session, locked_debt)._row
    if row.status != DebtStatus.ACTIVE.value:
        return OverdueTransitionResult(OverdueTransitionOutcome.NO_OP)
    if not is_effectively_overdue(
        status=DebtStatus.ACTIVE,
        due_date=row.due_date,
        server_now=normalized_now,
    ):
        return OverdueTransitionResult(OverdueTransitionOutcome.NO_OP)

    posted_total = posted_total_reader.read_posted_total_uzs(debt_id=DebtId(row.id))
    _require_coherent_unpaid_active_ledger(
        posted_total=posted_total,
        discounted_amount=Decimal(row.discounted_amount_uzs),
    )
    aggregate = debt_aggregate_from_row(row)
    transitioned = aggregate.mark_overdue(
        now=normalized_now,
        source=source,
        posted_total_uzs=posted_total,
    )
    increase = ClawbackIncreaseUZS(
        aggregate.original_amount.value - aggregate.discounted_amount.value
    )
    update_locked_debt(session, row=row, debt=transitioned)
    business_date = tashkent_business_date(normalized_now)
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_OVERDUE,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.DEBT,
            object_id=row.id,
            occurred_at=normalized_now,
            candidate_metadata=DebtOverdueAuditPayload(
                source=source,
                overdue_revision=transitioned.overdue_revision,
                business_date=business_date,
            ).as_candidate_metadata(),
        ),
    )
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_CLAWBACK_APPLIED,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.DEBT,
            object_id=row.id,
            occurred_at=normalized_now,
            candidate_metadata=DebtClawbackAppliedAuditPayload(
                source=source,
                balance_increase_uzs=increase,
                overdue_revision=transitioned.overdue_revision,
            ).as_candidate_metadata(),
        ),
    )
    return OverdueTransitionResult(OverdueTransitionOutcome.TRANSITIONED)


def materialize_overdue_debts(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
    batch_size: int,
    posted_total_reader_factory: PostedTotalReaderFactory,
) -> OverdueBatchResult:
    """Own discovery plus one independent transaction per detached candidate."""

    if not callable(posted_total_reader_factory):
        raise TypeError("posted_total_reader_factory must be callable")
    with session_factory.begin() as discovery_session:
        batch = discover_overdue_batch(
            discovery_session,
            now=now,
            batch_size=batch_size,
        )

    transitioned_count = 0
    no_op_count = 0
    for candidate in batch.candidates:
        try:
            with session_factory.begin() as transition_session:
                result = materialize_overdue_candidate(
                    transition_session,
                    candidate=candidate,
                    now=batch.normalized_now,
                    source=DebtOverdueSource.BATCH,
                    posted_total_reader=posted_total_reader_factory(transition_session),
                )
        except Exception:
            raise OverdueBatchTransitionError(
                "Overdue batch candidate transaction failed"
            ) from None
        transitioned_count += int(
            result.outcome is OverdueTransitionOutcome.TRANSITIONED
        )
        no_op_count += int(result.outcome is OverdueTransitionOutcome.NO_OP)
    return OverdueBatchResult(
        candidates_considered=len(batch.candidates),
        transitioned_count=transitioned_count,
        no_op_count=no_op_count,
    )


def _require_coherent_unpaid_active_ledger(
    *, posted_total: Decimal, discounted_amount: Decimal
) -> None:
    if (
        not isinstance(posted_total, Decimal)
        or not posted_total.is_finite()
        or posted_total.as_tuple().exponent != 0
        or posted_total < Decimal("0")
        or posted_total >= discounted_amount
    ):
        raise ValueError("Active debt payment ledger is incoherent")


def _normalize_now(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Overdue transition time must be timezone-aware")
    return value.astimezone(UTC)
