"""Caller-owned orchestration for tenant pending-debt proposals."""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtCreatedAuditPayload,
)
from app.audit.repository import append_audit_event
from app.auth.error_codes import ErrorCode
from app.debt.commands import CreateDebtCommand
from app.debt.contracts import DebtAggregate
from app.debt.creation_eligibility import (
    DebtOpenSetReaderFactory,
    evaluate_locked_debt_creation,
)
from app.debt.dependencies import DetachedDebtActorAuthority
from app.debt.offer_gate import lock_current_complete_debt_offer
from app.debt.policy import GlobalHardBlockReadPort
from app.debt.repository import insert_debt, mark_debt_predecessor_locked
from app.debt.targeting import (
    discover_tenant_debt_target,
    lock_debt_target_before_offer,
    lock_debt_target_shop_customer_after_offer,
)
from app.debt.values import DebtId, ShopCustomerId, ShopId, UserId
from app.idempotency.contracts import (
    CreateDebtRequestHash,
    IdempotencyEndpoint,
    IdempotencyOutcome,
    canonical_idempotency_key_digest,
    create_debt_request_hash,
)
from app.idempotency.models import IdempotencyKey
from app.idempotency.repository import (
    completed_idempotency_result_from_row,
    find_completed_key,
    insert_or_resolve_key,
)


@dataclass(frozen=True, slots=True, repr=False)
class CreateDebtProposalResult:
    outcome: IdempotencyOutcome | None
    debt_id: DebtId | None = field(default=None, repr=False)
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if self.error is None:
            if self.outcome not in {
                IdempotencyOutcome.NEW,
                IdempotencyOutcome.REPLAY,
            } or not isinstance(self.debt_id, DebtId):
                raise ValueError("Successful debt proposal result is invalid")
        elif self.outcome is not None or self.debt_id is not None:
            raise ValueError("Failed debt proposal result cannot disclose a result")

    def __repr__(self) -> str:
        return (
            "CreateDebtProposalResult("
            f"outcome={self.outcome!r}, debt_id=<redacted>, error={self.error!r})"
        )


