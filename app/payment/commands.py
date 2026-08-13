"""Server-authoritative raw-input assembly for M14 payment creation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from app.auth.error_codes import ErrorCode
from app.debt.enums import DebtBalanceBasis
from app.debt.values import DebtId, DebtRevision, ShopId, UserId
from app.idempotency.contracts import (
    CanonicalIdempotencyKey,
    CreatePaymentRequestHash,
    VoidPaymentRequestHash,
    create_void_payment_request_hash_v1,
    parse_idempotency_key,
    require_matching_idempotency_keys,
)
from app.payment.contracts import (
    create_payment_request_hash,
    create_payment_request_hash_v1,
    create_payment_request_hash_v2,
)
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.enums import (
    PaymentMethod,
    PaymentVoidOutcome,
    PaymentVoidReason,
    parse_payment_method,
    parse_payment_void_reason,
)
from app.payment.values import PaymentAmountUZS, PaymentId, parse_payment_amount_uzs

__all__ = (
    "CreatePaymentCommand",
    "CreatePaymentCommandAssembly",
    "CreatePaymentRawForm",
    "CreatePaymentV2RawForm",
    "CreatePaymentV2Command",
    "CompletedM14PaymentReplayCandidate",
    "CreatePaymentRequestAssembly",
    "VoidPaymentCommand",
    "VoidPaymentCommandAssembly",
    "VoidPaymentFailure",
    "VoidPaymentMutationResult",
    "VoidPaymentRawForm",
    "assemble_create_payment_command",
    "assemble_create_payment_request",
    "assemble_void_payment_command",
)

_CANONICAL_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*", flags=re.ASCII)


class VoidPaymentFailure(StrEnum):
    UNAVAILABLE = "PAYMENT_UNAVAILABLE"
    NOT_VOIDABLE = "PAYMENT_NOT_VOIDABLE"
    CHANGED = "DEBT_CHANGED"
    INVALID_INPUT = "VALIDATION_ERROR"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True, slots=True, repr=False)
class VoidPaymentRawForm:
    """Only normalized browser fields; target identity stays server-derived."""

    expected_revision: str = field(repr=False)
    reason: str | None = field(repr=False)
    idempotency_key: str | None = field(repr=False)
    confirmed: str | None = field(repr=False)

    def __repr__(self) -> str:
        return "VoidPaymentRawForm(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class VoidPaymentCommand:
    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)
    payment_id: PaymentId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    expected_revision: DebtRevision = field(repr=False)
    reason: PaymentVoidReason = field(repr=False)
    idempotency_key: CanonicalIdempotencyKey = field(repr=False)
    request_hash: VoidPaymentRequestHash = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor_user_id, UUID):
            raise ValueError("Void-payment command actor is invalid")
        if not isinstance(self.current_shop_id, UUID):
            raise ValueError("Void-payment command shop is invalid")
        if not isinstance(self.payment_id, PaymentId):
            raise ValueError("Void-payment command Payment is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Void-payment command Debt is invalid")
        if not isinstance(self.expected_revision, DebtRevision):
            raise ValueError("Void-payment command revision is invalid")
        if not isinstance(self.reason, PaymentVoidReason):
            raise ValueError("Void-payment command reason is invalid")
        if not isinstance(self.idempotency_key, CanonicalIdempotencyKey):
            raise ValueError("Void-payment command key is invalid")
        expected_hash = create_void_payment_request_hash_v1(
            actor_user_id=self.actor_user_id,
            shop_id=self.current_shop_id,
            payment_id=self.payment_id,
            debt_id=self.debt_id,
            expected_revision=self.expected_revision,
            reason=self.reason,
        )
        if self.request_hash != expected_hash:
            raise ValueError("Void-payment command request hash is invalid")

    def __repr__(self) -> str:
        return "VoidPaymentCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class VoidPaymentCommandAssembly:
    command: VoidPaymentCommand | None = field(default=None, repr=False)
    failure: VoidPaymentFailure | None = None

    def __post_init__(self) -> None:
        if (self.command is None) == (self.failure is None):
            raise ValueError("Void-payment command assembly is invalid")
        if self.failure not in {None, VoidPaymentFailure.INVALID_INPUT}:
            raise ValueError("Void-payment command assembly failure is invalid")

    def __repr__(self) -> str:
        return (
            f"VoidPaymentCommandAssembly(failure={self.failure!r}, command=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class VoidPaymentMutationResult:
    outcome: PaymentVoidOutcome
    payment_id: PaymentId = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, PaymentVoidOutcome):
            raise ValueError("Void-payment mutation outcome is invalid")
        if not isinstance(self.payment_id, PaymentId):
            raise ValueError("Void-payment mutation Payment is invalid")

    def __repr__(self) -> str:
        return (
            f"VoidPaymentMutationResult(outcome={self.outcome.value!r}, "
            "payment_id=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CreatePaymentRawForm:
    """The only client-originated fields permitted at the payment boundary."""

    debt_id: str = field(repr=False)
    amount_uzs: str = field(repr=False)
    method: str = field(repr=False)
    idempotency_key: str | None = field(repr=False)
    expected_revision: str = field(repr=False)

    def __repr__(self) -> str:
        return "CreatePaymentRawForm(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CreatePaymentV2RawForm:
    """M15 boundary form; missing basis is legacy replay input, never mutation."""

    debt_id: str = field(repr=False)
    amount_uzs: str = field(repr=False)
    method: str = field(repr=False)
    idempotency_key: str | None = field(repr=False)
    expected_revision: str = field(repr=False)
    expected_balance_basis: str | None = field(repr=False)

    def __repr__(self) -> str:
        return "CreatePaymentV2RawForm(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CreatePaymentCommand:
    """Canonical typed command; tenancy and actor identity are server injected."""

    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    amount: PaymentAmountUZS = field(repr=False)
    method: PaymentMethod
    idempotency_key: CanonicalIdempotencyKey = field(repr=False)
    expected_revision: DebtRevision
    request_hash: CreatePaymentRequestHash = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor_user_id, UUID):
            raise ValueError("Payment command actor is invalid")
        if not isinstance(self.current_shop_id, UUID):
            raise ValueError("Payment command shop is invalid")
        if not isinstance(self.debt_id, DebtId):
            raise ValueError("Payment command debt is invalid")
        if not isinstance(self.amount, PaymentAmountUZS):
            raise ValueError("Payment command amount is invalid")
        if not isinstance(self.method, PaymentMethod):
            raise ValueError("Payment command method is invalid")
        if not isinstance(self.idempotency_key, CanonicalIdempotencyKey):
            raise ValueError("Payment command idempotency key is invalid")
        if not isinstance(self.expected_revision, DebtRevision):
            raise ValueError("Payment command revision is invalid")
        if not isinstance(self.request_hash, CreatePaymentRequestHash):
            raise ValueError("Payment command request hash is invalid")
        expected_hash = create_payment_request_hash(
            actor_user_id=self.actor_user_id,
            shop_id=self.current_shop_id,
            debt_id=self.debt_id,
            amount=self.amount,
            method=self.method,
            expected_revision=self.expected_revision,
        )
        if self.request_hash != expected_hash:
            raise ValueError("Payment command request hash is invalid")

    def __repr__(self) -> str:
        return (
            "CreatePaymentCommand(actor_user_id=<redacted>, "
            "current_shop_id=<redacted>, debt_id=<redacted>, amount=<redacted>, "
            f"method={self.method.value!r}, idempotency_key=<redacted>, "
            f"expected_revision={self.expected_revision.value!r}, "
            "request_hash=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CreatePaymentV2Command:
    """Typed M15 mutation command with explicit stale balance basis."""

    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    amount: PaymentAmountUZS = field(repr=False)
    method: PaymentMethod
    idempotency_key: CanonicalIdempotencyKey = field(repr=False)
    expected_revision: DebtRevision
    expected_balance_basis: DebtBalanceBasis
    request_hash: CreatePaymentRequestHash = field(repr=False)

    def __post_init__(self) -> None:
        _require_common_command_fields(
            actor_user_id=self.actor_user_id,
            current_shop_id=self.current_shop_id,
            debt_id=self.debt_id,
            amount=self.amount,
            method=self.method,
            idempotency_key=self.idempotency_key,
            expected_revision=self.expected_revision,
        )
        if not isinstance(self.expected_balance_basis, DebtBalanceBasis):
            raise ValueError("Payment command balance basis is invalid")
        if not isinstance(self.request_hash, CreatePaymentRequestHash):
            raise ValueError("Payment command request hash is invalid")
        expected_hash = create_payment_request_hash_v2(
            actor_user_id=self.actor_user_id,
            shop_id=self.current_shop_id,
            debt_id=self.debt_id,
            amount=self.amount,
            method=self.method,
            expected_revision=self.expected_revision,
            expected_balance_basis=self.expected_balance_basis,
        )
        if self.request_hash != expected_hash:
            raise ValueError("Payment command request hash is invalid")

    def __repr__(self) -> str:
        return (
            "CreatePaymentV2Command(actor_user_id=<redacted>, "
            "current_shop_id=<redacted>, debt_id=<redacted>, amount=<redacted>, "
            f"method={self.method.value!r}, idempotency_key=<redacted>, "
            f"expected_revision={self.expected_revision.value!r}, "
            f"expected_balance_basis={self.expected_balance_basis.value!r}, "
            "request_hash=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CompletedM14PaymentReplayCandidate:
    """Typed v1 lookup input which cannot be passed to a mutation service."""

    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)
    debt_id: DebtId = field(repr=False)
    amount: PaymentAmountUZS = field(repr=False)
    method: PaymentMethod
    idempotency_key: CanonicalIdempotencyKey = field(repr=False)
    expected_revision: DebtRevision
    request_hash: CreatePaymentRequestHash = field(repr=False)

    def __post_init__(self) -> None:
        _require_common_command_fields(
            actor_user_id=self.actor_user_id,
            current_shop_id=self.current_shop_id,
            debt_id=self.debt_id,
            amount=self.amount,
            method=self.method,
            idempotency_key=self.idempotency_key,
            expected_revision=self.expected_revision,
        )
        if self.request_hash != create_payment_request_hash_v1(
            actor_user_id=self.actor_user_id,
            shop_id=self.current_shop_id,
            debt_id=self.debt_id,
            amount=self.amount,
            method=self.method,
            expected_revision=self.expected_revision,
        ):
            raise ValueError("Legacy payment replay request hash is invalid")

    def __repr__(self) -> str:
        return "CompletedM14PaymentReplayCandidate(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CreatePaymentRequestAssembly:
    command: CreatePaymentV2Command | None = field(default=None, repr=False)
    legacy_completed_replay: CompletedM14PaymentReplayCandidate | None = field(
        default=None, repr=False
    )
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        populated = sum(
            value is not None
            for value in (self.command, self.legacy_completed_replay, self.error)
        )
        if populated != 1:
            raise ValueError("Payment request assembly is invalid")
        if self.error is not None and self.error is not ErrorCode.VALIDATION_ERROR:
            raise ValueError("Payment request assembly error is invalid")

    def __repr__(self) -> str:
        return (
            f"CreatePaymentRequestAssembly(error={self.error!r}, "
            "command=<redacted>, legacy_completed_replay=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CreatePaymentCommandAssembly:
    """A public, localized-catalog-ready command result without raw diagnostics."""

    command: CreatePaymentCommand | None = field(default=None, repr=False)
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if (self.command is None) == (self.error is None):
            raise ValueError("Payment command assembly is invalid")
        if self.error is not None and self.error is not ErrorCode.VALIDATION_ERROR:
            raise ValueError("Payment command assembly error is invalid")

    @classmethod
    def valid(cls, command: CreatePaymentCommand) -> CreatePaymentCommandAssembly:
        return cls(command=command)

    @classmethod
    def invalid(cls) -> CreatePaymentCommandAssembly:
        return cls(error=ErrorCode.VALIDATION_ERROR)

    def __repr__(self) -> str:
        return f"CreatePaymentCommandAssembly(error={self.error!r}, command=<redacted>)"


def assemble_create_payment_command(
    *,
    actor: DetachedPaymentActorContext,
    form: CreatePaymentRawForm,
    header_idempotency_key: str | None,
) -> CreatePaymentCommandAssembly:
    """Parse canonical client values and inject the detached server context.

    All client parse/match failures intentionally collapse into the existing
    localized ``VALIDATION_ERROR`` catalog entry.  A later coordinator receives
    only a successful typed command, never raw form/header data.
    """

    if not isinstance(actor, DetachedPaymentActorContext):
        raise TypeError("actor must be a DetachedPaymentActorContext")
    if not isinstance(form, CreatePaymentRawForm):
        raise TypeError("form must be a CreatePaymentRawForm")
    try:
        debt_id = _parse_debt_locator(form.debt_id)
        amount = parse_payment_amount_uzs(form.amount_uzs)
        method = parse_payment_method(form.method)
        idempotency_key = require_matching_idempotency_keys(
            form_value=form.idempotency_key,
            header_value=header_idempotency_key,
        )
        expected_revision = _parse_expected_revision(form.expected_revision)
        request_hash = create_payment_request_hash(
            actor_user_id=UserId(actor.actor_user_id),
            shop_id=ShopId(actor.current_shop_id),
            debt_id=debt_id,
            amount=amount,
            method=method,
            expected_revision=expected_revision,
        )
        return CreatePaymentCommandAssembly.valid(
            CreatePaymentCommand(
                actor_user_id=UserId(actor.actor_user_id),
                current_shop_id=ShopId(actor.current_shop_id),
                debt_id=debt_id,
                amount=amount,
                method=method,
                idempotency_key=idempotency_key,
                expected_revision=expected_revision,
                request_hash=request_hash,
            )
        )
    except (TypeError, ValueError, OverflowError):
        return CreatePaymentCommandAssembly.invalid()


def assemble_create_payment_request(
    *,
    actor: DetachedPaymentActorContext,
    form: CreatePaymentV2RawForm,
    header_idempotency_key: str | None,
) -> CreatePaymentRequestAssembly:
    """Return exactly a v2 mutation or a completed-v1 replay lookup candidate."""

    if not isinstance(actor, DetachedPaymentActorContext):
        raise TypeError("actor must be a DetachedPaymentActorContext")
    if not isinstance(form, CreatePaymentV2RawForm):
        raise TypeError("form must be a CreatePaymentV2RawForm")
    try:
        actor_user_id = UserId(actor.actor_user_id)
        current_shop_id = ShopId(actor.current_shop_id)
        debt_id = _parse_debt_locator(form.debt_id)
        amount = parse_payment_amount_uzs(form.amount_uzs)
        method = parse_payment_method(form.method)
        idempotency_key = require_matching_idempotency_keys(
            form_value=form.idempotency_key,
            header_value=header_idempotency_key,
        )
        expected_revision = _parse_expected_revision(form.expected_revision)
        common = {
            "actor_user_id": actor_user_id,
            "current_shop_id": current_shop_id,
            "debt_id": debt_id,
            "amount": amount,
            "method": method,
            "idempotency_key": idempotency_key,
            "expected_revision": expected_revision,
        }
        if form.expected_balance_basis is None:
            request_hash = create_payment_request_hash_v1(
                actor_user_id=actor_user_id,
                shop_id=current_shop_id,
                debt_id=debt_id,
                amount=amount,
                method=method,
                expected_revision=expected_revision,
            )
            return CreatePaymentRequestAssembly(
                legacy_completed_replay=CompletedM14PaymentReplayCandidate(
                    **common,
                    request_hash=request_hash,
                )
            )
        expected_balance_basis = DebtBalanceBasis(form.expected_balance_basis)
        request_hash = create_payment_request_hash_v2(
            actor_user_id=actor_user_id,
            shop_id=current_shop_id,
            debt_id=debt_id,
            amount=amount,
            method=method,
            expected_revision=expected_revision,
            expected_balance_basis=expected_balance_basis,
        )
        return CreatePaymentRequestAssembly(
            command=CreatePaymentV2Command(
                **common,
                expected_balance_basis=expected_balance_basis,
                request_hash=request_hash,
            )
        )
    except (TypeError, ValueError, OverflowError):
        return CreatePaymentRequestAssembly(error=ErrorCode.VALIDATION_ERROR)


def assemble_void_payment_command(
    *,
    actor_user_id: UserId,
    current_shop_id: ShopId,
    payment_id: PaymentId,
    server_resolved_debt_id: DebtId,
    raw: VoidPaymentRawForm,
) -> VoidPaymentCommandAssembly:
    """Build a tenant-bound command from server identity and closed raw fields."""

    if not isinstance(raw, VoidPaymentRawForm):
        raise TypeError("raw must be a VoidPaymentRawForm")
    try:
        if not isinstance(actor_user_id, UUID):
            raise ValueError("Void-payment actor is invalid")
        if not isinstance(current_shop_id, UUID):
            raise ValueError("Void-payment Shop is invalid")
        if not isinstance(payment_id, PaymentId):
            raise ValueError("Void-payment Payment is invalid")
        if not isinstance(server_resolved_debt_id, DebtId):
            raise ValueError("Void-payment server-resolved Debt is invalid")
        if raw.confirmed != "yes":
            raise ValueError("Void-payment confirmation is invalid")
        expected_revision = _parse_expected_revision(raw.expected_revision)
        reason = parse_payment_void_reason(raw.reason)  # type: ignore[arg-type]
        key = parse_idempotency_key(raw.idempotency_key)  # type: ignore[arg-type]
        request_hash = create_void_payment_request_hash_v1(
            actor_user_id=actor_user_id,
            shop_id=current_shop_id,
            payment_id=payment_id,
            debt_id=server_resolved_debt_id,
            expected_revision=expected_revision,
            reason=reason,
        )
        return VoidPaymentCommandAssembly(
            command=VoidPaymentCommand(
                actor_user_id=actor_user_id,
                current_shop_id=current_shop_id,
                payment_id=payment_id,
                debt_id=server_resolved_debt_id,
                expected_revision=expected_revision,
                reason=reason,
                idempotency_key=key,
                request_hash=request_hash,
            )
        )
    except (TypeError, ValueError, OverflowError):
        return VoidPaymentCommandAssembly(failure=VoidPaymentFailure.INVALID_INPUT)


def _require_common_command_fields(
    *,
    actor_user_id: UserId,
    current_shop_id: ShopId,
    debt_id: DebtId,
    amount: PaymentAmountUZS,
    method: PaymentMethod,
    idempotency_key: CanonicalIdempotencyKey,
    expected_revision: DebtRevision,
) -> None:
    if not isinstance(actor_user_id, UUID):
        raise ValueError("Payment command actor is invalid")
    if not isinstance(current_shop_id, UUID):
        raise ValueError("Payment command shop is invalid")
    if not isinstance(debt_id, DebtId):
        raise ValueError("Payment command debt is invalid")
    if not isinstance(amount, PaymentAmountUZS):
        raise ValueError("Payment command amount is invalid")
    if not isinstance(method, PaymentMethod):
        raise ValueError("Payment command method is invalid")
    if not isinstance(idempotency_key, CanonicalIdempotencyKey):
        raise ValueError("Payment command idempotency key is invalid")
    if not isinstance(expected_revision, DebtRevision):
        raise ValueError("Payment command revision is invalid")


def _parse_debt_locator(value: str) -> DebtId:
    if not isinstance(value, str):
        raise ValueError("Debt locator is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Debt locator is invalid") from None
    if str(parsed) != value:
        raise ValueError("Debt locator is invalid")
    return DebtId(parsed)


def _parse_expected_revision(value: str) -> DebtRevision:
    if (
        not isinstance(value, str)
        or _CANONICAL_POSITIVE_INTEGER.fullmatch(value) is None
    ):
        raise ValueError("Expected payment revision is invalid")
    return DebtRevision(int(value))
