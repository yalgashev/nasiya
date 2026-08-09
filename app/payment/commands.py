"""Server-authoritative raw-input assembly for M14 payment creation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

from app.auth.error_codes import ErrorCode
from app.debt.values import DebtId, DebtRevision, ShopId, UserId
from app.idempotency.contracts import (
    CanonicalIdempotencyKey,
    CreatePaymentRequestHash,
    require_matching_idempotency_keys,
)
from app.payment.contracts import create_payment_request_hash
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.enums import PaymentMethod, parse_payment_method
from app.payment.values import PaymentAmountUZS, parse_payment_amount_uzs

__all__ = (
    "CreatePaymentCommand",
    "CreatePaymentCommandAssembly",
    "CreatePaymentRawForm",
    "assemble_create_payment_command",
)

_CANONICAL_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*", flags=re.ASCII)


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
