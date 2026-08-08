"""Server-authoritative raw-input assembly for a pending debt create."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.debt.business_time import (
    parse_due_date,
    pending_expires_at,
    validate_due_date_not_before_expiry_business_date,
)
from app.debt.values import (
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    calculate_discounted_amount,
    parse_discount_percent,
    parse_original_amount_uzs,
)
from app.idempotency.contracts import (
    CanonicalIdempotencyKey,
    require_matching_idempotency_keys,
)

__all__ = (
    "CreateDebtCommand",
    "CreateDebtRawForm",
    "assemble_create_debt_command",
)


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