def create_pending_debt_proposal(
    session: Session,
    *,
    authority: DetachedDebtActorAuthority,
    shop_customer_id: ShopCustomerId,
    command: CreateDebtCommand,
    global_hard_block_reader: GlobalHardBlockReadPort | None = None,
    open_set_reader_factory: DebtOpenSetReaderFactory | None = None,
) -> CreateDebtProposalResult:
    """Create one key/debt/audit unit without owning the borrowed Session."""

    if not isinstance(authority, DetachedDebtActorAuthority):
        raise TypeError("authority must be detached debt authority")
    if not isinstance(shop_customer_id, ShopCustomerId):
        raise TypeError("shop_customer_id must be a ShopCustomerId")
    if not isinstance(command, CreateDebtCommand):
        raise TypeError("command must be a CreateDebtCommand")
    if not authority.is_authenticated:
        return CreateDebtProposalResult(outcome=None, error=ErrorCode.FORBIDDEN)
    assert authority.actor_user_id is not None
    assert authority.current_shop_id is not None

    request_hash = create_debt_request_hash(
        actor_user_id=UserId(authority.actor_user_id),
        shop_id=ShopId(authority.current_shop_id),
        shop_customer_id=shop_customer_id,
        original_amount=command.original_amount,
        discount_basis_points=command.discount_basis_points,
        due_date=command.due_date,
    )
    key_digest = canonical_idempotency_key_digest(command.idempotency_key)
    completed = find_completed_key(
        session,
        actor_user_id=authority.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_DEBTS_CREATE,
        key_digest=key_digest,
    )
    if completed is not None:
        return _resolve_completed_key(completed, request_hash=request_hash)

    candidate = discover_tenant_debt_target(
        session,
        authority=authority,
        shop_customer_id=shop_customer_id,
    )
    target_resolution = lock_debt_target_before_offer(
        session,
        authority=authority,
        candidate=candidate,
    )
    if target_resolution.error is not None:
        return CreateDebtProposalResult(outcome=None, error=target_resolution.error)
    assert target_resolution.locked_before_offer is not None
    offer_result = lock_current_complete_debt_offer(
        session,
        locked_target=target_resolution.locked_before_offer,
    )
    if offer_result.error is not None:
        return CreateDebtProposalResult(outcome=None, error=offer_result.error)
    assert offer_result.locked_offer is not None
    locked_target = lock_debt_target_shop_customer_after_offer(
        session,
        locked_before_offer=target_resolution.locked_before_offer,
        locked_offer=offer_result.locked_offer,
    )
    if locked_target is None:
        return CreateDebtProposalResult(
            outcome=None,
            error=ErrorCode.SHOP_CUSTOMER_UNAVAILABLE,
        )

    completed = find_completed_key(
        session,
        actor_user_id=authority.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_DEBTS_CREATE,
        key_digest=key_digest,
    )
    if completed is not None:
        return _resolve_completed_key(completed, request_hash=request_hash)

    eligibility = evaluate_locked_debt_creation(
        session,
        locked_target=locked_target,
        original_amount=command.original_amount,
        global_hard_block_reader=global_hard_block_reader,
        open_set_reader_factory=open_set_reader_factory,
    )
    if eligibility.error is not None:
        return CreateDebtProposalResult(outcome=None, error=eligibility.error)

    debt_id = DebtId(uuid4())
    key_result = insert_or_resolve_key(
        session,
        actor_user_id=authority.actor_user_id,
        endpoint=IdempotencyEndpoint.SHOP_DEBTS_CREATE,
        key_digest=key_digest,
        request_hash=request_hash,
        result_object_id=debt_id.as_uuid(),
        now=command.created_at,
    )
    if key_result.outcome is IdempotencyOutcome.CONFLICT:
        return CreateDebtProposalResult(
            outcome=None,
            error=ErrorCode.IDEMPOTENCY_CONFLICT,
        )
    if key_result.outcome is IdempotencyOutcome.REPLAY:
        assert key_result.row is not None
        return _completed_result(key_result.row)

    row = locked_target._locked_shop_customer.row
    debt = DebtAggregate.create_pending(
        debt_id=debt_id,
        shop_customer_id=ShopCustomerId(row.id),
        created_by_user_id=UserId(authority.actor_user_id),
        original_amount=command.original_amount,
        discount_basis_points=command.discount_basis_points,
        discounted_amount=command.discounted_amount,
        due_date=command.due_date,
        created_at=command.created_at,
    )
    predecessor = mark_debt_predecessor_locked(
        session,
        locked_shop_customer=locked_target._locked_shop_customer,
    )
    insert_debt(session, locked_predecessor=predecessor, debt=debt)
    payload = DebtCreatedAuditPayload(
        original_amount=debt.original_amount,
        discount_basis_points=debt.discount_basis_points,
        discounted_amount=debt.discounted_amount,
        due_date=debt.due_date,
        pending_expires_at=debt.pending_expires_at,
    )
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_CREATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=authority.actor_user_id,
            object_type=AuditObjectType.DEBT,
            object_id=debt_id.as_uuid(),
            occurred_at=command.created_at,
            candidate_metadata=payload.as_candidate_metadata(),
        ),
    )
    return CreateDebtProposalResult(
        outcome=IdempotencyOutcome.NEW,
        debt_id=debt_id,
    )


def _resolve_completed_key(
    row: IdempotencyKey, *, request_hash: CreateDebtRequestHash
) -> CreateDebtProposalResult:
    if not hmac.compare_digest(row.request_hash, request_hash.value):
        return CreateDebtProposalResult(
            outcome=None,
            error=ErrorCode.IDEMPOTENCY_CONFLICT,
        )
    return _completed_result(row)


def _completed_result(row: IdempotencyKey) -> CreateDebtProposalResult:
    completed = completed_idempotency_result_from_row(row)
    return CreateDebtProposalResult(
        outcome=IdempotencyOutcome.REPLAY,
        debt_id=completed.debt_id,
    )
