"""Shared exact-boundary and bounded batch expiry services for M13 Debt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtExpiredAuditPayload,
)
from app.audit.repository import append_audit_event
from app.debt.business_time import is_pending_expired
from app.debt.customer_decision_targeting import (
    LockedCustomerDebt,
    _validate_locked_customer_debt,
)
from app.debt.enums import DebtExpirySource, DebtStatus
from app.debt.expiry_targeting import (
    DebtExpiryCandidate,
    _validate_locked_expiry_debt,
    discover_debt_expiry_candidates,
    lock_debt_for_expiry,
)
from app.debt.models import Debt
from app.debt.repository import debt_aggregate_from_row, update_locked_debt
from app.debt.tenant_cancel_targeting import (
    LockedTenantDebtForCancel,
    _validate_locked_tenant_debt,
)

__all__ = (
    "DebtExpiryBatchResult",
    "expire_locked_pending_debt_inline",
    "expire_pending_debt_candidate",
    "expire_pending_debts",
)


@dataclass(frozen=True, slots=True)
class DebtExpiryBatchResult:
    candidates_considered: int
    expired_count: int

    def __post_init__(self) -> None:
        if not 0 <= self.expired_count <= self.candidates_considered:
            raise ValueError("Debt expiry batch result is invalid")


def expire_locked_pending_debt_inline(
    session: Session,
    *,
    locked_debt: LockedCustomerDebt | LockedTenantDebtForCancel,
    now: datetime,
) -> bool:
    """Expire one locked pending Debt; return true for expired/already-expired."""

    if isinstance(locked_debt, LockedCustomerDebt):
        row = _validate_locked_customer_debt(session, locked_debt).row
    elif isinstance(locked_debt, LockedTenantDebtForCancel):
        row = _validate_locked_tenant_debt(session, locked_debt).row
    else:
        raise TypeError("locked debt must come from a decision resolver")
    return _expire_locked_row(
        session,
        row=row,
        now=now,
        source=DebtExpirySource.INLINE,
    )


def expire_pending_debt_candidate(
    session: Session, *, candidate: DebtExpiryCandidate, now: datetime
) -> bool:
    """Lock/recheck one candidate in a caller-owned bounded transaction."""

    locked = lock_debt_for_expiry(session, candidate=candidate)
    if locked is None:
        return False
    row = _validate_locked_expiry_debt(session, locked).row
    return _expire_locked_row(
        session,
        row=row,
        now=now,
        source=DebtExpirySource.BATCH,
    )


def expire_pending_debts(
    session_factory: sessionmaker[Session], *, now: datetime, batch_size: int
) -> DebtExpiryBatchResult:
    """Own one discovery transaction and one bounded transaction per candidate."""

    normalized_now = _normalized_now(now)
    with session_factory.begin() as discovery_session:
        candidates = discover_debt_expiry_candidates(
            discovery_session,
            now=normalized_now,
            batch_size=batch_size,
        )
    expired_count = 0
    for candidate in candidates:
        with session_factory.begin() as transition_session:
            expired_count += int(
                expire_pending_debt_candidate(
                    transition_session,
                    candidate=candidate,
                    now=normalized_now,
                )
            )
    return DebtExpiryBatchResult(
        candidates_considered=len(candidates),
        expired_count=expired_count,
    )


def _expire_locked_row(
    session: Session,
    *,
    row: Debt,
    now: datetime,
    source: DebtExpirySource,
) -> bool:
    normalized_now = _normalized_now(now)
    if row.status == DebtStatus.EXPIRED.value:
        return True
    if row.status != DebtStatus.PENDING.value or not is_pending_expired(
        now=normalized_now,
        pending_expires_at=row.pending_expires_at,
    ):
        return False
    transitioned = debt_aggregate_from_row(row).expire(
        now=normalized_now,
        source=source,
    )
    update_locked_debt(session, row=row, debt=transitioned)
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_EXPIRED,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.DEBT,
            object_id=row.id,
            occurred_at=normalized_now,
            candidate_metadata=DebtExpiredAuditPayload(
                source=source
            ).as_candidate_metadata(),
        ),
    )
    return True


def _normalized_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Debt expiry time must be timezone-aware")
    return now.astimezone(UTC)
