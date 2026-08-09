"""Caller-owned atomic orchestration for M14 partial and full payments."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy.orm import Session

from app.audit.contracts import DebtPaidAuditPayload, PaymentRecordedAuditPayload
from app.audit.repository import append_debt_paid_audit, append_payment_recorded_audit
from app.auth.error_codes import ErrorCode
from app.debt.repository import debt_aggregate_from_row, update_locked_debt
from app.debt.values import DebtId, DiscountedAmountUZS
from app.idempotency.contracts import (
    IdempotencyEndpoint,
    IdempotencyOutcome,
    canonical_idempotency_key_digest,
)
from app.idempotency.models import IdempotencyKey
from app.idempotency.repository import (
    completed_idempotency_result_from_row,
    find_completed_key,
    insert_or_resolve_key,
)
from app.payment.commands import CreatePaymentCommand
from app.payment.contracts import PaymentAggregate, payment_id_from_completed_result
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.policy import (
    capture_payment_server_now,
    evaluate_locked_debt_payability,
)
from app.payment.repository import (
    get_tenant_payment,
    insert_payment,
    posted_payment_total,
)
from app.payment.targeting import (
    LockedTenantPaymentDebt,
    discover_tenant_payment_target,
    lock_tenant_payment_debt,
    lock_tenant_payment_predecessors,
    validate_locked_tenant_payment_debt,
)
from app.payment.values import (
    IncoherentPaymentLedgerError,
    PaymentAmountUZS,
    PaymentId,
    RemainingDueUZS,
    calculate_remaining_due,
)

__all__ = (
    "LockedPaymentBalance",
    "PaymentAmountOutcome",
    "PaymentMutationRejected",
    "RecordDebtPaymentResult",
    "decide_locked_payment_amount",
    "read_locked_payment_balance",
    "record_debt_payment",
)

PaymentServerClock = Callable[[], datetime]


class PaymentAmountOutcome(StrEnum):
    PARTIAL = "partial"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class LockedPaymentBalance:
    remaining: RemainingDueUZS | None = field(default=None, repr=False)
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if self.error is None:
            if not isinstance(self.remaining, RemainingDueUZS):
                raise ValueError("Available locked balance requires remaining due")
        elif self.error is not ErrorCode.DEBT_NOT_PAYABLE or self.remaining is not None:
            raise ValueError("Locked payment balance result is invalid")


class PaymentMutationRejected(RuntimeError):
    """Stable business rejection that must escape the caller-owned TX-B."""

    __slots__ = ("error",)

    def __init__(self, error: ErrorCode) -> None:
        if error not in {
            ErrorCode.DEBT_UNAVAILABLE,
            ErrorCode.DEBT_NOT_PAYABLE,
            ErrorCode.DEBT_CHANGED,
            ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            ErrorCode.PAYMENT_UNAVAILABLE,
            ErrorCode.FORBIDDEN,
            ErrorCode.SHOP_SUSPENDED,
        }:
            raise ValueError("Payment mutation error is invalid")
        self.error = error
        super().__init__(error.value)

    def __repr__(self) -> str:
        return f"PaymentMutationRejected(error={self.error.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class RecordDebtPaymentResult:
    outcome: IdempotencyOutcome
    payment_id: PaymentId = field(repr=False)

    def __post_init__(self) -> None:
        if self.outcome not in {IdempotencyOutcome.NEW, IdempotencyOutcome.REPLAY}:
            raise ValueError("Payment result outcome is invalid")
        if not isinstance(self.payment_id, PaymentId):
            raise ValueError("Payment result ID is invalid")

    def __repr__(self) -> str:
        return (
            f"RecordDebtPaymentResult(outcome={self.outcome.value!r}, "
            "payment_id=<redacted>)"
        )


def read_locked_payment_balance(
    session: Session, *, locked_debt: LockedTenantPaymentDebt
) -> LockedPaymentBalance:
    """Re-read the ledger under the Debt lock and fail closed on bad/zero state."""

    locked = validate_locked_tenant_payment_debt(session, locked_debt)
    try:
        remaining = calculate_remaining_due(
            discounted_amount=DiscountedAmountUZS(locked.row.discounted_amount_uzs),
            posted_total=posted_payment_total(
                session,
                debt_id=DebtId(locked.row.id),
            ),
        )
    except IncoherentPaymentLedgerError:
        return LockedPaymentBalance(error=ErrorCode.DEBT_NOT_PAYABLE)
    if remaining.value == 0:
        return LockedPaymentBalance(error=ErrorCode.DEBT_NOT_PAYABLE)
    return LockedPaymentBalance(remaining=remaining)


def decide_locked_payment_amount(
    *, amount: PaymentAmountUZS, remaining: RemainingDueUZS
) -> PaymentAmountOutcome:
    if not isinstance(amount, PaymentAmountUZS):
        raise TypeError("amount must be a PaymentAmountUZS")
    if not isinstance(remaining, RemainingDueUZS) or remaining.value <= 0:
        raise ValueError("remaining must be a positive RemainingDueUZS")
    if amount.value > remaining.value:
        raise PaymentMutationRejected(ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE)
    if amount.value == remaining.value:
        return PaymentAmountOutcome.FULL
    return PaymentAmountOutcome.PARTIAL


def record_debt_payment(
    session: Session,
    *,
    actor: DetachedPaymentActorContext,
    command: CreatePaymentCommand,
    payment_clock: PaymentServerClock | None = None,
) -> RecordDebtPaymentResult:
    """Append one idempotent Payment/Debt/audit unit in the borrowed TX-B.

    ``PaymentMutationRejected`` and all persistence faults deliberately escape;
    callers must catch them only after their transaction context has rolled back.
    """

    _validate_service_inputs(actor=actor, command=command)
    clock = payment_clock or _utc_now
    if not callable(clock):
        raise TypeError("payment_clock must be callable")
    key_digest = canonical_idempotency_key_digest(command.idempotency_key)
    completed = find_completed_key(
        session,
        actor_user_id=actor.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
        key_digest=key_digest,
    )
    if completed is not None:
        return _resolve_completed_payment(
            session,
            row=completed,
            actor=actor,
            command=command,
        )

    candidate = discover_tenant_payment_target(
        session,
        actor=actor,
        debt_id=command.debt_id,
    )
    target = lock_tenant_payment_predecessors(
        session,
        actor=actor,
        candidate=candidate,
    )
    if target.error is not None:
        raise PaymentMutationRejected(target.error)
    assert target.locked is not None

    payment_id = PaymentId(uuid4())
    key_result = insert_or_resolve_key(
        session,
        actor_user_id=actor.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
        key_digest=key_digest,
        request_hash=command.request_hash,
        result_object_id=payment_id.as_uuid(),
        now=None,
    )
    if key_result.outcome is IdempotencyOutcome.CONFLICT:
        raise PaymentMutationRejected(ErrorCode.IDEMPOTENCY_CONFLICT)
    if key_result.outcome is IdempotencyOutcome.REPLAY:
        assert key_result.row is not None
        return _resolve_completed_payment(
            session,
            row=key_result.row,
            actor=actor,
            command=command,
        )

    debt_lock = lock_tenant_payment_debt(session, predecessors=target.locked)
    if debt_lock.error is not None:
        raise PaymentMutationRejected(debt_lock.error)
    assert debt_lock.locked is not None
    locked_debt = debt_lock.locked
    debt = debt_aggregate_from_row(locked_debt.row)

    captured_now = capture_payment_server_now(clock())
    payability = evaluate_locked_debt_payability(
        debt=debt,
        captured_now=captured_now,
    )
    if payability.error is not None:
        raise PaymentMutationRejected(payability.error)

    balance = read_locked_payment_balance(session, locked_debt=locked_debt)
    if balance.error is not None:
        raise PaymentMutationRejected(balance.error)
    assert balance.remaining is not None
    if command.expected_revision != debt.revision:
        raise PaymentMutationRejected(ErrorCode.DEBT_CHANGED)
    amount_outcome = decide_locked_payment_amount(
        amount=command.amount,
        remaining=balance.remaining,
    )

    updated_debt = debt.record_payment(
        payment_amount_uzs=command.amount.value,
        current_remaining_due_uzs=balance.remaining.value,
        expected_revision=command.expected_revision,
        payment_created_at=payability.payment_created_at,
    )
    payment = PaymentAggregate(
        id=payment_id,
        debt_id=debt.id,
        recorded_by_user_id=actor.actor_user_id,
        amount=command.amount,
        method=command.method,
        debt_revision_after=updated_debt.revision,
        created_at=payability.payment_created_at,
    )
    insert_payment(session, locked_debt=locked_debt.row, payment=payment)
    update_locked_debt(session, row=locked_debt.row, debt=updated_debt)
    append_payment_recorded_audit(
        session,
        payment_id=payment_id.as_uuid(),
        actor_user_id=actor.actor_user_id,
        occurred_at=payment.created_at,
        payload=PaymentRecordedAuditPayload(
            amount=payment.amount,
            method=payment.method,
            from_status=debt.status,
            to_status=updated_debt.status,
            debt_revision_after=updated_debt.revision,
        ),
    )
    if amount_outcome is PaymentAmountOutcome.FULL:
        append_debt_paid_audit(
            session,
            debt_id=debt.id.as_uuid(),
            actor_user_id=actor.actor_user_id,
            occurred_at=payment.created_at,
            payload=DebtPaidAuditPayload(
                debt_revision_after=updated_debt.revision,
            ),
        )
    return RecordDebtPaymentResult(
        outcome=IdempotencyOutcome.NEW,
        payment_id=payment_id,
    )


def _resolve_completed_payment(
    session: Session,
    *,
    row: IdempotencyKey,
    actor: DetachedPaymentActorContext,
    command: CreatePaymentCommand,
) -> RecordDebtPaymentResult:
    if not hmac.compare_digest(row.request_hash, command.request_hash.value):
        raise PaymentMutationRejected(ErrorCode.IDEMPOTENCY_CONFLICT)
    payment_id = payment_id_from_completed_result(
        completed_idempotency_result_from_row(row)
    )
    scoped = get_tenant_payment(
        session,
        shop_id=command.current_shop_id,
        payment_id=payment_id,
    )
    if (
        scoped is None
        or scoped.payment.debt_id != command.debt_id.as_uuid()
        or scoped.payment.recorded_by_user_id != actor.actor_user_id
        or scoped.payment.amount_uzs != command.amount.value
        or scoped.payment.method != command.method.value
        or scoped.payment.debt_revision_after != command.expected_revision.value + 1
    ):
        raise PaymentMutationRejected(ErrorCode.PAYMENT_UNAVAILABLE)
    return RecordDebtPaymentResult(
        outcome=IdempotencyOutcome.REPLAY,
        payment_id=payment_id,
    )


def _validate_service_inputs(
    *, actor: DetachedPaymentActorContext, command: CreatePaymentCommand
) -> None:
    if not isinstance(actor, DetachedPaymentActorContext):
        raise TypeError("actor must be a DetachedPaymentActorContext")
    if not isinstance(command, CreatePaymentCommand):
        raise TypeError("command must be a CreatePaymentCommand")
    if (
        command.actor_user_id != actor.actor_user_id
        or command.current_shop_id != actor.current_shop_id
    ):
        raise ValueError("Payment command does not match detached actor context")


def _utc_now() -> datetime:
    return datetime.now(UTC)
