"""Immutable, privacy-bounded Payment and receipt contracts."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.debt.business_time import is_effectively_overdue, normalize_payment_created_at
from app.debt.enums import M17_PERSISTED_STATUSES, DebtBalanceBasis, DebtStatus
from app.debt.values import DebtId, DebtRevision, UserId
from app.idempotency.contracts import (
    CompletedIdempotencyResult,
    CreatePaymentRequestHash,
    IdempotencyResultType,
)
from app.payment.enums import PaymentMethod, PaymentVoidReason
from app.payment.values import (
    PaymentAmountUZS,
    PaymentId,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
)
from app.shop.values import ShopId
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "PaymentAggregate",
    "PaymentHistoryItem",
    "PaymentLedgerFact",
    "PaymentLedgerInvariantError",
    "PaymentNotVoidableError",
    "PaymentVoidAggregate",
    "PaymentProjection",
    "PaymentReceiptProjection",
    "IncoherentPaymentHistoryError",
    "create_payment_request_hash_v1",
    "create_payment_request_hash_v2",
    "create_payment_request_hash",
    "calculate_non_voided_posted_total",
    "latest_non_voided_payment",
    "resolve_current_balance_basis",
    "resolve_historical_balance_basis",
    "payment_id_from_completed_result",
    "require_latest_non_voided_payment",
)

_CREATE_PAYMENT_HASH_DOMAIN_V1 = b"nasiya.m14.create-payment.request.v1"
_CREATE_PAYMENT_HASH_DOMAIN_V2 = b"nasiya.m15.create-payment.request.v2"


class IncoherentPaymentHistoryError(ValueError):
    """Raised when a Payment revision collides with the overdue marker."""


class PaymentLedgerInvariantError(ValueError):
    """Raised when revision facts cannot describe one append-only Debt ledger."""


class PaymentNotVoidableError(ValueError):
    """Identifier-free denial for a missing, earlier, or already-voided target."""


@dataclass(frozen=True, slots=True, repr=False)
class PaymentLedgerFact:
    """One immutable Payment plus its optional append-only void revision."""

    payment_id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    amount: PaymentAmountUZS
    debt_revision_after: DebtRevision
    void_debt_revision_after: DebtRevision | None = None

    def __post_init__(self) -> None:
        _require_payment_id(self.payment_id)
        _require_debt_id(self.debt_id)
        _require_amount(self.amount)
        _require_revision(self.debt_revision_after)
        if self.void_debt_revision_after is not None:
            _require_revision(self.void_debt_revision_after)
            if self.void_debt_revision_after.value <= self.debt_revision_after.value:
                raise PaymentLedgerInvariantError(
                    "Payment void revision must follow its Payment revision"
                )

    @property
    def is_currently_non_voided(self) -> bool:
        return self.void_debt_revision_after is None

    def is_non_voided_at(self, revision: DebtRevision) -> bool:
        """Apply the frozen revision-as-of inclusion predicate."""

        _require_revision(revision)
        return self.debt_revision_after.value <= revision.value and (
            self.void_debt_revision_after is None
            or self.void_debt_revision_after.value > revision.value
        )

    def __repr__(self) -> str:
        return "PaymentLedgerFact(<redacted>)"


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
class PaymentVoidAggregate:
    """Trusted immutable PaymentVoid row without a public locator."""

    id: UUID = field(repr=False)
    payment_id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    source_payment_revision: DebtRevision
    debt_revision_after: DebtRevision
    voided_by_user_id: UserId = field(repr=False)
    reason: PaymentVoidReason = field(repr=False)
    voided_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("PaymentVoid identity is invalid")
        _require_payment_id(self.payment_id)
        _require_debt_id(self.debt_id)
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("PaymentVoid ShopCustomer is invalid")
        _require_revision(self.source_payment_revision)
        _require_revision(self.debt_revision_after)
        if self.source_payment_revision.value >= self.debt_revision_after.value:
            raise ValueError("PaymentVoid revision chain is invalid")
        _require_user_id(self.voided_by_user_id)
        if not isinstance(self.reason, PaymentVoidReason):
            raise ValueError("PaymentVoid reason is invalid")
        object.__setattr__(
            self,
            "voided_at",
            normalize_payment_created_at(self.voided_at),
        )

    def __repr__(self) -> str:
        return "PaymentVoidAggregate(<redacted>)"


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
    voided_at: datetime | None = None

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
        if self.voided_at is not None:
            voided_at = normalize_payment_created_at(self.voided_at)
            if voided_at < self.created_at:
                raise ValueError("Payment void cannot precede Payment")
            object.__setattr__(self, "voided_at", voided_at)

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None

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
    historical_balance_basis: DebtBalanceBasis = DebtBalanceBasis.DISCOUNTED
    current_balance_basis: DebtBalanceBasis = DebtBalanceBasis.DISCOUNTED

    def __post_init__(self) -> None:
        _require_amount(self.amount)
        _require_method(self.method)
        _require_remaining_due(
            self.historical_balance_after, field_name="Historical balance"
        )
        _require_remaining_due(self.current_balance, field_name="Current balance")
        if (
            not isinstance(self.current_debt_status, DebtStatus)
            or self.current_debt_status not in M17_PERSISTED_STATUSES
        ):
            raise ValueError("Receipt debt status is outside the M17 persisted subset")
        if not isinstance(self.historical_balance_basis, DebtBalanceBasis):
            raise ValueError("Receipt historical balance basis is invalid")
        if not isinstance(self.current_balance_basis, DebtBalanceBasis):
            raise ValueError("Receipt current balance basis is invalid")
        if (
            self.current_debt_status
            in {
                DebtStatus.OVERDUE,
                DebtStatus.WRITTEN_OFF,
                DebtStatus.WRITTEN_OFF_SETTLED,
            }
            and self.current_balance_basis is not DebtBalanceBasis.ORIGINAL
        ):
            raise ValueError("Recovery receipt current balance must use original basis")
        if (
            self.current_debt_status is DebtStatus.WRITTEN_OFF_SETTLED
            and self.current_balance.value != 0
        ):
            raise ValueError("Settled write-off receipt balance must be zero")
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


def calculate_non_voided_posted_total(
    facts: Iterable[PaymentLedgerFact],
    *,
    as_of_revision: DebtRevision | None = None,
) -> PostedPaymentTotalUZS:
    """Sum current anti-join facts or apply the exact revision-as-of predicate."""

    validated = _validate_payment_ledger_facts(facts)
    if as_of_revision is not None:
        _require_revision(as_of_revision)
    included = (
        fact.is_currently_non_voided
        if as_of_revision is None
        else fact.is_non_voided_at(as_of_revision)
        for fact in validated
    )
    total = sum(
        (
            fact.amount.value
            for fact, is_included in zip(validated, included, strict=True)
            if is_included
        ),
        start=Decimal("0"),
    )
    return PostedPaymentTotalUZS(total)


def latest_non_voided_payment(
    facts: Iterable[PaymentLedgerFact],
) -> PaymentLedgerFact | None:
    """Return latest strictly by maximum current non-voided Debt revision."""

    validated = _validate_payment_ledger_facts(facts)
    current = tuple(fact for fact in validated if fact.is_currently_non_voided)
    if not current:
        return None
    return max(current, key=lambda fact: fact.debt_revision_after.value)


def require_latest_non_voided_payment(
    facts: Iterable[PaymentLedgerFact],
    *,
    target_payment_id: PaymentId,
) -> PaymentLedgerFact:
    """Resolve only the maximum non-voided revision with a generic denial."""

    _require_payment_id(target_payment_id)
    validated = _validate_payment_ledger_facts(facts)
    latest = latest_non_voided_payment(validated)
    if latest is None or latest.payment_id != target_payment_id:
        raise PaymentNotVoidableError("Payment is not voidable")
    return latest


def create_payment_request_hash(
    *,
    actor_user_id: UserId,
    shop_id: ShopId,
    debt_id: DebtId,
    amount: PaymentAmountUZS,
    method: PaymentMethod,
    expected_revision: DebtRevision,
) -> CreatePaymentRequestHash:
    """Retain the exact M14 v1 domain for completed legacy replay."""

    return create_payment_request_hash_v1(
        actor_user_id=actor_user_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount=amount,
        method=method,
        expected_revision=expected_revision,
    )


def create_payment_request_hash_v1(
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
            _CREATE_PAYMENT_HASH_DOMAIN_V1,
            actor_user_id.bytes,
            shop_id.bytes,
            debt_id.as_uuid().bytes,
            str(amount.value).encode("ascii"),
            method.value.encode("ascii"),
            str(expected_revision.value).encode("ascii"),
        )
    )
    return CreatePaymentRequestHash(hashlib.sha256(encoded).hexdigest())


def create_payment_request_hash_v2(
    *,
    actor_user_id: UserId,
    shop_id: ShopId,
    debt_id: DebtId,
    amount: PaymentAmountUZS,
    method: PaymentMethod,
    expected_revision: DebtRevision,
    expected_balance_basis: DebtBalanceBasis,
) -> CreatePaymentRequestHash:
    if not isinstance(actor_user_id, UUID) or not isinstance(shop_id, UUID):
        raise ValueError("Create-payment request identity is invalid")
    _require_debt_id(debt_id)
    _require_amount(amount)
    _require_method(method)
    _require_revision(expected_revision)
    if not isinstance(expected_balance_basis, DebtBalanceBasis):
        raise ValueError("Create-payment request balance basis is invalid")
    encoded = _length_safe_encode(
        (
            _CREATE_PAYMENT_HASH_DOMAIN_V2,
            actor_user_id.bytes,
            shop_id.bytes,
            debt_id.as_uuid().bytes,
            str(amount.value).encode("ascii"),
            method.value.encode("ascii"),
            str(expected_revision.value).encode("ascii"),
            expected_balance_basis.value.encode("ascii"),
        )
    )
    return CreatePaymentRequestHash(hashlib.sha256(encoded).hexdigest())


def resolve_historical_balance_basis(
    *,
    payment_revision: DebtRevision,
    overdue_revision: DebtRevision | None,
) -> DebtBalanceBasis:
    _require_revision(payment_revision)
    if overdue_revision is None:
        return DebtBalanceBasis.DISCOUNTED
    _require_revision(overdue_revision)
    if payment_revision.value < overdue_revision.value:
        return DebtBalanceBasis.DISCOUNTED
    if payment_revision.value > overdue_revision.value:
        return DebtBalanceBasis.ORIGINAL
    raise IncoherentPaymentHistoryError(
        "Payment revision cannot equal overdue revision"
    )


def resolve_current_balance_basis(
    *,
    status: DebtStatus,
    due_date: date,
    server_now: datetime,
    overdue_revision: DebtRevision | None,
) -> DebtBalanceBasis:
    if overdue_revision is not None:
        _require_revision(overdue_revision)
    if status is DebtStatus.ACTIVE:
        if overdue_revision is not None:
            raise IncoherentPaymentHistoryError(
                "Active debt cannot carry overdue revision"
            )
        return (
            DebtBalanceBasis.ORIGINAL
            if is_effectively_overdue(
                status=status,
                due_date=due_date,
                server_now=server_now,
            )
            else DebtBalanceBasis.DISCOUNTED
        )
    if status is DebtStatus.OVERDUE:
        if overdue_revision is None:
            raise IncoherentPaymentHistoryError(
                "Persisted overdue debt requires overdue revision"
            )
        return DebtBalanceBasis.ORIGINAL
    if status in {DebtStatus.WRITTEN_OFF, DebtStatus.WRITTEN_OFF_SETTLED}:
        if overdue_revision is None:
            raise IncoherentPaymentHistoryError(
                "Written-off debt requires overdue revision"
            )
        return DebtBalanceBasis.ORIGINAL
    if status is DebtStatus.PAID:
        return (
            DebtBalanceBasis.DISCOUNTED
            if overdue_revision is None
            else DebtBalanceBasis.ORIGINAL
        )
    raise ValueError("Debt status has no payable balance basis")


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


def _validate_payment_ledger_facts(
    facts: Iterable[PaymentLedgerFact],
) -> tuple[PaymentLedgerFact, ...]:
    try:
        validated = tuple(facts)
    except TypeError:
        raise TypeError("Payment ledger facts must be iterable") from None
    if any(not isinstance(fact, PaymentLedgerFact) for fact in validated):
        raise TypeError("Payment ledger contains an invalid fact")
    if not validated:
        return validated

    debt_id = validated[0].debt_id
    payment_ids: set[PaymentId] = set()
    payment_revisions: set[DebtRevision] = set()
    void_revisions: set[DebtRevision] = set()
    for fact in validated:
        if fact.debt_id != debt_id:
            raise PaymentLedgerInvariantError(
                "Payment ledger facts must belong to one Debt"
            )
        if fact.payment_id in payment_ids:
            raise PaymentLedgerInvariantError("Payment ledger repeats a Payment")
        if fact.debt_revision_after in payment_revisions:
            raise PaymentLedgerInvariantError(
                "Payment ledger repeats a Payment revision"
            )
        payment_ids.add(fact.payment_id)
        payment_revisions.add(fact.debt_revision_after)
        if fact.void_debt_revision_after is not None:
            if fact.void_debt_revision_after in void_revisions:
                raise PaymentLedgerInvariantError(
                    "Payment ledger repeats a void revision"
                )
            void_revisions.add(fact.void_debt_revision_after)
    if payment_revisions & void_revisions:
        raise PaymentLedgerInvariantError(
            "Payment and void facts cannot share a Debt revision"
        )
    for voided_fact in (
        fact for fact in validated if fact.void_debt_revision_after is not None
    ):
        assert voided_fact.void_debt_revision_after is not None
        void_revision = voided_fact.void_debt_revision_after.value
        non_voided_before = tuple(
            fact
            for fact in validated
            if fact.debt_revision_after.value < void_revision
            and (
                fact.void_debt_revision_after is None
                or fact.void_debt_revision_after.value >= void_revision
            )
        )
        if (
            not non_voided_before
            or max(
                non_voided_before,
                key=lambda fact: fact.debt_revision_after.value,
            )
            is not voided_fact
        ):
            raise PaymentLedgerInvariantError(
                "Payment void did not target the latest non-voided revision"
            )
    return validated


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
