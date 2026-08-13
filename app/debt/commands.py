"""Server-authoritative raw-input assembly for a pending debt create."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from app.debt.business_time import (
    parse_due_date,
    pending_expires_at,
    validate_due_date_not_before_expiry_business_date,
)
from app.debt.contracts import WriteOffReason
from app.debt.values import (
    DebtId,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    calculate_discounted_amount,
    parse_discount_percent,
    parse_original_amount_uzs,
)
from app.idempotency.contracts import (
    CanonicalIdempotencyKey,
    IdempotencyOutcome,
    WriteOffDebtRequestHash,
    create_write_off_debt_request_hash_v1,
    parse_idempotency_key,
    require_matching_idempotency_keys,
)
from app.offers.authorization import PlatformAdminActor

__all__ = (
    "CreateDebtCommand",
    "CreateDebtRawForm",
    "WriteOffDebtCommand",
    "WriteOffDebtCommandAssembly",
    "WriteOffDebtFailure",
    "WriteOffDebtMutationResult",
    "WriteOffDebtRawForm",
    "WriteOffReason",
    "WriteOffAdminRoute",
    "M17_ADMIN_WRITE_OFF_ROUTES",
    "assemble_create_debt_command",
    "assemble_write_off_debt_command",
)

_CANONICAL_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*", flags=re.ASCII)


class WriteOffDebtFailure(StrEnum):
    UNAVAILABLE = "DEBT_UNAVAILABLE"
    CHANGED = "DEBT_CHANGED"
    NOT_WRITABLE_OFF = "DEBT_NOT_WRITABLE_OFF"
    INVALID_REASON = "WRITE_OFF_REASON_INVALID"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True, slots=True)
class WriteOffAdminRoute:
    method: Literal["GET", "POST"]
    path: str

    def __post_init__(self) -> None:
        if self.method not in {"GET", "POST"}:
            raise ValueError("Write-off admin route method is invalid")
        if not isinstance(self.path, str) or not self.path.startswith("/admin/"):
            raise ValueError("Write-off admin route path is invalid")


M17_ADMIN_WRITE_OFF_ROUTES: Final = (
    WriteOffAdminRoute("GET", "/admin/debts/write-off-candidates"),
    WriteOffAdminRoute("GET", "/admin/debts/{debt_id}/write-off"),
    WriteOffAdminRoute("POST", "/admin/debts/{debt_id}/write-off"),
)


@dataclass(frozen=True, slots=True, repr=False)
class WriteOffDebtRawForm:
    debt_id: str = field(repr=False)
    expected_revision: str = field(repr=False)
    reason: str | None = field(repr=False)
    idempotency_key: str | None = field(repr=False)

    def __repr__(self) -> str:
        return "WriteOffDebtRawForm(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class WriteOffDebtCommand:
    actor: PlatformAdminActor = field(repr=False)
    actor_user_id: UUID = field(init=False, repr=False)
    debt_id: DebtId = field(repr=False)
    expected_revision: DebtRevision
    reason: WriteOffReason = field(repr=False)
    idempotency_key: CanonicalIdempotencyKey = field(repr=False)
    request_hash: WriteOffDebtRequestHash = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor, PlatformAdminActor):
            raise ValueError("Write-off command authority is invalid")
        object.__setattr__(self, "actor_user_id", self.actor.user_id)
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Write-off command Debt is invalid")
        if not isinstance(self.expected_revision, DebtRevision):
            raise ValueError("Write-off command revision is invalid")
        if not isinstance(self.reason, WriteOffReason):
            raise ValueError("Write-off command reason is invalid")
        if not isinstance(self.idempotency_key, CanonicalIdempotencyKey):
            raise ValueError("Write-off command key is invalid")
        expected = create_write_off_debt_request_hash_v1(
            actor_user_id=self.actor_user_id,
            debt_id=self.debt_id,
            expected_revision=self.expected_revision,
            reason=self.reason,
        )
        if self.request_hash != expected:
            raise ValueError("Write-off command request hash is invalid")

    def __repr__(self) -> str:
        return "WriteOffDebtCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class WriteOffDebtCommandAssembly:
    command: WriteOffDebtCommand | None = field(default=None, repr=False)
    failure: WriteOffDebtFailure | None = None

    def __post_init__(self) -> None:
        if (self.command is None) == (self.failure is None):
            raise ValueError("Write-off command assembly is invalid")
        if self.failure not in {None, WriteOffDebtFailure.INVALID_REASON}:
            raise ValueError("Write-off command assembly failure is invalid")

    def __repr__(self) -> str:
        return (
            f"WriteOffDebtCommandAssembly(failure={self.failure!r}, command=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class WriteOffDebtMutationResult:
    outcome: IdempotencyOutcome
    debt_id: DebtId = field(repr=False)

    def __post_init__(self) -> None:
        if self.outcome not in {IdempotencyOutcome.NEW, IdempotencyOutcome.REPLAY}:
            raise ValueError("Write-off mutation outcome is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Write-off mutation Debt is invalid")

    def __repr__(self) -> str:
        return (
            f"WriteOffDebtMutationResult(outcome={self.outcome.value!r}, id=<redacted>)"
        )


def assemble_write_off_debt_command(
    *,
    actor: PlatformAdminActor,
    raw: WriteOffDebtRawForm,
) -> WriteOffDebtCommandAssembly:
    """Accept only a platform-admin capability and canonical closed inputs."""

    if not isinstance(actor, PlatformAdminActor):
        raise TypeError("actor must be a PlatformAdminActor")
    if not isinstance(raw, WriteOffDebtRawForm):
        raise TypeError("raw must be a WriteOffDebtRawForm")
    try:
        debt_id = DebtId(UUID(raw.debt_id))
        if str(debt_id.as_uuid()) != raw.debt_id:
            raise ValueError("Debt locator is not canonical")
        if _CANONICAL_POSITIVE_INTEGER.fullmatch(raw.expected_revision) is None:
            raise ValueError("Expected revision is invalid")
        expected_revision = DebtRevision(int(raw.expected_revision))
        reason = WriteOffReason(raw.reason)
        key = parse_idempotency_key(raw.idempotency_key)  # type: ignore[arg-type]
        request_hash = create_write_off_debt_request_hash_v1(
            actor_user_id=actor.user_id,
            debt_id=debt_id,
            expected_revision=expected_revision,
            reason=reason,
        )
        return WriteOffDebtCommandAssembly(
            command=WriteOffDebtCommand(
                actor=actor,
                debt_id=debt_id,
                expected_revision=expected_revision,
                reason=reason,
                idempotency_key=key,
                request_hash=request_hash,
            )
        )
    except (TypeError, ValueError, OverflowError):
        return WriteOffDebtCommandAssembly(failure=WriteOffDebtFailure.INVALID_REASON)


@dataclass(frozen=True, slots=True, repr=False)
class CreateDebtRawForm:
    """Exactly the browser fields that may influence a new pending debt."""

    original_amount_uzs: str = field(repr=False)
    discount_percent: str = field(repr=False)
    due_date: str = field(repr=False)
    idempotency_key: str | None = field(repr=False)

    def __repr__(self) -> str:
        return "CreateDebtRawForm(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CreateDebtCommand:
    """Immutable server result; it deliberately has no client financial outputs."""

    idempotency_key: CanonicalIdempotencyKey = field(repr=False)
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: date
    created_at: datetime
    pending_expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, CanonicalIdempotencyKey):
            raise ValueError("Create debt idempotency key is invalid")
        if not isinstance(self.original_amount, OriginalAmountUZS):
            raise ValueError("Create debt original amount is invalid")
        if not isinstance(self.discount_basis_points, DiscountBasisPoints):
            raise ValueError("Create debt discount is invalid")
        if not isinstance(self.discounted_amount, DiscountedAmountUZS):
            raise ValueError("Create debt discounted amount is invalid")
        if not isinstance(self.due_date, date) or isinstance(self.due_date, datetime):
            raise ValueError("Create debt due date is invalid")
        created_at = _as_utc(self.created_at)
        expiry = _as_utc(self.pending_expires_at)
        if expiry != pending_expires_at(created_at):
            raise ValueError("Create debt pending expiry is invalid")
        if self.discounted_amount != calculate_discounted_amount(
            self.original_amount, self.discount_basis_points
        ):
            raise ValueError("Create debt discounted amount is not server-computed")
        validate_due_date_not_before_expiry_business_date(
            due_date=self.due_date, pending_expiry=expiry
        )
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "pending_expires_at", expiry)

    def __repr__(self) -> str:
        return (
            "CreateDebtCommand(idempotency_key=<redacted>, "
            f"original_amount={self.original_amount!r}, "
            f"discount_basis_points={self.discount_basis_points!r}, "
            f"discounted_amount={self.discounted_amount!r}, "
            f"due_date={self.due_date!r}, created_at={self.created_at!r}, "
            f"pending_expires_at={self.pending_expires_at!r})"
        )


def assemble_create_debt_command(
    *,
    form: CreateDebtRawForm,
    header_idempotency_key: str | None,
    now: datetime,
) -> CreateDebtCommand:
    """Parse only permitted raw fields and derive every persisted calculation."""

    if not isinstance(form, CreateDebtRawForm):
        raise TypeError("form must be a CreateDebtRawForm")
    created_at = _as_utc(now)
    original_amount = parse_original_amount_uzs(form.original_amount_uzs)
    discount_basis_points = parse_discount_percent(
        form.discount_percent
    ).as_basis_points()
    discounted_amount = calculate_discounted_amount(
        original_amount, discount_basis_points
    )
    expiry = pending_expires_at(created_at)
    due_date = validate_due_date_not_before_expiry_business_date(
        due_date=parse_due_date(form.due_date),
        pending_expiry=expiry,
    )
    return CreateDebtCommand(
        idempotency_key=require_matching_idempotency_keys(
            form_value=form.idempotency_key,
            header_value=header_idempotency_key,
        ),
        original_amount=original_amount,
        discount_basis_points=discount_basis_points,
        discounted_amount=discounted_amount,
        due_date=due_date,
        created_at=created_at,
        pending_expires_at=expiry,
    )


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Create debt now must be an aware datetime")
    return value.astimezone(UTC)
