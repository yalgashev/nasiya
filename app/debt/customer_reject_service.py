"""Atomic own-customer rejection of one pending M13 Debt."""

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
    DebtRejectedAuditPayload,
)
from app.audit.repository import append_audit_event
from app.auth.error_codes import ErrorCode
from app.debt.contracts import DebtReason
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.customer_decision_targeting import (
    discover_own_customer_debt,
    lock_customer_debt_after_offer,
    lock_customer_debt_predecessors,
)
from app.debt.enums import DebtStatus
from app.debt.expiry_service import expire_locked_pending_debt_inline
from app.debt.repository import debt_aggregate_from_row, update_locked_debt
from app.debt.values import DebtId, DebtRevision

__all__ = (
    "CustomerDebtRejectOutcome",
    "RejectCustomerDebtCommand",
    "RejectCustomerDebtResult",
    "reject_own_customer_debt",
)


class CustomerDebtRejectOutcome(StrEnum):
    REJECTED = "rejected"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True, repr=False)
class RejectCustomerDebtCommand:
    debt_id: DebtId = field(repr=False)
    expected_revision: DebtRevision
    now: datetime
    raw_reason: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Reject debt identity is invalid")
        if not isinstance(self.expected_revision, DebtRevision):
            raise ValueError("Reject debt revision is invalid")
        if self.raw_reason is not None and not isinstance(self.raw_reason, str):
            raise ValueError("Reject debt reason source is invalid")
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("Reject debt time must be timezone-aware")
        object.__setattr__(self, "now", self.now.astimezone(UTC))

    def __repr__(self) -> str:
        return (
            "RejectCustomerDebtCommand(debt_id=<redacted>, "
            f"expected_revision={self.expected_revision.value!r}, "
            f"now={self.now!r}, raw_reason=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class RejectCustomerDebtResult:
    outcome: CustomerDebtRejectOutcome | None
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if (self.error is None) != isinstance(self.outcome, CustomerDebtRejectOutcome):
            raise ValueError("Reject customer debt result is invalid")
        allowed_errors = {
            ErrorCode.DEBT_UNAVAILABLE,
            ErrorCode.DEBT_NOT_PENDING,
            ErrorCode.DEBT_EXPIRED,
            ErrorCode.VALIDATION_ERROR,
        }
        if self.error is not None and self.error not in allowed_errors:
            raise ValueError("Reject customer debt error is invalid")


def reject_own_customer_debt(
    session: Session,
    *,
    authority: CustomerDebtAuthority | None,
    command: RejectCustomerDebtCommand,
) -> RejectCustomerDebtResult:
    """Reject without legal acceptance; suspended shops do not remove this right."""

    if not isinstance(command, RejectCustomerDebtCommand):
        raise TypeError("command must be a RejectCustomerDebtCommand")
    if authority is None:
        return _failure(ErrorCode.DEBT_UNAVAILABLE)
    if not isinstance(authority, CustomerDebtAuthority):
        raise TypeError("authority must be a CustomerDebtAuthority")
    try:
        reason = _optional_reason(command.raw_reason)
    except ValueError:
        return _failure(ErrorCode.VALIDATION_ERROR)
    candidate = discover_own_customer_debt(
        session,
        authority=authority,
        debt_id=command.debt_id,
    )
    predecessors = lock_customer_debt_predecessors(
        session,
        authority=authority,
        candidate=candidate,
        allow_suspended_shop=True,
    )
    if predecessors.error is not None:
        return _failure(predecessors.error)
    assert predecessors.locked is not None
    debt_result = lock_customer_debt_after_offer(
        session,
        locked=predecessors.locked,
        offer=None,
    )
    if debt_result.error is not None:
        return _failure(debt_result.error)
    assert debt_result.locked is not None
    locked_debt = debt_result.locked
    row = locked_debt.row
    if expire_locked_pending_debt_inline(
        session,
        locked_debt=locked_debt,
        now=command.now,
    ):
        return _failure(ErrorCode.DEBT_EXPIRED)
    if row.status == DebtStatus.REJECTED.value:
        exact = (
            row.revision == command.expected_revision.value + 1
            and row.rejection_reason == (None if reason is None else reason.value)
        )
        if exact:
            return RejectCustomerDebtResult(outcome=CustomerDebtRejectOutcome.REPLAY)
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    if row.status != DebtStatus.PENDING.value:
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    if row.revision != command.expected_revision.value:
        return _failure(ErrorCode.DEBT_NOT_PENDING)
    transitioned = debt_aggregate_from_row(row).reject(
        now=command.now,
        reason=reason,
    )
    update_locked_debt(session, row=row, debt=transitioned)
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_REJECTED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=authority.user_id,
            object_type=AuditObjectType.DEBT,
            object_id=row.id,
            occurred_at=command.now,
            candidate_metadata=DebtRejectedAuditPayload(
                reason_provided=reason is not None
            ).as_candidate_metadata(),
        ),
    )
    return RejectCustomerDebtResult(outcome=CustomerDebtRejectOutcome.REJECTED)


def _optional_reason(raw_reason: str | None) -> DebtReason | None:
    if raw_reason is None or not raw_reason.strip():
        return None
    return DebtReason(raw_reason)


def _failure(error: ErrorCode) -> RejectCustomerDebtResult:
    return RejectCustomerDebtResult(outcome=None, error=error)
