"""Immutable pending-debt aggregate and lifecycle contracts."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.debt.business_time import (
    is_effectively_overdue,
    is_payment_due_date_payable,
    is_pending_expired,
    normalize_payment_created_at,
    pending_expires_at,
    validate_due_date_not_before_expiry_business_date,
)
from app.debt.enums import (
    M17_PERSISTED_STATUSES,
    DebtExpirySource,
    DebtOverdueSource,
    DebtPaymentFailure,
    DebtStatus,
)
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
    UserId,
)

__all__ = (
    "DebtAggregate",
    "DebtLifecycleError",
    "DebtPaymentVoidTransition",
    "DebtPaymentVoidTransitionError",
    "DebtPaymentTransitionError",
    "DebtProjection",
    "DebtReason",
    "PendingPaymentVoidOverdueEffect",
    "reopen_debt_after_payment_void",
    "WriteOffReason",
)


class DebtLifecycleError(ValueError):
    """Raised when an immutable pending-debt transition is unavailable."""


class DebtPaymentTransitionError(DebtLifecycleError):
    """Typed, identifier-free failure from the active payment transition."""

    def __init__(self, failure: DebtPaymentFailure) -> None:
        if not isinstance(failure, DebtPaymentFailure):
            raise ValueError("Debt payment failure is invalid")
        self.failure = failure
        super().__init__(failure.value)

    def __repr__(self) -> str:
        return f"DebtPaymentTransitionError(failure={self.failure!r})"


class DebtPaymentVoidTransitionError(DebtLifecycleError):
    """Identifier-free denial for an incoherent Payment-void Debt transition."""


class WriteOffReason(StrEnum):
    COLLECTION_EXHAUSTED = "collection_exhausted"
    CUSTOMER_UNREACHABLE = "customer_unreachable"
    INSOLVENCY_OR_DECEASED = "insolvency_or_deceased"
    LEGAL_OR_COMPLIANCE = "legal_or_compliance"
    FRAUD_OR_ABUSE = "fraud_or_abuse"


@dataclass(frozen=True, slots=True, repr=False)
class DebtReason:
    value: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise ValueError("Debt reason is invalid")
        normalized = self.value.strip()
        if not 1 <= len(normalized) <= 500:
            raise ValueError("Debt reason must be 1 to 500 characters")
        if any(unicodedata.category(char) == "Cc" for char in normalized):
            raise ValueError("Debt reason contains a control character")
        object.__setattr__(self, "value", normalized)

    def __repr__(self) -> str:
        return "DebtReason(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True)
class DebtProjection:
    """Identifier- and reason-free state for a trusted adapter."""

    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: date
    pending_expires_at: datetime
    status: DebtStatus
    revision: DebtRevision
    created_at: datetime
    updated_at: datetime
    accepted_at: datetime | None
    rejected_at: datetime | None
    cancelled_at: datetime | None
    expired_at: datetime | None
    paid_at: datetime | None
    overdue_at: datetime | None = None
    overdue_revision: DebtRevision | None = None
    written_off_at: datetime | None = None
    written_off_settled_at: datetime | None = None


@dataclass(frozen=True, slots=True, repr=False)
class DebtAggregate:
    """Trusted debt state; money and due date never change after creation."""

    id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    created_by_user_id: UserId = field(repr=False)
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: date
    pending_expires_at: datetime
    status: DebtStatus
    revision: DebtRevision
    created_at: datetime
    updated_at: datetime
    accepted_at: datetime | None = None
    rejected_at: datetime | None = None
    cancelled_at: datetime | None = None
    expired_at: datetime | None = None
    paid_at: datetime | None = None
    overdue_at: datetime | None = None
    overdue_revision: DebtRevision | None = None
    rejection_reason: DebtReason | None = field(default=None, repr=False)
    cancellation_reason: DebtReason | None = field(default=None, repr=False)
    written_off_at: datetime | None = None
    written_off_revision: DebtRevision | None = None
    written_off_reason: WriteOffReason | None = field(default=None, repr=False)
    written_off_actor_user_id: UserId | None = field(default=None, repr=False)
    written_off_settled_at: datetime | None = None
    written_off_settled_revision: DebtRevision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, DebtId):
            raise ValueError("Debt ID is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Debt shop customer ID is invalid")
        _require_uuid(self.created_by_user_id, field_name="creator")
        if not isinstance(self.original_amount, OriginalAmountUZS):
            raise ValueError("Debt original amount is invalid")
        if not isinstance(self.discount_basis_points, DiscountBasisPoints):
            raise ValueError("Debt discount basis points are invalid")
        if not isinstance(self.discounted_amount, DiscountedAmountUZS):
            raise ValueError("Debt discounted amount is invalid")
        if self.discounted_amount.value > self.original_amount.value:
            raise ValueError("Debt discounted amount cannot exceed original amount")
        if not isinstance(self.due_date, date) or isinstance(self.due_date, datetime):
            raise ValueError("Debt due date is invalid")
        if (
            not isinstance(self.status, DebtStatus)
            or self.status not in M17_PERSISTED_STATUSES
        ):
            raise ValueError("Debt status is invalid")
        if not isinstance(self.revision, DebtRevision):
            raise ValueError("Debt revision is invalid")

        created_at = _as_utc(self.created_at, field_name="created_at")
        updated_at = _as_utc(self.updated_at, field_name="updated_at")
        expiry = _as_utc(self.pending_expires_at, field_name="pending_expires_at")
        if updated_at < created_at:
            raise ValueError("Debt update time is invalid")
        if expiry != pending_expires_at(created_at):
            raise ValueError("Debt pending expiry must be exactly 72 hours")
        validate_due_date_not_before_expiry_business_date(
            due_date=self.due_date, pending_expiry=expiry
        )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "pending_expires_at", expiry)
        for field_name in (
            "accepted_at",
            "rejected_at",
            "cancelled_at",
            "expired_at",
            "paid_at",
            "overdue_at",
            "written_off_at",
            "written_off_settled_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                normalized = _as_utc(value, field_name=field_name)
                if normalized < created_at or normalized > updated_at:
                    raise ValueError("Debt lifecycle timestamp is invalid")
                object.__setattr__(self, field_name, normalized)
        if (self.overdue_at is None) != (self.overdue_revision is None):
            raise ValueError("Debt overdue metadata must be present together")
        if self.overdue_revision is not None:
            if not isinstance(self.overdue_revision, DebtRevision):
                raise ValueError("Debt overdue revision is invalid")
            if self.overdue_revision.value > self.revision.value:
                raise ValueError("Debt overdue revision cannot exceed current revision")
        self._validate_write_off_metadata()
        self._validate_lifecycle_timestamps()

    @classmethod
    def create_pending(
        cls,
        *,
        debt_id: DebtId,
        shop_customer_id: ShopCustomerId,
        created_by_user_id: UserId,
        original_amount: OriginalAmountUZS,
        discount_basis_points: DiscountBasisPoints,
        discounted_amount: DiscountedAmountUZS,
        due_date: date,
        created_at: datetime,
    ) -> DebtAggregate:
        created_at = _as_utc(created_at, field_name="created_at")
        return cls(
            id=debt_id,
            shop_customer_id=shop_customer_id,
            created_by_user_id=created_by_user_id,
            original_amount=original_amount,
            discount_basis_points=discount_basis_points,
            discounted_amount=discounted_amount,
            due_date=due_date,
            pending_expires_at=pending_expires_at(created_at),
            status=DebtStatus.PENDING,
            revision=DebtRevision(1),
            created_at=created_at,
            updated_at=created_at,
        )

    def accept(self, *, now: datetime) -> DebtAggregate:
        self._require_unexpired_pending(now)
        transition_at = _transition_time(now)
        return replace(
            self,
            status=DebtStatus.ACTIVE,
            revision=DebtRevision(self.revision.value + 1),
            updated_at=transition_at,
            accepted_at=transition_at,
        )

    def reject(
        self, *, now: datetime, reason: DebtReason | None = None
    ) -> DebtAggregate:
        self._require_unexpired_pending(now)
        if reason is not None and not isinstance(reason, DebtReason):
            raise ValueError("Debt rejection reason is invalid")
        transition_at = _transition_time(now)
        return replace(
            self,
            status=DebtStatus.REJECTED,
            revision=DebtRevision(self.revision.value + 1),
            updated_at=transition_at,
            rejected_at=transition_at,
            rejection_reason=reason,
        )

    def cancel(self, *, now: datetime, reason: DebtReason) -> DebtAggregate:
        self._require_unexpired_pending(now)
        if not isinstance(reason, DebtReason):
            raise ValueError("Debt cancellation reason is required")
        transition_at = _transition_time(now)
        return replace(
            self,
            status=DebtStatus.CANCELLED,
            revision=DebtRevision(self.revision.value + 1),
            updated_at=transition_at,
            cancelled_at=transition_at,
            cancellation_reason=reason,
        )

    def expire(self, *, now: datetime, source: DebtExpirySource) -> DebtAggregate:
        self._require_pending()
        transition_at = _transition_time(now)
        if transition_at < self.pending_expires_at:
            raise DebtLifecycleError("Pending debt has not expired")
        if not isinstance(source, DebtExpirySource):
            raise ValueError("Debt expiry source is invalid")
        return replace(
            self,
            status=DebtStatus.EXPIRED,
            revision=DebtRevision(self.revision.value + 1),
            updated_at=transition_at,
            expired_at=transition_at,
        )

    def record_payment(
        self,
        *,
        payment_amount_uzs: Decimal,
        current_remaining_due_uzs: Decimal,
        expected_revision: DebtRevision,
        payment_created_at: datetime,
    ) -> DebtAggregate:
        if self.status not in {DebtStatus.ACTIVE, DebtStatus.OVERDUE}:
            raise DebtPaymentTransitionError(DebtPaymentFailure.NOT_PAYABLE)

        transition_at = normalize_payment_created_at(payment_created_at)
        if self.status is DebtStatus.ACTIVE and not is_payment_due_date_payable(
            payment_created_at=transition_at,
            due_date=self.due_date,
        ):
            raise DebtPaymentTransitionError(DebtPaymentFailure.NOT_PAYABLE)

        if not _is_positive_whole_uzs(
            current_remaining_due_uzs
        ) or current_remaining_due_uzs > (
            self.discounted_amount.value
            if self.status is DebtStatus.ACTIVE
            else self.original_amount.value
        ):
            raise DebtPaymentTransitionError(DebtPaymentFailure.NOT_PAYABLE)

        if not isinstance(expected_revision, DebtRevision):
            raise ValueError("Expected debt revision is invalid")
        if expected_revision != self.revision:
            raise DebtPaymentTransitionError(DebtPaymentFailure.CHANGED)

        if not _is_positive_whole_uzs(payment_amount_uzs):
            raise ValueError("Payment amount is invalid")
        if payment_amount_uzs > current_remaining_due_uzs:
            raise DebtPaymentTransitionError(DebtPaymentFailure.AMOUNT_EXCEEDS_BALANCE)

        is_full_payment = payment_amount_uzs == current_remaining_due_uzs
        return replace(
            self,
            status=DebtStatus.PAID if is_full_payment else self.status,
            revision=DebtRevision(self.revision.value + 1),
            updated_at=transition_at,
            paid_at=transition_at if is_full_payment else None,
        )

    def mark_overdue(
        self,
        *,
        now: datetime,
        source: DebtOverdueSource,
        posted_total_uzs: Decimal,
    ) -> DebtAggregate:
        if self.status is not DebtStatus.ACTIVE:
            raise DebtLifecycleError("Only active debt may become overdue")
        transition_at = _transition_time(now)
        if not is_effectively_overdue(
            status=self.status,
            due_date=self.due_date,
            server_now=transition_at,
        ):
            raise DebtLifecycleError("Active debt has not passed its due date")
        if not isinstance(source, DebtOverdueSource):
            raise ValueError("Debt overdue source is invalid")
        if source is DebtOverdueSource.PAYMENT_VOID:
            raise DebtLifecycleError(
                "Payment-void overdue requires a paid Debt transition"
            )
        if (
            not _is_nonnegative_whole_uzs(posted_total_uzs)
            or posted_total_uzs > self.discounted_amount.value
        ):
            raise DebtLifecycleError("Active debt payment ledger is incoherent")
        overdue_revision = DebtRevision(self.revision.value + 1)
        return replace(
            self,
            status=DebtStatus.OVERDUE,
            revision=overdue_revision,
            updated_at=transition_at,
            overdue_at=transition_at,
            overdue_revision=overdue_revision,
        )

    def mark_written_off(
        self,
        *,
        now: datetime,
        actor_user_id: UserId,
        reason: WriteOffReason,
        posted_total_uzs: Decimal,
        expected_revision: DebtRevision,
    ) -> DebtAggregate:
        if self.status is not DebtStatus.OVERDUE:
            raise DebtLifecycleError("Debt is not writable off")
        if not isinstance(expected_revision, DebtRevision):
            raise ValueError("Expected debt revision is invalid")
        if expected_revision != self.revision:
            raise DebtPaymentTransitionError(DebtPaymentFailure.CHANGED)
        if not isinstance(reason, WriteOffReason):
            raise ValueError("Write-off reason is invalid")
        _require_uuid(actor_user_id, field_name="write-off actor")
        if (
            not _is_nonnegative_whole_uzs(posted_total_uzs)
            or posted_total_uzs >= self.original_amount.value
        ):
            raise DebtLifecycleError("Debt write-off balance is invalid")
        transition_at = _transition_time(now)
        if self.overdue_at is None or transition_at < self.overdue_at:
            raise DebtLifecycleError("Debt write-off time is invalid")
        revision = DebtRevision(self.revision.value + 1)
        return replace(
            self,
            status=DebtStatus.WRITTEN_OFF,
            revision=revision,
            updated_at=transition_at,
            written_off_at=transition_at,
            written_off_revision=revision,
            written_off_reason=reason,
            written_off_actor_user_id=actor_user_id,
        )

    def record_written_off_recovery(
        self,
        *,
        payment_amount_uzs: Decimal,
        current_remaining_due_uzs: Decimal,
        expected_revision: DebtRevision,
        payment_created_at: datetime,
    ) -> DebtAggregate:
        if self.status is not DebtStatus.WRITTEN_OFF:
            raise DebtPaymentTransitionError(DebtPaymentFailure.NOT_PAYABLE)
        if not isinstance(expected_revision, DebtRevision):
            raise ValueError("Expected debt revision is invalid")
        if expected_revision != self.revision:
            raise DebtPaymentTransitionError(DebtPaymentFailure.CHANGED)
        if (
            not _is_positive_whole_uzs(current_remaining_due_uzs)
            or current_remaining_due_uzs > self.original_amount.value
        ):
            raise DebtPaymentTransitionError(DebtPaymentFailure.NOT_PAYABLE)
        if not _is_positive_whole_uzs(payment_amount_uzs):
            raise ValueError("Payment amount is invalid")
        if payment_amount_uzs > current_remaining_due_uzs:
            raise DebtPaymentTransitionError(DebtPaymentFailure.AMOUNT_EXCEEDS_BALANCE)
        transition_at = normalize_payment_created_at(payment_created_at)
        if self.written_off_at is None or transition_at < self.written_off_at:
            raise DebtPaymentTransitionError(DebtPaymentFailure.NOT_PAYABLE)
        revision = DebtRevision(self.revision.value + 1)
        if payment_amount_uzs < current_remaining_due_uzs:
            return replace(self, revision=revision, updated_at=transition_at)
        return replace(
            self,
            status=DebtStatus.WRITTEN_OFF_SETTLED,
            revision=revision,
            updated_at=transition_at,
            written_off_settled_at=transition_at,
            written_off_settled_revision=revision,
        )

    def to_projection(self) -> DebtProjection:
        return DebtProjection(
            original_amount=self.original_amount,
            discount_basis_points=self.discount_basis_points,
            discounted_amount=self.discounted_amount,
            due_date=self.due_date,
            pending_expires_at=self.pending_expires_at,
            status=self.status,
            revision=self.revision,
            created_at=self.created_at,
            updated_at=self.updated_at,
            accepted_at=self.accepted_at,
            rejected_at=self.rejected_at,
            cancelled_at=self.cancelled_at,
            expired_at=self.expired_at,
            paid_at=self.paid_at,
            overdue_at=self.overdue_at,
            overdue_revision=self.overdue_revision,
            written_off_at=self.written_off_at,
            written_off_settled_at=self.written_off_settled_at,
        )

    def _require_unexpired_pending(self, now: datetime) -> None:
        self._require_pending()
        if is_pending_expired(now=now, pending_expires_at=self.pending_expires_at):
            raise DebtLifecycleError("Pending debt has expired")

    def _require_pending(self) -> None:
        if self.status is not DebtStatus.PENDING:
            raise DebtLifecycleError("Debt is not pending")

    def _validate_lifecycle_timestamps(self) -> None:
        terminal_times = (
            self.rejected_at,
            self.cancelled_at,
            self.expired_at,
        )
        if self.status is not DebtStatus.PAID and self.paid_at is not None:
            raise ValueError("Only paid debt may have payment timestamp")
        if self.status is DebtStatus.PENDING:
            self._require_no_overdue_metadata()
            if self.accepted_at is not None or any(
                value is not None for value in terminal_times
            ):
                raise ValueError("Pending debt cannot have lifecycle timestamps")
            if (
                self.rejection_reason is not None
                or self.cancellation_reason is not None
            ):
                raise ValueError("Pending debt cannot have terminal reasons")
            return
        if self.status is DebtStatus.ACTIVE:
            self._require_no_overdue_metadata()
            if self.accepted_at is None or any(
                value is not None for value in terminal_times
            ):
                raise ValueError("Active debt requires only acceptance timestamp")
            if (
                self.rejection_reason is not None
                or self.cancellation_reason is not None
            ):
                raise ValueError("Active debt cannot have terminal reasons")
            return
        if self.status is DebtStatus.REJECTED:
            self._require_no_overdue_metadata()
            if (
                self.rejected_at is None
                or self.accepted_at is not None
                or any(
                    value is not None for value in (self.cancelled_at, self.expired_at)
                )
            ):
                raise ValueError("Rejected debt requires only rejection timestamp")
            if self.cancellation_reason is not None:
                raise ValueError("Rejected debt has incompatible terminal metadata")
            return
        if self.status is DebtStatus.CANCELLED:
            self._require_no_overdue_metadata()
            if self.cancelled_at is None or self.cancellation_reason is None:
                raise ValueError(
                    "Cancelled debt requires cancellation reason and timestamp"
                )
            if (
                self.accepted_at is not None
                or self.rejected_at is not None
                or self.expired_at is not None
            ):
                raise ValueError("Cancelled debt requires only cancellation timestamp")
            if self.rejection_reason is not None:
                raise ValueError("Cancelled debt has incompatible terminal metadata")
            return
        if self.status is DebtStatus.EXPIRED:
            self._require_no_overdue_metadata()
            if self.expired_at is None:
                raise ValueError("Expired debt requires expiry timestamp")
            if (
                self.accepted_at is not None
                or self.rejected_at is not None
                or self.cancelled_at is not None
            ):
                raise ValueError("Expired debt requires only expiry timestamp")
            if (
                self.rejection_reason is not None
                or self.cancellation_reason is not None
            ):
                raise ValueError("Expired debt cannot have terminal reasons")
            return
        if self.status is DebtStatus.OVERDUE:
            if (
                self.accepted_at is None
                or self.overdue_at is None
                or self.overdue_revision is None
                or any(value is not None for value in terminal_times)
            ):
                raise ValueError(
                    "Overdue debt requires acceptance and overdue metadata only"
                )
            if (
                self.rejection_reason is not None
                or self.cancellation_reason is not None
            ):
                raise ValueError("Overdue debt cannot have terminal reasons")
            if self.overdue_at < self.accepted_at:
                raise ValueError("Debt overdue timestamp cannot precede acceptance")
            return
        if self.status in {DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF_SETTLED}:
            if (
                self.accepted_at is None
                or self.overdue_at is None
                or any(value is not None for value in terminal_times)
                or self.paid_at is not None
                or self.rejection_reason is not None
                or self.cancellation_reason is not None
            ):
                raise ValueError("Written-off debt lifecycle metadata is invalid")
            if self.overdue_at < self.accepted_at:
                raise ValueError("Debt overdue timestamp cannot precede acceptance")
            return
        if self.status is DebtStatus.PAID:
            if (
                self.accepted_at is None
                or self.paid_at is None
                or any(value is not None for value in terminal_times)
            ):
                raise ValueError(
                    "Paid debt requires acceptance and payment timestamps only"
                )
            if (
                self.rejection_reason is not None
                or self.cancellation_reason is not None
            ):
                raise ValueError("Paid debt cannot have terminal reasons")
            if self.paid_at < self.accepted_at:
                raise ValueError("Debt payment timestamp cannot precede acceptance")
            if self.overdue_at is None:
                if self.overdue_revision is not None:
                    raise ValueError("Debt overdue metadata must be present together")
            else:
                if (
                    self.overdue_revision is None
                    or self.overdue_revision.value >= self.revision.value
                ):
                    raise ValueError(
                        "Late-paid debt requires an earlier overdue revision"
                    )
                if self.overdue_at < self.accepted_at or self.paid_at < self.overdue_at:
                    raise ValueError("Late-paid debt timestamps are out of order")
            return
        raise ValueError("Debt cannot use a status outside M17")

    def _validate_write_off_metadata(self) -> None:
        write_off_values = (
            self.written_off_at,
            self.written_off_revision,
            self.written_off_reason,
            self.written_off_actor_user_id,
        )
        if any(value is not None for value in write_off_values) and not all(
            value is not None for value in write_off_values
        ):
            raise ValueError("Debt write-off metadata must be present together")
        settlement_values = (
            self.written_off_settled_at,
            self.written_off_settled_revision,
        )
        if any(value is not None for value in settlement_values) and not all(
            value is not None for value in settlement_values
        ):
            raise ValueError("Debt settlement metadata must be present together")
        has_write_off = all(value is not None for value in write_off_values)
        has_settlement = all(value is not None for value in settlement_values)
        if self.status not in {DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF_SETTLED}:
            if has_write_off or has_settlement:
                raise ValueError("Debt status cannot carry write-off metadata")
            return
        if not has_write_off:
            raise ValueError("Written-off debt requires write-off metadata")
        if self.overdue_at is None or self.overdue_revision is None:
            raise ValueError("Written-off debt requires overdue metadata")
        assert self.written_off_revision is not None
        assert self.written_off_at is not None
        if not isinstance(self.written_off_reason, WriteOffReason):
            raise ValueError("Debt write-off reason is invalid")
        _require_uuid(self.written_off_actor_user_id, field_name="write-off actor")
        if self.written_off_revision.value <= self.overdue_revision.value:
            raise ValueError("Debt write-off revision must follow overdue revision")
        if self.written_off_revision.value > self.revision.value:
            raise ValueError("Debt write-off revision cannot exceed current revision")
        if self.written_off_at < self.overdue_at:
            raise ValueError("Debt write-off time cannot precede overdue time")
        if self.status is DebtStatus.WRITTEN_OFF:
            if has_settlement:
                raise ValueError("Unsettled written-off debt cannot carry settlement")
            return
        if not has_settlement:
            raise ValueError("Settled written-off debt requires settlement metadata")
        assert self.written_off_settled_revision is not None
        assert self.written_off_settled_at is not None
        if self.written_off_settled_revision.value <= self.written_off_revision.value:
            raise ValueError("Debt settlement revision must follow write-off revision")
        if self.written_off_settled_revision != self.revision:
            raise ValueError("Debt settlement revision must equal current revision")
        if self.written_off_settled_at < self.written_off_at:
            raise ValueError("Debt settlement time cannot precede write-off time")

    def _require_no_overdue_metadata(self) -> None:
        if self.overdue_at is not None or self.overdue_revision is not None:
            raise ValueError("Debt status cannot carry overdue metadata")

    def __repr__(self) -> str:
        return (
            "DebtAggregate(id=<redacted>, shop_customer_id=<redacted>, "
            "created_by_user_id=<redacted>, "
            f"original_amount={self.original_amount!r}, "
            f"discount_basis_points={self.discount_basis_points!r}, "
            f"discounted_amount={self.discounted_amount!r}, "
            f"due_date={self.due_date!r}, "
            f"pending_expires_at={self.pending_expires_at!r}, status={self.status!r}, "
            f"revision={self.revision!r}, created_at={self.created_at!r}, "
            f"updated_at={self.updated_at!r}, accepted_at={self.accepted_at!r}, "
            f"rejected_at={self.rejected_at!r}, cancelled_at={self.cancelled_at!r}, "
            f"expired_at={self.expired_at!r}, rejection_reason=<redacted>, "
            f"paid_at={self.paid_at!r}, cancellation_reason=<redacted>, "
            "written_off_reason=<redacted>, written_off_actor_user_id=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PendingPaymentVoidOverdueEffect:
    """Typed canonical overdue effect for only paid-after-due void."""

    source: DebtOverdueSource
    from_status: DebtStatus
    overdue_revision: DebtRevision
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.source is not DebtOverdueSource.PAYMENT_VOID:
            raise DebtPaymentVoidTransitionError(
                "Payment-void overdue effect source is invalid"
            )
        if self.from_status is not DebtStatus.PAID:
            raise DebtPaymentVoidTransitionError(
                "Payment-void overdue effect must originate from paid"
            )
        if not isinstance(self.overdue_revision, DebtRevision):
            raise DebtPaymentVoidTransitionError(
                "Payment-void overdue effect revision is invalid"
            )
        object.__setattr__(self, "occurred_at", _transition_time(self.occurred_at))

    def __repr__(self) -> str:
        return "PendingPaymentVoidOverdueEffect(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DebtPaymentVoidTransition:
    """Validated before/after contract consumed by the later pure producer."""

    before: DebtAggregate = field(repr=False)
    debt: DebtAggregate = field(repr=False)
    expected_revision: DebtRevision
    voided_at: datetime
    remaining_due_uzs: Decimal = field(repr=False)
    overdue_effect: PendingPaymentVoidOverdueEffect | None = field(
        default=None, repr=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.before, DebtAggregate) or not isinstance(
            self.debt, DebtAggregate
        ):
            raise TypeError("Payment-void transition requires Debt aggregates")
        if not isinstance(self.expected_revision, DebtRevision):
            raise ValueError("Expected debt revision is invalid")
        if self.expected_revision != self.before.revision:
            raise DebtPaymentVoidTransitionError("Debt revision changed")
        voided_at = _transition_time(self.voided_at)
        object.__setattr__(self, "voided_at", voided_at)
        if not _is_positive_whole_uzs(self.remaining_due_uzs):
            raise DebtPaymentVoidTransitionError(
                "Payment void must restore a positive remaining balance"
            )
        self._validate_common_state(voided_at)
        self._validate_status_and_markers(voided_at)

    def _validate_common_state(self, voided_at: datetime) -> None:
        before = self.before
        after = self.debt
        if after.revision != DebtRevision(before.revision.value + 1):
            raise DebtPaymentVoidTransitionError(
                "Payment void must consume exactly one Debt revision"
            )
        if after.updated_at != voided_at:
            raise DebtPaymentVoidTransitionError(
                "Payment void update time must equal void time"
            )
        immutable_fields = (
            "id",
            "shop_customer_id",
            "created_by_user_id",
            "original_amount",
            "discount_basis_points",
            "discounted_amount",
            "due_date",
            "pending_expires_at",
            "created_at",
            "accepted_at",
            "rejected_at",
            "cancelled_at",
            "expired_at",
            "rejection_reason",
            "cancellation_reason",
            "written_off_at",
            "written_off_revision",
            "written_off_reason",
            "written_off_actor_user_id",
        )
        if any(
            getattr(before, name) != getattr(after, name) for name in immutable_fields
        ):
            raise DebtPaymentVoidTransitionError(
                "Payment void changed immutable Debt evidence"
            )
        if after.paid_at is not None:
            raise DebtPaymentVoidTransitionError(
                "Payment void must clear the current paid marker"
            )
        if (
            after.written_off_settled_at is not None
            or after.written_off_settled_revision is not None
        ):
            raise DebtPaymentVoidTransitionError(
                "Payment void must clear the current settlement marker pair"
            )

    def _validate_status_and_markers(self, voided_at: datetime) -> None:
        before = self.before
        after = self.debt
        effect = self.overdue_effect
        if before.status is DebtStatus.ACTIVE:
            self._require_exact_status(DebtStatus.ACTIVE)
            self._require_preserved_overdue_markers()
            self._require_no_overdue_effect()
            return
        if before.status is DebtStatus.OVERDUE:
            self._require_exact_status(DebtStatus.OVERDUE)
            self._require_preserved_overdue_markers()
            self._require_no_overdue_effect()
            return
        if before.status is DebtStatus.WRITTEN_OFF:
            self._require_exact_status(DebtStatus.WRITTEN_OFF)
            self._require_preserved_overdue_markers()
            self._require_no_overdue_effect()
            return
        if before.status is DebtStatus.WRITTEN_OFF_SETTLED:
            self._require_exact_status(DebtStatus.WRITTEN_OFF)
            self._require_preserved_overdue_markers()
            self._require_no_overdue_effect()
            return
        if before.status is not DebtStatus.PAID:
            raise DebtPaymentVoidTransitionError(
                "Debt status cannot be reopened by Payment void"
            )
        if before.overdue_revision is not None:
            self._require_exact_status(DebtStatus.OVERDUE)
            self._require_preserved_overdue_markers()
            self._require_no_overdue_effect()
            return
        if is_payment_due_date_payable(
            payment_created_at=voided_at,
            due_date=before.due_date,
        ):
            self._require_exact_status(DebtStatus.ACTIVE)
            self._require_preserved_overdue_markers()
            self._require_no_overdue_effect()
            return

        self._require_exact_status(DebtStatus.OVERDUE)
        if after.overdue_at != voided_at or after.overdue_revision != after.revision:
            raise DebtPaymentVoidTransitionError(
                "Paid-after-due void requires current-revision overdue markers"
            )
        if (
            not isinstance(effect, PendingPaymentVoidOverdueEffect)
            or effect.overdue_revision != after.revision
            or effect.occurred_at != voided_at
        ):
            raise DebtPaymentVoidTransitionError(
                "Paid-after-due void requires one canonical pending overdue effect"
            )

    def _require_exact_status(self, status: DebtStatus) -> None:
        if self.debt.status is not status:
            raise DebtPaymentVoidTransitionError(
                "Payment void resulting Debt status is invalid"
            )

    def _require_preserved_overdue_markers(self) -> None:
        if (
            self.debt.overdue_at != self.before.overdue_at
            or self.debt.overdue_revision != self.before.overdue_revision
        ):
            raise DebtPaymentVoidTransitionError(
                "Payment void changed historic overdue evidence"
            )

    def _require_no_overdue_effect(self) -> None:
        if self.overdue_effect is not None:
            raise DebtPaymentVoidTransitionError(
                "Payment void produced an unexpected overdue effect"
            )

    def __repr__(self) -> str:
        return "DebtPaymentVoidTransition(<redacted>)"


def reopen_debt_after_payment_void(
    *,
    debt: DebtAggregate,
    expected_revision: DebtRevision,
    payment_created_at: datetime,
    voided_at: datetime,
    remaining_due_uzs: Decimal,
) -> DebtPaymentVoidTransition:
    """Purely derive the one-revision Debt state restored by a latest void."""

    if not isinstance(debt, DebtAggregate):
        raise TypeError("debt must be a DebtAggregate")
    if expected_revision != debt.revision:
        raise DebtPaymentVoidTransitionError("Debt revision changed")
    payment_at = _transition_time(payment_created_at)
    normalized_voided_at = _transition_time(voided_at)
    if normalized_voided_at < payment_at or normalized_voided_at < debt.updated_at:
        raise DebtPaymentVoidTransitionError(
            "Payment void cannot precede current source facts"
        )
    next_revision = DebtRevision(debt.revision.value + 1)
    changes: dict[str, object] = {
        "revision": next_revision,
        "updated_at": normalized_voided_at,
    }
    effect = None
    if debt.status is DebtStatus.PAID:
        changes["paid_at"] = None
        if debt.overdue_revision is not None:
            changes["status"] = DebtStatus.OVERDUE
        elif is_payment_due_date_payable(
            payment_created_at=normalized_voided_at,
            due_date=debt.due_date,
        ):
            changes["status"] = DebtStatus.ACTIVE
        else:
            changes.update(
                status=DebtStatus.OVERDUE,
                overdue_at=normalized_voided_at,
                overdue_revision=next_revision,
            )
            effect = PendingPaymentVoidOverdueEffect(
                source=DebtOverdueSource.PAYMENT_VOID,
                from_status=DebtStatus.PAID,
                overdue_revision=next_revision,
                occurred_at=normalized_voided_at,
            )
    elif debt.status is DebtStatus.WRITTEN_OFF_SETTLED:
        changes.update(
            status=DebtStatus.WRITTEN_OFF,
            written_off_settled_at=None,
            written_off_settled_revision=None,
        )
    elif debt.status not in {
        DebtStatus.ACTIVE,
        DebtStatus.OVERDUE,
        DebtStatus.WRITTEN_OFF,
    }:
        raise DebtPaymentVoidTransitionError(
            "Debt status cannot be reopened by Payment void"
        )
    after = replace(debt, **changes)
    return DebtPaymentVoidTransition(
        before=debt,
        debt=after,
        expected_revision=expected_revision,
        voided_at=normalized_voided_at,
        remaining_due_uzs=remaining_due_uzs,
        overdue_effect=effect,
    )


def _require_uuid(value: object, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"Debt {field_name} is invalid")


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"Debt {field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _transition_time(now: datetime) -> datetime:
    return _as_utc(now, field_name="transition time")


def _is_positive_whole_uzs(value: object) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and value.as_tuple().exponent == 0
        and value > Decimal("0")
    )


def _is_nonnegative_whole_uzs(value: object) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and value.as_tuple().exponent == 0
        and value >= Decimal("0")
    )
