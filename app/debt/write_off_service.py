"""Caller-owned atomic platform-admin write-off coordinator."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.audit.repository import append_debt_written_off_audit
from app.debt.commands import (
    WriteOffDebtCommand,
    WriteOffDebtFailure,
    WriteOffDebtMutationResult,
)
from app.debt.overdue_ports import LockedDebtPostedTotalReadPort
from app.debt.rating_ports import (
    LockedWrittenOffRatingAppendPort,
    WrittenOffRatingAppendOutcome,
    mark_locked_overdue_rating_source,
)
from app.debt.repository import (
    debt_aggregate_from_row,
    update_locked_debt,
    validate_locked_write_off_debt,
)
from app.debt.values import DebtId
from app.debt.write_off_core import (
    OverdueWriteOffSourceFacts,
    materialize_persisted_overdue_write_off,
)
from app.debt.write_off_targeting import (
    discover_admin_write_off_target,
    lock_admin_write_off_debt,
    lock_admin_write_off_predecessors,
    read_admin_completed_write_off,
)
from app.idempotency.contracts import (
    IdempotencyEndpoint,
    IdempotencyOutcome,
    IdempotencyResultType,
    canonical_idempotency_key_digest,
)
from app.idempotency.models import IdempotencyKey
from app.idempotency.repository import (
    completed_idempotency_result_from_row,
    find_completed_key,
    insert_or_resolve_key,
)
from app.shop_customer.values import ShopCustomerId

__all__ = ("WriteOffMutationRejected", "write_off_overdue_debt")

WriteOffClock = Callable[[], datetime]


class WriteOffMutationRejected(RuntimeError):
    __slots__ = ("failure",)

    def __init__(self, failure: WriteOffDebtFailure) -> None:
        if not isinstance(failure, WriteOffDebtFailure):
            raise ValueError("Write-off failure is invalid")
        self.failure = failure
        super().__init__(failure.value)

    def __repr__(self) -> str:
        return f"WriteOffMutationRejected(failure={self.failure.value!r})"


def write_off_overdue_debt(
    session: Session,
    *,
    command: WriteOffDebtCommand,
    rating_append_port: LockedWrittenOffRatingAppendPort,
    posted_total_reader: LockedDebtPostedTotalReadPort,
    clock: WriteOffClock | None = None,
) -> WriteOffDebtMutationResult:
    """Stage one Debt/-40/audit/key unit; the caller owns the transaction."""

    if not isinstance(command, WriteOffDebtCommand):
        raise TypeError("command must be a WriteOffDebtCommand")
    if not isinstance(rating_append_port, LockedWrittenOffRatingAppendPort):
        raise TypeError("rating_append_port must implement write-off append port")
    if not isinstance(posted_total_reader, LockedDebtPostedTotalReadPort):
        raise TypeError("posted_total_reader must implement locked Debt read port")
    server_clock = clock or _utc_now
    if not callable(server_clock):
        raise TypeError("clock must be callable")
    digest = canonical_idempotency_key_digest(command.idempotency_key)
    completed = find_completed_key(
        session,
        actor_user_id=command.actor_user_id,
        endpoint=IdempotencyEndpoint.ADMIN_DEBTS_WRITE_OFF,
        key_digest=digest,
    )
    if completed is not None:
        return _resolve_replay(session, row=completed, command=command)

    target = discover_admin_write_off_target(
        session, actor=command.actor, debt_id=command.debt_id
    )
    predecessors = lock_admin_write_off_predecessors(
        session, command=command, target=target
    )
    if predecessors is None:
        raise WriteOffMutationRejected(WriteOffDebtFailure.UNAVAILABLE)

    key = insert_or_resolve_key(
        session,
        actor_user_id=command.actor_user_id,
        endpoint=IdempotencyEndpoint.ADMIN_DEBTS_WRITE_OFF,
        key_digest=digest,
        request_hash=command.request_hash,
        result_object_id=command.debt_id.as_uuid(),
        now=None,
    )
    if key.outcome is IdempotencyOutcome.CONFLICT:
        raise WriteOffMutationRejected(WriteOffDebtFailure.IDEMPOTENCY_CONFLICT)
    if key.outcome is IdempotencyOutcome.REPLAY:
        assert key.row is not None
        return _resolve_replay(session, row=key.row, command=command)

    locked_token = lock_admin_write_off_debt(session, predecessors=predecessors)
    if locked_token is None:
        raise WriteOffMutationRejected(WriteOffDebtFailure.UNAVAILABLE)
    locked = validate_locked_write_off_debt(session, locked_token)
    debt = debt_aggregate_from_row(locked._row)
    if debt.revision != command.expected_revision:
        raise WriteOffMutationRejected(WriteOffDebtFailure.CHANGED)
    rating_source = mark_locked_overdue_rating_source(
        session,
        customer_id=predecessors.target.customer_id,
        shop_customer_id=ShopCustomerId(locked._row.shop_customer_id),
        debt_id=DebtId(locked._row.id),
    )
    source = _read_overdue_source_facts(
        session,
        debt=debt,
        rating_append_port=rating_append_port,
        rating_source=rating_source,
        posted_total_reader=posted_total_reader,
    )
    occurred_at = _aware_utc(server_clock())
    try:
        pending = materialize_persisted_overdue_write_off(
            debt=debt,
            expected_revision=command.expected_revision,
            actor_user_id=command.actor_user_id,
            reason=command.reason,
            occurred_at=occurred_at,
            event_id=uuid4(),
            source=source,
        )
    except ValueError as exc:
        raise WriteOffMutationRejected(WriteOffDebtFailure.NOT_WRITABLE_OFF) from exc

    update_locked_debt(session, row=locked._row, debt=pending.debt)
    rating_outcome = rating_append_port.append_pending_written_off(
        session,
        locked_source=rating_source,
        effect=pending.rating_effect,
    )
    if rating_outcome is not WrittenOffRatingAppendOutcome.APPENDED:
        raise RuntimeError("Write-off rating source is inconsistent")
    assert pending.debt.written_off_at is not None
    assert pending.debt.written_off_revision is not None
    append_debt_written_off_audit(
        session,
        debt_id=pending.debt.id.as_uuid(),
        actor_user_id=command.actor_user_id,
        occurred_at=pending.debt.written_off_at,
        written_off_at=pending.debt.written_off_at,
        current_revision=pending.debt.written_off_revision,
        payload=pending.audit_payload,
    )
    return WriteOffDebtMutationResult(
        outcome=IdempotencyOutcome.NEW,
        debt_id=command.debt_id,
    )


def _resolve_replay(
    session: Session,
    *,
    row: IdempotencyKey,
    command: WriteOffDebtCommand,
) -> WriteOffDebtMutationResult:
    if not hmac.compare_digest(row.request_hash, command.request_hash.value):
        raise WriteOffMutationRejected(WriteOffDebtFailure.IDEMPOTENCY_CONFLICT)
    completed = completed_idempotency_result_from_row(row)
    if (
        completed.result_type is not IdempotencyResultType.DEBT
        or completed.debt_id != command.debt_id
        or read_admin_completed_write_off(
            session, actor=command.actor, debt_id=completed.debt_id
        )
        is None
    ):
        raise WriteOffMutationRejected(WriteOffDebtFailure.UNAVAILABLE)
    return WriteOffDebtMutationResult(
        outcome=IdempotencyOutcome.REPLAY,
        debt_id=completed.debt_id,
    )


def _read_overdue_source_facts(
    session: Session,
    *,
    debt: object,
    rating_append_port: LockedWrittenOffRatingAppendPort,
    rating_source,
    posted_total_reader: LockedDebtPostedTotalReadPort,
) -> OverdueWriteOffSourceFacts:
    from app.debt.contracts import DebtAggregate

    if not isinstance(debt, DebtAggregate) or debt.overdue_at is None:
        return OverdueWriteOffSourceFacts(
            posted_total_uzs=posted_total_reader.read_posted_total_uzs(debt_id=debt.id),
            has_unique_overdue_rating=False,
            has_exact_overdue_audit_pair=False,
        )
    assert debt.overdue_revision is not None
    rating_ok = rating_append_port.has_coherent_overdue_source(
        session,
        locked_source=rating_source,
        overdue_at=debt.overdue_at,
        overdue_revision=debt.overdue_revision,
    )
    audits = tuple(
        session.scalars(
            select(AuditLog).where(
                AuditLog.object_type == "debt",
                AuditLog.object_id == debt.id.as_uuid(),
                AuditLog.event_type.in_(("debt.overdue", "debt.clawback_applied")),
            )
        )
    )
    audit_types = {row.event_type for row in audits}
    audit_ok = (
        len(audits) == 2
        and audit_types == {"debt.overdue", "debt.clawback_applied"}
        and all(
            row.actor_kind == "SYSTEM"
            and row.actor_user_id is None
            and row.occurred_at == debt.overdue_at
            and row.payload.get("overdue_revision") == debt.overdue_revision.value
            for row in audits
        )
    )
    return OverdueWriteOffSourceFacts(
        posted_total_uzs=posted_total_reader.read_posted_total_uzs(debt_id=debt.id),
        has_unique_overdue_rating=rating_ok,
        has_exact_overdue_audit_pair=audit_ok,
    )


def _aware_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Write-off clock must return aware time")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
