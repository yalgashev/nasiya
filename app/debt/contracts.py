"""Immutable pending-debt aggregate and lifecycle contracts."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from uuid import UUID

from app.debt.business_time import (
    is_pending_expired,
    pending_expires_at,
    validate_due_date_not_before_expiry_business_date,
)
from app.debt.enums import DebtExpirySource, DebtStatus
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
    "DebtProjection",
    "DebtReason",
)


class DebtLifecycleError(ValueError):
    """Raised when an immutable pending-debt transition is unavailable."""


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
    rejection_reason: DebtReason | None = field(default=None, repr=False)
    cancellation_reason: DebtReason | None = field(default=None, repr=False)

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
        if not isinstance(self.status, DebtStatus):
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
        for field_name in ("accepted_at", "rejected_at", "cancelled_at", "expired_at"):
            value = getattr(self, field_name)
            if value is not None:
                normalized = _as_utc(value, field_name=field_name)
                if normalized < created_at or normalized > updated_at:
                    raise ValueError("Debt lifecycle timestamp is invalid")
                object.__setattr__(self, field_name, normalized)
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
        if self.status is DebtStatus.PENDING:
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
        raise ValueError("M13 debt cannot use future lifecycle status")

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
            "cancellation_reason=<redacted>)"
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
