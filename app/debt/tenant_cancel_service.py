"""Atomic same-shop staff cancellation of one pending M13 Debt."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtCancelledAuditPayload,
)
from app.audit.repository import append_audit_event
from app.auth.error_codes import ErrorCode
from app.debt.contracts import DebtReason
from app.debt.dependencies import DetachedDebtActorAuthority
from app.debt.enums import DebtStatus
from app.debt.expiry_service import expire_locked_pending_debt_inline
from app.debt.repository import debt_aggregate_from_row, update_locked_debt
from app.debt.tenant_cancel_targeting import (
    discover_tenant_debt_for_cancel,
    lock_tenant_debt_for_cancel,
)
from app.debt.values import DebtId, DebtRevision

__all__ = (
    "CancelTenantDebtCommand",
    "CancelTenantDebtResult",
    "TenantDebtCancelOutcome",
    "cancel_tenant_debt",
)


class TenantDebtCancelOutcome(StrEnum):
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True, repr=False)
class CancelTenantDebtCommand:
    debt_id: DebtId = field(repr=False)
    expected_revision: DebtRevision
    now: datetime
    raw_reason: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Cancel debt identity is invalid")
        if not isinstance(self.expected_revision, DebtRevision):
            raise ValueError("Cancel debt revision is invalid")
        if not isinstance(self.raw_reason, str):
            raise ValueError("Cancel debt reason source is invalid")
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("Cancel debt time must be timezone-aware")
        object.__setattr__(self, "now", self.now.astimezone(UTC))

    def __repr__(self) -> str:
        return (
            "CancelTenantDebtCommand(debt_id=<redacted>, "
            f"expected_revision={self.expected_revision.value!r}, "
            f"now={self.now!r}, raw_reason=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class CancelTenantDebtResult:
    outcome: TenantDebtCancelOutcome | None
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(self.outcome, TenantDebtCancelOutcome):
            raise ValueError("Cancel tenant debt result is invalid")
        allowed_errors = {
            ErrorCode.FORBIDDEN,
            ErrorCode.SHOP_SUSPENDED,
            ErrorCode.DEBT_UNAVAILABLE,
            ErrorCode.DEBT_NOT_PENDING,
            ErrorCode.DEBT_EXPIRED,
            ErrorCode.REASON_REQUIRED,
            ErrorCode.VALIDATION_ERROR,
        }
        if self.error is not None and self.error not in allowed_errors:
            raise ValueError("Cancel tenant debt error is invalid")


def cancel_tenant_debt(
    session: Session,
    *,
    authority: DetachedDebtActorAuthority,
    command: CancelTenantDebtCommand,
) -> CancelTenantDebtResult:
    """Cancel one current-tenant pending debt without owning the Session."""

    if not isinstance(authority, DetachedDebtActorAuthority):
        raise TypeError("authority must be detached debt authority")
    if not isinstance(command, CancelTenantDebtCommand):
        raise TypeError("command must be a CancelTenantDebtCommand")
    if not authority.is_authenticated:
        return _failure(ErrorCode.FORBIDDEN)
    candidate = discover_tenant_debt_for_cancel(
        session,
        authority=authority,
        debt_id=command.debt_id,
    )
    locked_result = lock_tenant_debt_for_cancel(
        session,
        authority=authority,
        candidate=candidate,
    )
    if locked_result.error is not None:
        return _failure(locked_result.error)
    assert locked_result.locked is not None
    locked_debt = locked_result.locked
    row = locked_debt.row
    if expire_locked_pending_debt_inline(
        session,
        locked_debt=locked_debt,
        now=command.now,
    ):
        return _failure(ErrorCode.DEBT_EXPIRED)
    if row.status != DebtStatus.PENDING.value:
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    if row.revision != command.expected_revision.value:
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    try:
        reason = DebtReason(command.raw_reason)
    except ValueError:
        error = (
            ErrorCode.REASON_REQUIRED
            if not command.raw_reason.strip()
            else ErrorCode.VALIDATION_ERROR
        )
        return _failure(error)
    transitioned = debt_aggregate_from_row(row).cancel(
        now=command.now,
        reason=reason,
    )
    update_locked_debt(session, row=row, debt=transitioned)
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_CANCELLED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=locked_result.locked.actor_user_id,
            object_type=AuditObjectType.DEBT,
            object_id=row.id,
            occurred_at=command.now,
            candidate_metadata=DebtCancelledAuditPayload().as_candidate_metadata(),
        ),
    )
    return CancelTenantDebtResult(outcome=TenantDebtCancelOutcome.CANCELLED)


def _failure(error: ErrorCode) -> CancelTenantDebtResult:
    return CancelTenantDebtResult(outcome=None, error=error)
