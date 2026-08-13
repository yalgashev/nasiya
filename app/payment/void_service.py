"""Exactly-once, caller-transaction-owned Payment void orchestration."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.audit.contracts import (
    DebtReopenedAfterPaymentVoidAuditPayload,
    PaymentVoidedAuditPayload,
    create_debt_reopened_after_payment_void_audit_event,
    create_payment_voided_audit_event,
)
from app.audit.repository import append_audit_event
from app.auth.error_codes import ErrorCode
from app.debt.business_time import tashkent_business_date
from app.debt.contracts import reopen_debt_after_payment_void
from app.debt.enums import DebtBalanceBasis, DebtOverdueSource, DebtStatus
from app.debt.overdue_service import (
    PendingOverdueTransitionEffect,
    append_pending_overdue_audits,
)
from app.debt.rating_ports import (
    LockedOverdueRatingAppendPort,
    OverdueRatingAppendOutcome,
    PendingOverdueRatingEffect,
    mark_locked_overdue_rating_source,
)
from app.debt.repository import debt_aggregate_from_row, update_locked_debt
from app.debt.values import ClawbackIncreaseUZS
from app.idempotency.contracts import (
    IdempotencyEndpoint,
    IdempotencyOutcome,
    IdempotencyResultType,
    canonical_idempotency_key_digest,
)
from app.idempotency.repository import find_completed_key, insert_or_resolve_key
from app.payment.commands import VoidPaymentCommand, VoidPaymentMutationResult
from app.payment.contracts import PaymentVoidAggregate
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.enums import PaymentVoidOutcome
from app.payment.policy import capture_payment_server_now
from app.payment.rating_ports import (
    PaymentVoidCompensationAppendOutcome,
    PaymentVoidRatingAppendPort,
    PaymentVoidRatingSourceFacts,
    PaymentVoidRatingSourceReadPort,
)
from app.payment.repository import (
    get_tenant_payment,
    insert_payment_void,
    payment_void_exists_for_tenant_debt,
)
from app.payment.service import PaymentMutationRejected
from app.payment.values import calculate_payment_void_money
from app.payment.void_source import prove_locked_payment_void_source
from app.payment.void_targeting import (
    discover_tenant_payment_void_target,
    lock_tenant_payment_void_predecessors,
    lock_tenant_payment_void_target,
)

PaymentVoidClock = Callable[[], datetime]

__all__ = ("void_payment",)


def void_payment(
    session: Session,
    *,
    actor: DetachedPaymentActorContext,
    command: VoidPaymentCommand,
    rating_port: PaymentVoidRatingSourceReadPort,
    payment_void_clock: PaymentVoidClock | None = None,
) -> VoidPaymentMutationResult:
    """Append all void evidence atomically; the caller owns commit/rollback."""

    if not isinstance(actor, DetachedPaymentActorContext):
        raise TypeError("actor must be a DetachedPaymentActorContext")
    if not isinstance(command, VoidPaymentCommand):
        raise TypeError("command must be a VoidPaymentCommand")
    if (
        actor.actor_user_id != command.actor_user_id
        or actor.current_shop_id != command.current_shop_id
    ):
        raise PaymentMutationRejected(ErrorCode.PAYMENT_UNAVAILABLE)
    if not isinstance(rating_port, PaymentVoidRatingAppendPort) or not isinstance(
        rating_port, LockedOverdueRatingAppendPort
    ):
        raise TypeError("rating_port must implement void and overdue rating ports")
    clock = payment_void_clock or _utc_now
    if not callable(clock):
        raise TypeError("payment_void_clock must be callable")

    candidate = discover_tenant_payment_void_target(
        session, actor=actor, payment_id=command.payment_id
    )
    if candidate is None or candidate.debt_id != command.debt_id:
        raise PaymentMutationRejected(ErrorCode.PAYMENT_UNAVAILABLE)
    error, predecessors = lock_tenant_payment_void_predecessors(
        session, actor=actor, candidate=candidate
    )
    if error is not None:
        raise PaymentMutationRejected(error)
    assert predecessors is not None

    key_digest = canonical_idempotency_key_digest(command.idempotency_key)
    completed = find_completed_key(
        session,
        actor_user_id=actor.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_PAYMENTS_VOID,
        key_digest=key_digest,
    )
    if completed is not None:
        return _resolve_completed_void(
            session, row=completed, actor=actor, command=command
        )
    key_result = insert_or_resolve_key(
        session,
        actor_user_id=actor.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_PAYMENTS_VOID,
        key_digest=key_digest,
        request_hash=command.request_hash,
        result_object_id=command.payment_id.as_uuid(),
        now=None,
    )
    if key_result.outcome is IdempotencyOutcome.CONFLICT:
        raise PaymentMutationRejected(ErrorCode.IDEMPOTENCY_CONFLICT)
    if key_result.outcome is IdempotencyOutcome.REPLAY:
        assert key_result.row is not None
        return _resolve_completed_void(
            session, row=key_result.row, actor=actor, command=command
        )

    target_result = lock_tenant_payment_void_target(
        session, candidate=candidate, predecessors=predecessors
    )
    if target_result.error is not None:
        raise PaymentMutationRejected(target_result.error)
    assert target_result.locked is not None
    locked_target = target_result.locked
    try:
        source = prove_locked_payment_void_source(
            session,
            locked_target=locked_target,
            target_payment_id=command.payment_id,
            expected_debt_revision=command.expected_revision,
        )
    except ValueError as exc:
        raise PaymentMutationRejected(ErrorCode.PAYMENT_NOT_VOIDABLE) from exc

    debt_before = debt_aggregate_from_row(locked_target.locked_debt.row)
    source_token = rating_port.read_pre_transition_source(
        session,
        facts=PaymentVoidRatingSourceFacts(
            payment_id=source.payment.id,
            debt_id=source.debt_id,
            shop_customer_id=source.shop_customer_id,
            terminal_status=debt_before.status,
            payment_amount=source.payment.amount,
            original_amount=debt_before.original_amount,
            discounted_amount=debt_before.discounted_amount,
            as_of_payment_total=source.as_of_payment_total,
            accepted_at=debt_before.accepted_at,
            due_date=debt_before.due_date,
            overdue_revision=debt_before.overdue_revision,
            source_revision=source.source_revision,
            source_occurred_at=source.source_occurred_at,
        ),
    )
    if debt_before.status is DebtStatus.WRITTEN_OFF_SETTLED and source_token is None:
        raise RuntimeError("Payment rating source is inconsistent")

    voided_at = capture_payment_server_now(clock()).value
    provisional_basis = (
        DebtBalanceBasis.ORIGINAL
        if debt_before.status.value in {"overdue", "written_off", "written_off_settled"}
        or debt_before.overdue_revision is not None
        else DebtBalanceBasis.DISCOUNTED
    )
    money = calculate_payment_void_money(
        posted_total_before=source.current_total,
        target_amount=source.payment.amount,
        resulting_balance_basis=provisional_basis,
        original_amount=debt_before.original_amount,
        discounted_amount=debt_before.discounted_amount,
    )
    transition = reopen_debt_after_payment_void(
        debt=debt_before,
        expected_revision=command.expected_revision,
        payment_created_at=source.payment.created_at,
        voided_at=voided_at,
        remaining_due_uzs=money.remaining_due_after.value,
    )
    if transition.debt.status.value in {"overdue", "written_off"}:
        final_money = calculate_payment_void_money(
            posted_total_before=source.current_total,
            target_amount=source.payment.amount,
            resulting_balance_basis=DebtBalanceBasis.ORIGINAL,
            original_amount=debt_before.original_amount,
            discounted_amount=debt_before.discounted_amount,
        )
        if final_money.remaining_due_after.value != money.remaining_due_after.value:
            transition = reopen_debt_after_payment_void(
                debt=debt_before,
                expected_revision=command.expected_revision,
                payment_created_at=source.payment.created_at,
                voided_at=voided_at,
                remaining_due_uzs=final_money.remaining_due_after.value,
            )

    update_locked_debt(session, row=locked_target.locked_debt.row, debt=transition.debt)
    insert_payment_void(
        session,
        locked_debt=locked_target.locked_debt.row,
        payment_void=PaymentVoidAggregate(
            id=uuid4(),
            payment_id=source.payment.id,
            debt_id=source.debt_id,
            shop_customer_id=source.shop_customer_id,
            source_payment_revision=source.source_revision,
            debt_revision_after=transition.debt.revision,
            voided_by_user_id=actor.actor_user_id,
            reason=command.reason,
            voided_at=voided_at,
        ),
    )

    if source_token is not None:
        compensation = rating_port.append_source_compensation(
            session,
            source=source_token,
            voided_at=voided_at,
            completed_replay=False,
        )
        if compensation is not PaymentVoidCompensationAppendOutcome.APPENDED:
            raise RuntimeError("Payment rating compensation is inconsistent")

    if transition.overdue_effect is not None:
        locked_rating = mark_locked_overdue_rating_source(
            session,
            customer_id=predecessors.customer_id,
            shop_customer_id=source.shop_customer_id,
            debt_id=source.debt_id,
        )
        overdue = PendingOverdueTransitionEffect(
            rating_effect=PendingOverdueRatingEffect(
                event_id=uuid4(),
                debt_id=source.debt_id,
                shop_customer_id=source.shop_customer_id,
                overdue_at=voided_at,
                source_revision=transition.debt.revision,
            ),
            source=DebtOverdueSource.PAYMENT_VOID,
            overdue_revision=transition.debt.revision,
            balance_increase_uzs=ClawbackIncreaseUZS(
                debt_before.original_amount.value - debt_before.discounted_amount.value
            ),
            business_date=tashkent_business_date(voided_at),
        )
        if (
            rating_port.append_pending_overdue(
                session, locked_source=locked_rating, effect=overdue.rating_effect
            )
            is not OverdueRatingAppendOutcome.APPENDED
        ):
            raise RuntimeError("Payment void overdue source is inconsistent")
        append_pending_overdue_audits(session, effect=overdue)

    append_audit_event(
        session,
        create_payment_voided_audit_event(
            actor_user_id=actor.actor_user_id,
            payment_id=command.payment_id.as_uuid(),
            occurred_at=voided_at,
            voided_at=voided_at,
            current_revision=transition.debt.revision,
            payload=PaymentVoidedAuditPayload(
                reason=command.reason,
                from_status=debt_before.status,
                to_status=transition.debt.status,
                debt_revision_after=transition.debt.revision,
            ),
        ),
    )
    if transition.debt.status is not debt_before.status:
        append_audit_event(
            session,
            create_debt_reopened_after_payment_void_audit_event(
                actor_user_id=actor.actor_user_id,
                debt_id=source.debt_id.as_uuid(),
                occurred_at=voided_at,
                voided_at=voided_at,
                current_revision=transition.debt.revision,
                payload=DebtReopenedAfterPaymentVoidAuditPayload(
                    from_status=debt_before.status,
                    to_status=transition.debt.status,
                    debt_revision_after=transition.debt.revision,
                ),
            ),
        )
    return VoidPaymentMutationResult(
        outcome=PaymentVoidOutcome.NEW, payment_id=command.payment_id
    )


def _resolve_completed_void(session, *, row, actor, command):
    if not hmac.compare_digest(row.request_hash, command.request_hash.value):
        raise PaymentMutationRejected(ErrorCode.IDEMPOTENCY_CONFLICT)
    if (
        row.result_object_type != IdempotencyResultType.PAYMENT.value
        or row.result_object_id != command.payment_id.as_uuid()
        or get_tenant_payment(
            session,
            shop_id=actor.current_shop_id,
            payment_id=command.payment_id,
        )
        is None
        or not payment_void_exists_for_tenant_debt(
            session,
            shop_id=actor.current_shop_id,
            shop_customer_id=_shop_customer_id_for_command(session, actor, command),
            debt_id=command.debt_id,
            payment_id=command.payment_id,
        )
    ):
        raise RuntimeError("Completed Payment void replay is inconsistent")
    return VoidPaymentMutationResult(
        outcome=PaymentVoidOutcome.REPLAY, payment_id=command.payment_id
    )


def _shop_customer_id_for_command(session, actor, command):
    scoped = get_tenant_payment(
        session, shop_id=actor.current_shop_id, payment_id=command.payment_id
    )
    if scoped is None or scoped.debt.id != command.debt_id.as_uuid():
        raise RuntimeError("Completed Payment void replay is inconsistent")
    from app.shop_customer.values import ShopCustomerId

    return ShopCustomerId(scoped.debt.shop_customer_id)


def _utc_now() -> datetime:
    return datetime.now(UTC)
