"""Atomic M15 overdue transition and bounded route-agnostic batch service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

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
    locked_overdue_rating_source,
    resolve_and_lock_overdue_candidate,
)
from app.debt.rating_ports import (
    LockedOverdueRatingAppendPort,
    OverdueRatingAppendOutcome,
    PendingOverdueRatingEffect,
)
from app.debt.repository import (
    LockedDebtTransitionScope,
    debt_aggregate_from_row,
    mark_locked_debt_transition_scope,
    update_locked_debt,
    validate_locked_debt_transition_scope,
)
from app.debt.values import ClawbackIncreaseUZS, DebtId, DebtRevision
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "OverdueBatchResult",
    "OverdueBatchTransitionError",
    "OverdueTransitionOutcome",
    "OverdueTransitionResult",
    "PendingOverdueTransitionEffect",
    "append_pending_overdue_audits",
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
    effect: PendingOverdueTransitionEffect | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, OverdueTransitionOutcome):
            raise ValueError("Overdue transition outcome is invalid")
        if self.outcome is OverdueTransitionOutcome.TRANSITIONED:
            if not isinstance(self.effect, PendingOverdueTransitionEffect):
                raise ValueError("Overdue transition requires a pending effect")
        elif self.effect is not None:
            raise ValueError("No-op overdue transition cannot carry an effect")


@dataclass(frozen=True, slots=True, repr=False)
class PendingOverdueTransitionEffect:
    """Debt-owned source fact and audit data awaiting caller orchestration."""

    rating_effect: PendingOverdueRatingEffect = field(repr=False)
    source: DebtOverdueSource
    overdue_revision: DebtRevision
    balance_increase_uzs: ClawbackIncreaseUZS = field(repr=False)
    business_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.rating_effect, PendingOverdueRatingEffect):
            raise ValueError("Pending overdue rating effect is invalid")
        if not isinstance(self.source, DebtOverdueSource):
            raise ValueError("Pending overdue source is invalid")
        if not isinstance(self.overdue_revision, DebtRevision):
            raise ValueError("Pending overdue revision is invalid")
        if not isinstance(self.balance_increase_uzs, ClawbackIncreaseUZS):
            raise ValueError("Pending overdue increase is invalid")
        if not isinstance(self.business_date, date) or isinstance(
            self.business_date, datetime
        ):
            raise ValueError("Pending overdue business date is invalid")

    def __repr__(self) -> str:
        return "PendingOverdueTransitionEffect(<redacted>)"


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
    rating_append_port: LockedOverdueRatingAppendPort,
) -> OverdueTransitionResult:
    """Forward-lock/recheck one detached candidate and transition if still lawful."""

    if not isinstance(candidate, OverdueCandidateLocator):
        raise TypeError("candidate must be an OverdueCandidateLocator")
    locked = resolve_and_lock_overdue_candidate(session, candidate=candidate)
    if locked is None:
        return OverdueTransitionResult(OverdueTransitionOutcome.NO_OP)
    if not isinstance(rating_append_port, LockedOverdueRatingAppendPort):
        raise TypeError("rating_append_port must implement its structural port")
    locked_row = locked_overdue_debt_row(session, locked)
    rating_source = locked_overdue_rating_source(session, locked)
    result = materialize_locked_overdue_debt(
        session,
        locked_debt=mark_locked_debt_transition_scope(session, locked_row=locked_row),
        now=now,
        source=source,
        posted_total_reader=posted_total_reader,
    )
    if result.effect is not None:
        rating_outcome = rating_append_port.append_pending_overdue(
            session,
            locked_source=rating_source,
            effect=result.effect.rating_effect,
        )
        if rating_outcome is not OverdueRatingAppendOutcome.APPENDED:
            raise RuntimeError("Overdue rating source is inconsistent")
        append_pending_overdue_audits(session, effect=result.effect)
    return result


def materialize_locked_overdue_debt(
    session: Session,
    *,
    locked_debt: LockedDebtTransitionScope,
    now: datetime,
    source: DebtOverdueSource,
    posted_total_reader: LockedDebtPostedTotalReadPort,
) -> OverdueTransitionResult:
    """Apply only the locked Debt transition and return its pending source fact."""

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
    effect = PendingOverdueTransitionEffect(
        rating_effect=PendingOverdueRatingEffect(
            event_id=uuid4(),
            debt_id=DebtId(row.id),
            shop_customer_id=ShopCustomerId(row.shop_customer_id),
            overdue_at=normalized_now,
            source_revision=transitioned.overdue_revision,
        ),
        source=source,
        overdue_revision=transitioned.overdue_revision,
        balance_increase_uzs=increase,
        business_date=business_date,
    )
    return OverdueTransitionResult(
        OverdueTransitionOutcome.TRANSITIONED,
        effect=effect,
    )


def append_pending_overdue_audits(
    session: Session, *, effect: PendingOverdueTransitionEffect
) -> None:
    """Append the canonical SYSTEM audit pair after its rating event."""

    if not isinstance(effect, PendingOverdueTransitionEffect):
        raise TypeError("effect must be a PendingOverdueTransitionEffect")
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_OVERDUE,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.DEBT,
            object_id=effect.rating_effect.debt_id.as_uuid(),
            occurred_at=effect.rating_effect.overdue_at,
            candidate_metadata=DebtOverdueAuditPayload(
                source=effect.source,
                overdue_revision=effect.overdue_revision,
                business_date=effect.business_date,
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
            object_id=effect.rating_effect.debt_id.as_uuid(),
            occurred_at=effect.rating_effect.overdue_at,
            candidate_metadata=DebtClawbackAppliedAuditPayload(
                source=effect.source,
                balance_increase_uzs=effect.balance_increase_uzs,
                overdue_revision=effect.overdue_revision,
            ).as_candidate_metadata(),
        ),
    )


def materialize_overdue_debts(
    session_factory: sessionmaker[Session],
    *,
    now: datetime,
    batch_size: int,
    posted_total_reader_factory: PostedTotalReaderFactory,
    rating_append_port: LockedOverdueRatingAppendPort,
) -> OverdueBatchResult:
    """Own discovery plus one independent transaction per detached candidate."""

    if not callable(posted_total_reader_factory):
        raise TypeError("posted_total_reader_factory must be callable")
    if not isinstance(rating_append_port, LockedOverdueRatingAppendPort):
        raise TypeError("rating_append_port must implement its structural port")
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
                    rating_append_port=rating_append_port,
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
