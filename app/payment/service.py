"""Caller-owned M15 orchestration for on-time and late Debt payments."""

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
from app.debt.business_time import tashkent_business_date
from app.debt.enums import DebtBalanceBasis, DebtOverdueSource
from app.debt.overdue_service import (
    append_pending_overdue_audits,
    materialize_locked_overdue_debt,
)
from app.debt.rating_ports import (
    LockedOverdueRatingAppendPort,
    OverdueRatingAppendOutcome,
    mark_locked_overdue_rating_source,
)
from app.debt.repository import (
    debt_aggregate_from_row,
    mark_locked_debt_transition_scope,
    update_locked_debt,
)
from app.debt.values import DebtId, DiscountedAmountUZS, OriginalAmountUZS
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
from app.payment.commands import (
    CompletedM14PaymentReplayCandidate,
    CreatePaymentV2Command,
)
from app.payment.contracts import PaymentAggregate, payment_id_from_completed_result
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.policy import (
    capture_payment_server_now,
    evaluate_locked_debt_payability,
)
from app.payment.rating_ports import (
    LockedPaymentRatingAppendPort,
    PaymentRatingAppendOutcome,
    PaymentRatingEligibility,
    PaymentRatingEligibilityFacts,
    PendingOnTimePaidRatingEffect,
)
from app.payment.repository import (
    SqlAlchemyLockedDebtPostedTotalReader,
    get_tenant_payment,
    insert_payment,
    posted_payment_total,
)
from app.payment.targeting import (
    LockedTenantPaymentDebt,
    discover_tenant_payment_target,
    lock_tenant_payment_debt,
    lock_tenant_payment_predecessors,
    recheck_tenant_payment_replay_authority,
    validate_locked_tenant_payment_debt,
)
from app.payment.values import (
    IncoherentPaymentLedgerError,
    PaymentAmountUZS,
    PaymentId,
    PostedPaymentTotalUZS,
    RemainingDueUZS,
    calculate_remaining_due_for_basis,
)
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "LockedPaymentBalance",
    "PaymentAmountOutcome",
    "PaymentMutationRejected",
    "RecordDebtPaymentResult",
    "decide_locked_payment_amount",
    "read_locked_payment_balance",
    "record_debt_payment",
    "resolve_completed_m14_payment_replay",
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
            ErrorCode.VALIDATION_ERROR,
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
    session: Session,
    *,
    locked_debt: LockedTenantPaymentDebt,
    balance_basis: DebtBalanceBasis,
) -> LockedPaymentBalance:
    """Re-read the ledger under the Debt lock and fail closed on bad/zero state."""

    locked = validate_locked_tenant_payment_debt(session, locked_debt)
    try:
        remaining = calculate_remaining_due_for_basis(
            basis=balance_basis,
            original_amount=OriginalAmountUZS(locked.row.original_amount_uzs),
            discounted_amount=DiscountedAmountUZS(locked.row.discounted_amount_uzs),
            posted_total=PostedPaymentTotalUZS(
                posted_payment_total(session, debt_id=DebtId(locked.row.id)).value
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
    command: CreatePaymentV2Command,
    rating_append_port: LockedPaymentRatingAppendPort,
    payment_clock: PaymentServerClock | None = None,
) -> RecordDebtPaymentResult:
    """Append one idempotent Payment/Debt/audit unit in the borrowed TX-B.

    ``PaymentMutationRejected`` and all persistence faults deliberately escape;
    callers must catch them only after their transaction context has rolled back.
    """

    _validate_service_inputs(actor=actor, command=command)
    if not isinstance(
        rating_append_port, LockedPaymentRatingAppendPort
    ) or not isinstance(rating_append_port, LockedOverdueRatingAppendPort):
        raise TypeError("rating_append_port must implement payment and overdue ports")
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
    assert payability.balance_basis is not None
    if command.expected_revision != debt.revision:
        raise PaymentMutationRejected(ErrorCode.DEBT_CHANGED)
    if command.expected_balance_basis is not payability.balance_basis:
        raise PaymentMutationRejected(ErrorCode.DEBT_CHANGED)

    overdue_effect = None
    overdue_rating_source = None
    if payability.requires_overdue_transition:
        overdue_rating_source = mark_locked_overdue_rating_source(
            session,
            customer_id=locked_debt.predecessors.customer_id,
            shop_customer_id=ShopCustomerId(locked_debt.row.shop_customer_id),
            debt_id=DebtId(locked_debt.row.id),
        )
        overdue_result = materialize_locked_overdue_debt(
            session,
            locked_debt=mark_locked_debt_transition_scope(
                session, locked_row=locked_debt.row
            ),
            now=payability.payment_created_at,
            source=DebtOverdueSource.INLINE_PAYMENT,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )
        overdue_effect = overdue_result.effect
        if overdue_effect is None:
            raise RuntimeError("Required overdue transition produced no source fact")
        debt = debt_aggregate_from_row(locked_debt.row)

    balance = read_locked_payment_balance(
        session,
        locked_debt=locked_debt,
        balance_basis=payability.balance_basis,
    )
    if balance.error is not None:
        raise PaymentMutationRejected(balance.error)
    assert balance.remaining is not None
    amount_outcome = decide_locked_payment_amount(
        amount=command.amount,
        remaining=balance.remaining,
    )

    updated_debt = debt.record_payment(
        payment_amount_uzs=command.amount.value,
        current_remaining_due_uzs=balance.remaining.value,
        expected_revision=debt.revision,
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
    update_locked_debt(session, row=locked_debt.row, debt=updated_debt)
    insert_payment(session, locked_debt=locked_debt.row, payment=payment)

    if overdue_effect is not None:
        assert overdue_rating_source is not None
        overdue_outcome = rating_append_port.append_pending_overdue(
            session,
            locked_source=overdue_rating_source,
            effect=overdue_effect.rating_effect,
        )
        if overdue_outcome is not OverdueRatingAppendOutcome.APPENDED:
            raise RuntimeError("Overdue rating source is inconsistent")
        append_pending_overdue_audits(session, effect=overdue_effect)
    else:
        assert debt.accepted_at is not None
        initial_facts = PaymentRatingEligibilityFacts(
            shop_customer_id=debt.shop_customer_id,
            pre_status=debt.status,
            post_status=updated_debt.status,
            payment_amount=command.amount,
            discounted_remaining=balance.remaining,
            original_amount=debt.original_amount,
            accepted_at=debt.accepted_at,
            payment_created_at=payment.created_at,
            due_date=debt.due_date,
            overdue_at=debt.overdue_at,
            overdue_revision=debt.overdue_revision,
        )
        eligibility = rating_append_port.evaluate_on_time_paid(initial_facts)
        if eligibility is PaymentRatingEligibility.AWARD:
            payment_business_date = tashkent_business_date(payment.created_at)
            daily_cap_used = rating_append_port.positive_daily_slot_used(
                session,
                locked_debt=locked_debt,
                payment_business_date=payment_business_date,
            )
            eligibility = rating_append_port.evaluate_on_time_paid(
                PaymentRatingEligibilityFacts(
                    shop_customer_id=debt.shop_customer_id,
                    pre_status=debt.status,
                    post_status=updated_debt.status,
                    payment_amount=command.amount,
                    discounted_remaining=balance.remaining,
                    original_amount=debt.original_amount,
                    accepted_at=debt.accepted_at,
                    payment_created_at=payment.created_at,
                    due_date=debt.due_date,
                    overdue_at=debt.overdue_at,
                    overdue_revision=debt.overdue_revision,
                    daily_cap_already_used=daily_cap_used,
                )
            )
            if eligibility is PaymentRatingEligibility.AWARD:
                rating_outcome = rating_append_port.append_pending_on_time_paid(
                    session,
                    locked_debt=locked_debt,
                    effect=PendingOnTimePaidRatingEffect(
                        event_id=uuid4(),
                        debt_id=debt.id,
                        shop_customer_id=debt.shop_customer_id,
                        payment_created_at=payment.created_at,
                        payment_business_date=payment_business_date,
                    ),
                )
                if rating_outcome not in {
                    PaymentRatingAppendOutcome.APPENDED,
                    PaymentRatingAppendOutcome.DAILY_CAP_ALREADY_USED,
                }:
                    raise RuntimeError("On-time rating source is inconsistent")
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
    command: CreatePaymentV2Command,
) -> RecordDebtPaymentResult:
    authority_error = recheck_tenant_payment_replay_authority(
        session,
        actor=actor,
    )
    if authority_error is not None:
        raise PaymentMutationRejected(authority_error)
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
        or scoped.payment.debt_revision_after
        not in _allowed_completed_revisions(command)
    ):
        raise PaymentMutationRejected(ErrorCode.PAYMENT_UNAVAILABLE)
    return RecordDebtPaymentResult(
        outcome=IdempotencyOutcome.REPLAY,
        payment_id=payment_id,
    )


