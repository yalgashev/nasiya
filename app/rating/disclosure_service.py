"""Atomic private risk-band disclosure snapshot and replay composition."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.audit.repository import append_risk_band_disclosure_audit
from app.auth.error_codes import ErrorCode
from app.debt.business_time import tashkent_business_date
from app.debt.repository import mark_locked_customer_hard_block_scope
from app.idempotency.contracts import (
    IdempotencyEndpoint,
    IdempotencyOutcome,
    IdempotencyResultType,
    canonical_idempotency_key_digest,
)
from app.idempotency.repository import (
    completed_idempotency_result_from_row,
    find_completed_key,
    insert_or_resolve_key,
)
from app.rating.contracts import RiskBandDisclosureProjection
from app.rating.current_read_service import read_locked_current_risk_band
from app.rating.disclosure import (
    DisclosureMutationResult,
    RiskBandDisclosureAuditPayload,
    RiskBandDisclosureCommand,
)
from app.rating.ports import LockedRatingCustomerScope
from app.rating.repository import (
    insert_disclosure_view_locked,
    read_exact_tenant_disclosure_projection,
    read_tenant_disclosure_projection,
)
from app.rating.targeting import (
    DetachedDisclosureActorContext,
    discover_tenant_disclosure_target,
    lock_tenant_disclosure_target,
    recheck_historical_disclosure_authority,
)
from app.rating.values import DisclosureViewId

__all__ = (
    "DisclosureMutationRejected",
    "DisclosurePersistenceError",
    "read_risk_band_disclosure_snapshot",
    "record_risk_band_disclosure",
)

type DisclosureClock = Callable[[], datetime]


class DisclosureMutationRejected(RuntimeError):
    """Identifier-free expected denial at the private disclosure boundary."""

    def __init__(self, error: ErrorCode) -> None:
        if error not in {
            ErrorCode.FORBIDDEN,
            ErrorCode.SHOP_SUSPENDED,
            ErrorCode.SHOP_CUSTOMER_UNAVAILABLE,
            ErrorCode.IDEMPOTENCY_CONFLICT,
        }:
            raise ValueError("Disclosure rejection code is invalid")
        self.error = error
        super().__init__("Risk-band disclosure is unavailable")

    def __repr__(self) -> str:
        return "DisclosureMutationRejected(<redacted>)"


class DisclosurePersistenceError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Risk-band disclosure persistence failed")

    def __repr__(self) -> str:
        return "DisclosurePersistenceError(<redacted>)"


def record_risk_band_disclosure(
    session: Session,
    *,
    actor: DetachedDisclosureActorContext,
    command: RiskBandDisclosureCommand,
    disclosure_clock: DisclosureClock,
) -> DisclosureMutationResult:
    """Create or replay one immutable snapshot in the caller-owned transaction."""

    _validate_command(actor=actor, command=command)
    if not callable(disclosure_clock):
        raise TypeError("disclosure_clock must be callable")

    candidate = discover_tenant_disclosure_target(
        session,
        actor=actor,
        shop_customer_id=command.shop_customer_id,
    )
    target_result = lock_tenant_disclosure_target(
        session,
        actor=actor,
        candidate=candidate,
    )
    if target_result.error is not None:
        raise DisclosureMutationRejected(target_result.error)
    assert target_result.locked is not None
    target = target_result.locked

    endpoint = IdempotencyEndpoint.SHOP_RISK_BAND_DISCLOSURES_CREATE
    key_digest = canonical_idempotency_key_digest(command.idempotency_key)
    completed = find_completed_key(
        session,
        actor_user_id=actor.actor_user_id,
        endpoint=endpoint,
        key_digest=key_digest,
    )
    if completed is not None:
        return _resolve_completed_disclosure(
            session,
            row=completed,
            actor=actor,
            command=command,
        )

    disclosure_view_id = DisclosureViewId(uuid4())
    try:
        key_result = insert_or_resolve_key(
            session,
            actor_user_id=actor.actor_user_id,
            endpoint=endpoint,
            key_digest=key_digest,
            request_hash=command.request_hash,
            result_object_id=disclosure_view_id.as_uuid(),
            now=None,
        )
    except Exception:
        raise DisclosurePersistenceError() from None
    if key_result.outcome is IdempotencyOutcome.CONFLICT:
        raise DisclosureMutationRejected(ErrorCode.IDEMPOTENCY_CONFLICT)
    if key_result.outcome is IdempotencyOutcome.REPLAY:
        assert key_result.row is not None
        return _resolve_completed_disclosure(
            session,
            row=key_result.row,
            actor=actor,
            command=command,
        )

    viewed_at = _capture_disclosure_now(disclosure_clock())
    locked_customer = mark_locked_customer_hard_block_scope(
        session,
        locked_customer=target.customer,
    )
    projection = read_locked_current_risk_band(
        session,
        locked_customer=locked_customer,
        as_of_business_date=tashkent_business_date(viewed_at),
    )
    try:
        insert_disclosure_view_locked(
            session,
            locked_customer=LockedRatingCustomerScope(
                customer_id=target.customer_id,
                _session=session,
            ),
            disclosure_view_id=disclosure_view_id,
            actor_user_id=target.actor_user_id,
            current_shop_id=target.current_shop_id,
            shop_customer_id=target.shop_customer_id,
            purpose=command.purpose,
            band=projection.band,
            viewed_at=viewed_at,
        )
        append_risk_band_disclosure_audit(
            session,
            disclosure_view_id=disclosure_view_id.as_uuid(),
            actor_user_id=target.actor_user_id,
            occurred_at=viewed_at,
            payload=RiskBandDisclosureAuditPayload(
                purpose=command.purpose,
                band=projection.band,
            ),
        )
    except Exception:
        raise DisclosurePersistenceError() from None
    return DisclosureMutationResult(
        outcome=IdempotencyOutcome.NEW,
        disclosure_view_id=disclosure_view_id,
    )


def read_risk_band_disclosure_snapshot(
    session: Session,
    *,
    actor: DetachedDisclosureActorContext,
    disclosure_view_id: DisclosureViewId,
) -> RiskBandDisclosureProjection | None:
    """Read a stored band-only snapshot without target locks or recomputation."""

    if not isinstance(actor, DetachedDisclosureActorContext):
        raise TypeError("actor must be a DetachedDisclosureActorContext")
    if not isinstance(disclosure_view_id, DisclosureViewId):
        raise TypeError("disclosure_view_id must be a DisclosureViewId")
    if recheck_historical_disclosure_authority(session, actor=actor) is not None:
        return None
    return read_tenant_disclosure_projection(
        session,
        actor_user_id=actor.actor_user_id,
        current_shop_id=actor.current_shop_id,
        disclosure_view_id=disclosure_view_id,
    )


def _resolve_completed_disclosure(
    session: Session,
    *,
    row,
    actor: DetachedDisclosureActorContext,
    command: RiskBandDisclosureCommand,
) -> DisclosureMutationResult:
    if not hmac.compare_digest(row.request_hash, command.request_hash.value):
        raise DisclosureMutationRejected(ErrorCode.IDEMPOTENCY_CONFLICT)
    completed = completed_idempotency_result_from_row(row)
    try:
        raw_id = completed.require_result_object_id(
            expected_type=IdempotencyResultType.DISCLOSURE_VIEW
        )
        disclosure_view_id = DisclosureViewId(raw_id)
    except (TypeError, ValueError):
        raise RuntimeError("Disclosure replay resolution failed") from None
    stored = read_exact_tenant_disclosure_projection(
        session,
        actor_user_id=actor.actor_user_id,
        current_shop_id=actor.current_shop_id,
        shop_customer_id=command.shop_customer_id,
        disclosure_view_id=disclosure_view_id,
    )
    if stored is None or stored.purpose is not command.purpose:
        raise RuntimeError("Disclosure replay resolution failed")
    return DisclosureMutationResult(
        outcome=IdempotencyOutcome.REPLAY,
        disclosure_view_id=disclosure_view_id,
    )


def _validate_command(
    *, actor: DetachedDisclosureActorContext, command: RiskBandDisclosureCommand
) -> None:
    if not isinstance(actor, DetachedDisclosureActorContext):
        raise TypeError("actor must be a DetachedDisclosureActorContext")
    if not isinstance(command, RiskBandDisclosureCommand):
        raise TypeError("command must be a RiskBandDisclosureCommand")
    if (
        command.actor_user_id != actor.actor_user_id
        or command.current_shop_id != actor.current_shop_id
    ):
        raise ValueError("Disclosure command does not match actor context")


def _capture_disclosure_now(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Disclosure clock must return an aware datetime")
    return value.astimezone(UTC)
