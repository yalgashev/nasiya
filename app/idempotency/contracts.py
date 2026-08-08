"""Canonical, redacted idempotency key and create-debt hash contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.debt.values import (
    DebtId,
    DiscountBasisPoints,
    OriginalAmountUZS,
    ShopCustomerId,
    ShopId,
    UserId,
)

__all__ = (
    "CanonicalIdempotencyKey",
    "CompletedIdempotencyResult",
    "CreateDebtRequestHash",
    "IdempotencyEndpoint",
    "IdempotencyKeyDigest",
    "IdempotencyOutcome",
    "IdempotencyResolution",
    "IdempotencyResultType",
    "canonical_idempotency_key_digest",
    "parse_idempotency_key",
    "require_matching_idempotency_keys",
)

_SHA256_HEX_PATTERN: Final = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)
_CREATE_DEBT_HASH_DOMAIN: Final = b"nasiya.m13.create-debt.request.v1"


@dataclass(frozen=True, slots=True, repr=False)
class CanonicalIdempotencyKey:
    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise ValueError("Idempotency key is invalid")

    def as_uuid(self) -> UUID:
        return self.value

    def __repr__(self) -> str:
        return "CanonicalIdempotencyKey(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class IdempotencyKeyDigest:
    value: str

    def __post_init__(self) -> None:
        _require_sha256_hex(self.value, field_name="Idempotency key digest")

    def __repr__(self) -> str:
        return "IdempotencyKeyDigest(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CreateDebtRequestHash:
    value: str

    def __post_init__(self) -> None:
        _require_sha256_hex(self.value, field_name="Create-debt request hash")

    def __repr__(self) -> str:
        return "CreateDebtRequestHash(<redacted>)"


class IdempotencyEndpoint(StrEnum):
    SHOP_DEBTS_CREATE = "shop.debts.create"


class IdempotencyResultType(StrEnum):
    DEBT = "debt"


class IdempotencyOutcome(StrEnum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True, repr=False)
class CompletedIdempotencyResult:
    result_type: IdempotencyResultType
    debt_id: DebtId = field(repr=False)
    completed_at: datetime

    def __post_init__(self) -> None:
        if self.result_type is not IdempotencyResultType.DEBT:
            raise ValueError("Idempotency result type is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Idempotency debt result is invalid")
        if (
            not isinstance(self.completed_at, datetime)
            or self.completed_at.tzinfo is None
            or self.completed_at.utcoffset() is None
        ):
            raise ValueError("Idempotency completion time must be timezone-aware")
        object.__setattr__(self, "completed_at", self.completed_at.astimezone(UTC))

    def __repr__(self) -> str:
        return (
            "CompletedIdempotencyResult("
            f"result_type={self.result_type.value!r}, debt_id=<redacted>, "
            f"completed_at={self.completed_at!r})"
        )


@dataclass(frozen=True, slots=True)
class IdempotencyResolution:
    outcome: IdempotencyOutcome
    completed_result: CompletedIdempotencyResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, IdempotencyOutcome):
            raise ValueError("Idempotency outcome is invalid")
        if self.outcome is IdempotencyOutcome.REPLAY:
            if not isinstance(self.completed_result, CompletedIdempotencyResult):
                raise ValueError("Idempotency replay requires completed result")
        elif self.completed_result is not None:
            raise ValueError("Only idempotency replay may disclose completed result")

    @classmethod
    def new(cls) -> IdempotencyResolution:
        return cls(outcome=IdempotencyOutcome.NEW)

    @classmethod
    def replay(cls, result: CompletedIdempotencyResult) -> IdempotencyResolution:
        return cls(outcome=IdempotencyOutcome.REPLAY, completed_result=result)

    @classmethod
    def conflict(cls) -> IdempotencyResolution:
        return cls(outcome=IdempotencyOutcome.CONFLICT)


def parse_idempotency_key(value: str) -> CanonicalIdempotencyKey:
    if not isinstance(value, str):
        raise ValueError("Idempotency key is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Idempotency key is invalid") from None
    if str(parsed) != value:
        raise ValueError("Idempotency key is invalid")
    return CanonicalIdempotencyKey(parsed)


def require_matching_idempotency_keys(
    *, form_value: str | None, header_value: str | None
) -> CanonicalIdempotencyKey:
    if form_value is None and header_value is None:
        raise ValueError("Idempotency key is required")
    form_key = parse_idempotency_key(form_value) if form_value is not None else None
    header_key = (
        parse_idempotency_key(header_value) if header_value is not None else None
    )
    if form_key is not None and header_key is not None and form_key != header_key:
        raise ValueError("Idempotency keys do not match")
    return form_key or header_key  # type: ignore[return-value]


def canonical_idempotency_key_digest(
    key: CanonicalIdempotencyKey,
) -> IdempotencyKeyDigest:
    if not isinstance(key, CanonicalIdempotencyKey):
        raise ValueError("Idempotency key is invalid")
    canonical = str(key.as_uuid()).encode("ascii")
    return IdempotencyKeyDigest(hashlib.sha256(canonical).hexdigest())


def create_debt_request_hash(
    *,
    actor_user_id: UserId,
    shop_id: ShopId,
    shop_customer_id: ShopCustomerId,
    original_amount: OriginalAmountUZS,
    discount_basis_points: DiscountBasisPoints,
    due_date: date,
) -> CreateDebtRequestHash:
    if not isinstance(actor_user_id, UUID) or not isinstance(shop_id, UUID):
        raise ValueError("Create-debt request identity is invalid")
    if not isinstance(shop_customer_id, ShopCustomerId):
        raise ValueError("Create-debt request shop customer is invalid")
    if not isinstance(original_amount, OriginalAmountUZS):
        raise ValueError("Create-debt request original amount is invalid")
    if not isinstance(discount_basis_points, DiscountBasisPoints):
        raise ValueError("Create-debt request discount is invalid")
    if not isinstance(due_date, date) or isinstance(due_date, datetime):
        raise ValueError("Create-debt request due date is invalid")
    encoded = _length_safe_encode(
        (
            _CREATE_DEBT_HASH_DOMAIN,
            actor_user_id.bytes,
            shop_id.bytes,
            shop_customer_id.as_uuid().bytes,
            str(original_amount.value).encode("ascii"),
            str(discount_basis_points.value).encode("ascii"),
            due_date.isoformat().encode("ascii"),
        )
    )
    return CreateDebtRequestHash(hashlib.sha256(encoded).hexdigest())


def _length_safe_encode(parts: tuple[bytes, ...]) -> bytes:
    return b"".join(len(part).to_bytes(4, byteorder="big") + part for part in parts)


def _require_sha256_hex(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_HEX_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256 hex")
