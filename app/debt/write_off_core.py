"""Pure persisted-overdue write-off transition."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.audit.contracts import DebtWrittenOffAuditPayload
from app.debt.contracts import DebtAggregate, DebtLifecycleError, WriteOffReason
from app.debt.enums import DebtStatus
from app.debt.rating_ports import PendingWrittenOffRatingEffect
from app.debt.values import DebtRevision, UserId

__all__ = (
    "OverdueWriteOffSourceFacts",
    "PendingWrittenOffTransition",
    "materialize_persisted_overdue_write_off",
)


@dataclass(frozen=True, slots=True, repr=False)
class OverdueWriteOffSourceFacts:
    posted_total_uzs: Decimal = field(repr=False)
    has_unique_overdue_rating: bool
    has_exact_overdue_audit_pair: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.posted_total_uzs, Decimal)
            or not self.posted_total_uzs.is_finite()
            or self.posted_total_uzs.as_tuple().exponent != 0
            or self.posted_total_uzs < 0
        ):
            raise ValueError("Write-off source ledger is invalid")
        if not isinstance(self.has_unique_overdue_rating, bool) or not isinstance(
            self.has_exact_overdue_audit_pair, bool
        ):
            raise ValueError("Write-off source evidence is invalid")

    def __repr__(self) -> str:
        return "OverdueWriteOffSourceFacts(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PendingWrittenOffTransition:
    debt: DebtAggregate = field(repr=False)
    rating_effect: PendingWrittenOffRatingEffect = field(repr=False)
    audit_payload: DebtWrittenOffAuditPayload

    def __repr__(self) -> str:
        return "PendingWrittenOffTransition(<redacted>)"


def materialize_persisted_overdue_write_off(
    *,
    debt: DebtAggregate,
    expected_revision: DebtRevision,
    actor_user_id: UUID,
    reason: WriteOffReason,
    occurred_at: datetime,
    event_id: UUID,
    source: OverdueWriteOffSourceFacts,
) -> PendingWrittenOffTransition:
    if not isinstance(debt, DebtAggregate):
        raise TypeError("debt must be a DebtAggregate")
    normalized = _aware_utc(occurred_at)
    if (
        debt.status is not DebtStatus.OVERDUE
        or debt.overdue_at is None
        or debt.overdue_revision is None
        or not source.has_unique_overdue_rating
        or not source.has_exact_overdue_audit_pair
    ):
        raise DebtLifecycleError("Debt write-off source is incoherent")
    updated = debt.mark_written_off(
        now=normalized,
        actor_user_id=UserId(actor_user_id),
        reason=reason,
        posted_total_uzs=source.posted_total_uzs,
        expected_revision=expected_revision,
    )
    assert updated.written_off_at is not None
    assert updated.written_off_revision is not None
    return PendingWrittenOffTransition(
        debt=updated,
        rating_effect=PendingWrittenOffRatingEffect(
            event_id=event_id,
            debt_id=updated.id,
            shop_customer_id=updated.shop_customer_id,
            written_off_at=updated.written_off_at,
            source_revision=updated.written_off_revision,
        ),
        audit_payload=DebtWrittenOffAuditPayload(
            written_off_revision=updated.written_off_revision
        ),
    )


def _aware_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Write-off time must be aware")
    return value.astimezone(UTC)
