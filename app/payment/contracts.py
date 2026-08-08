"""Immutable, privacy-bounded Payment and receipt contracts."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.debt.business_time import normalize_payment_created_at
from app.debt.enums import M14_PERSISTED_STATUSES, DebtStatus
from app.debt.values import DebtId, DebtRevision, UserId
from app.idempotency.contracts import (
    CompletedIdempotencyResult,
    CreatePaymentRequestHash,
    IdempotencyResultType,
)
from app.payment.enums import PaymentMethod
from app.payment.values import PaymentAmountUZS, PaymentId, RemainingDueUZS
from app.shop.values import ShopId

__all__ = (
    "PaymentAggregate",
    "PaymentHistoryItem",
    "PaymentProjection",
    "PaymentReceiptProjection",
    "create_payment_request_hash",
    "payment_id_from_completed_result",
)

_CREATE_PAYMENT_HASH_DOMAIN = b"nasiya.m14.create-payment.request.v1"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentAggregate:
    """Trusted immutable payment state; identifiers remain private to adapters."""

    id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    recorded_by_user_id: UserId = field(repr=False)
    amount: PaymentAmountUZS
    method: PaymentMethod
    debt_revision_after: DebtRevision
    created_at: datetime

    def __post_init__(self) -> None:
        _require_payment_id(self.id)
        _require_debt_id(self.debt_id)
        _require_user_id(self.recorded_by_user_id)
        _require_amount(self.amount)
        _require_method(self.method)
        _require_revision(self.debt_revision_after)
        object.__setattr__(
            self,
            "created_at",
            normalize_payment_created_at(self.created_at),
        )

    def to_projection(self) -> PaymentProjection:
        return PaymentProjection(
            payment_id=self.id,
            debt_id=self.debt_id,
            amount=self.amount,
            method=self.method,
            debt_revision_after=self.debt_revision_after,
            created_at=self.created_at,
        )

    def to_history_item(self) -> PaymentHistoryItem:
        return PaymentHistoryItem(
            payment_id=self.id,
            amount=self.amount,
            method=self.method,
            debt_revision_after=self.debt_revision_after,
            created_at=self.created_at,
        )

    def __repr__(self) -> str:
        return "PaymentAggregate(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentProjection:
    """Identifier-safe payment projection for trusted read adapters."""

    payment_id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    amount: PaymentAmountUZS
    method: PaymentMethod
    debt_revision_after: DebtRevision
    created_at: datetime

    def __post_init__(self) -> None:
        _require_payment_id(self.payment_id)
        _require_debt_id(self.debt_id)
        _require_amount(self.amount)
        _require_method(self.method)
        _require_revision(self.debt_revision_after)
        object.__setattr__(
            self,
            "created_at",
            normalize_payment_created_at(self.created_at),
        )

    def __repr__(self) -> str:
        return "PaymentProjection(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentHistoryItem:
    """One revision-ordered payment fact without actor or raw identifiers."""

    payment_id: PaymentId = field(repr=False)
    amount: PaymentAmountUZS
    method: PaymentMethod
    debt_revision_after: DebtRevision
    created_at: datetime

    def __post_init__(self) -> None:
        _require_payment_id(self.payment_id)
        _require_amount(self.amount)
        _require_method(self.method)
        _require_revision(self.debt_revision_after)
        object.__setattr__(
            self,
            "created_at",
            normalize_payment_created_at(self.created_at),
        )

    def __repr__(self) -> str:
        return "PaymentHistoryItem(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentReceiptProjection:
    """Safe immutable payment fact plus explicitly current derived balances."""

    amount: PaymentAmountUZS
    method: PaymentMethod
    created_at: datetime
    historical_balance_after: RemainingDueUZS
    current_balance: RemainingDueUZS
    current_debt_status: DebtStatus
    shop_display_name: str = field(repr=False)

    def __post_init__(self) -> None:
        _require_amount(self.amount)
        _require_method(self.method)
        _require_remaining_due(
            self.historical_balance_after, field_name="Historical balance"
        )
        _require_remaining_due(self.current_balance, field_name="Current balance")
        if (
            not isinstance(self.current_debt_status, DebtStatus)
            or self.current_debt_status not in M14_PERSISTED_STATUSES
        ):
            raise ValueError("Receipt debt status is outside the M14 persisted subset")
        object.__setattr__(
            self,
            "created_at",
            normalize_payment_created_at(self.created_at),
        )
        object.__setattr__(
            self,
            "shop_display_name",
            _normalize_shop_display_name(self.shop_display_name),
        )

    def __repr__(self) -> str:
        return "PaymentReceiptProjection(<safe>)"


def create_payment_request_hash(
    *,
    actor_user_id: UserId,
    shop_id: ShopId,
    debt_id: DebtId,
    amount: PaymentAmountUZS,
    method: PaymentMethod,
    expected_revision: DebtRevision,
) -> CreatePaymentRequestHash:
    if not isinstance(actor_user_id, UUID) or not isinstance(shop_id, UUID):
        raise ValueError("Create-payment request identity is invalid")
    _require_debt_id(debt_id)
    _require_amount(amount)
    _require_method(method)
    _require_revision(expected_revision)
    encoded = _length_safe_encode(
        (
            _CREATE_PAYMENT_HASH_DOMAIN,
            actor_user_id.bytes,
            shop_id.bytes,
            debt_id.as_uuid().bytes,
            str(amount.value).encode("ascii"),
            method.value.encode("ascii"),
            str(expected_revision.value).encode("ascii"),
        )
    )
    return CreatePaymentRequestHash(hashlib.sha256(encoded).hexdigest())


def payment_id_from_completed_result(
    result: CompletedIdempotencyResult,
) -> PaymentId:
    if not isinstance(result, CompletedIdempotencyResult):
        raise ValueError("Completed payment idempotency result is invalid")
    return PaymentId(
        result.require_result_object_id(expected_type=IdempotencyResultType.PAYMENT)
    )


def _require_payment_id(value: PaymentId) -> None:
    if not isinstance(value, PaymentId):
        raise ValueError("Payment ID is invalid")


def _require_debt_id(value: DebtId) -> None:
    if not isinstance(value, DebtId):
        raise ValueError("Payment debt ID is invalid")


def _require_user_id(value: UserId) -> None:
    if not isinstance(value, UUID):
        raise ValueError("Payment recorded-by user ID is invalid")


def _require_amount(value: PaymentAmountUZS) -> None:
    if not isinstance(value, PaymentAmountUZS):
        raise ValueError("Payment amount is invalid")


def _require_method(value: PaymentMethod) -> None:
    if not isinstance(value, PaymentMethod):
        raise ValueError("Payment method is invalid")


def _require_revision(value: DebtRevision) -> None:
    if not isinstance(value, DebtRevision):
        raise ValueError("Payment debt revision is invalid")


def _require_remaining_due(value: RemainingDueUZS, *, field_name: str) -> None:
    if not isinstance(value, RemainingDueUZS):
        raise ValueError(f"{field_name} is invalid")


def _normalize_shop_display_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Receipt shop display name is invalid")
    normalized = " ".join(value.split())
    if not 2 <= len(normalized) <= 120:
        raise ValueError("Receipt shop display name is invalid")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise ValueError("Receipt shop display name is invalid")
    return normalized


def _length_safe_encode(parts: tuple[bytes, ...]) -> bytes:
    return b"".join(len(part).to_bytes(4, byteorder="big") + part for part in parts)