def _validate_service_inputs(
    *, actor: DetachedPaymentActorContext, command: CreatePaymentV2Command
) -> None:
    if not isinstance(actor, DetachedPaymentActorContext):
        raise TypeError("actor must be a DetachedPaymentActorContext")
    if not isinstance(command, CreatePaymentV2Command):
        raise TypeError("command must be a CreatePaymentV2Command")
    if (
        command.actor_user_id != actor.actor_user_id
        or command.current_shop_id != actor.current_shop_id
    ):
        raise ValueError("Payment command does not match detached actor context")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def resolve_completed_m14_payment_replay(
    session: Session,
    *,
    actor: DetachedPaymentActorContext,
    candidate: CompletedM14PaymentReplayCandidate,
) -> RecordDebtPaymentResult:
    """Resolve only an already-completed v1 row; this path never owns a clock."""

    if not isinstance(actor, DetachedPaymentActorContext):
        raise TypeError("actor must be a DetachedPaymentActorContext")
    if not isinstance(candidate, CompletedM14PaymentReplayCandidate):
        raise TypeError("candidate must be a CompletedM14PaymentReplayCandidate")
    if (
        candidate.actor_user_id != actor.actor_user_id
        or candidate.current_shop_id != actor.current_shop_id
    ):
        raise ValueError("Replay candidate does not match detached actor context")
    row = find_completed_key(
        session,
        actor_user_id=actor.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
        key_digest=canonical_idempotency_key_digest(candidate.idempotency_key),
    )
    if row is None:
        raise PaymentMutationRejected(ErrorCode.VALIDATION_ERROR)
    authority_error = recheck_tenant_payment_replay_authority(session, actor=actor)
    if authority_error is not None:
        raise PaymentMutationRejected(authority_error)
    if not hmac.compare_digest(row.request_hash, candidate.request_hash.value):
        raise PaymentMutationRejected(ErrorCode.IDEMPOTENCY_CONFLICT)
    payment_id = payment_id_from_completed_result(
        completed_idempotency_result_from_row(row)
    )
    scoped = get_tenant_payment(
        session,
        shop_id=candidate.current_shop_id,
        payment_id=payment_id,
    )
    if (
        scoped is None
        or scoped.payment.debt_id != candidate.debt_id.as_uuid()
        or scoped.payment.recorded_by_user_id != actor.actor_user_id
        or scoped.payment.amount_uzs != candidate.amount.value
        or scoped.payment.method != candidate.method.value
        or scoped.payment.debt_revision_after != candidate.expected_revision.value + 1
    ):
        raise PaymentMutationRejected(ErrorCode.PAYMENT_UNAVAILABLE)
    return RecordDebtPaymentResult(
        outcome=IdempotencyOutcome.REPLAY,
        payment_id=payment_id,
    )


def _allowed_completed_revisions(command: CreatePaymentV2Command) -> set[int]:
    base = command.expected_revision.value
    if command.expected_balance_basis.value == "original":
        return {base + 1, base + 2}
    return {base + 1}
