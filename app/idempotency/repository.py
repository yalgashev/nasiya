"""Narrow durable replay persistence; callers retain transaction ownership."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.idempotency.contracts import (
    CompletedIdempotencyResult,
    CreateDebtRequestHash,
    CreatePaymentRequestHash,
    IdempotencyEndpoint,
    IdempotencyKeyDigest,
    IdempotencyOutcome,
    IdempotencyResultType,
    VoidPaymentRequestHash,
    WriteOffDebtRequestHash,
)
from app.idempotency.models import IdempotencyKey
from app.rating.values import RiskBandDisclosureRequestHash

_UNIQUE = "uq_idempotency_keys_actor_user_id_endpoint_key_digest"
_RequestHash = (
    CreateDebtRequestHash
    | CreatePaymentRequestHash
    | RiskBandDisclosureRequestHash
    | VoidPaymentRequestHash
    | WriteOffDebtRequestHash
)


@dataclass(frozen=True, slots=True, repr=False)
class IdempotencyInsertResult:
    outcome: IdempotencyOutcome
    row: IdempotencyKey | None

    def __repr__(self) -> str:
        return "IdempotencyInsertResult(row=<redacted>)"


def completed_idempotency_result_from_row(
    row: IdempotencyKey,
) -> CompletedIdempotencyResult:
    """Map a persisted idempotency row to its typed, fail-closed result."""
    if not isinstance(row, IdempotencyKey):
        raise TypeError("row must be an IdempotencyKey")
    return CompletedIdempotencyResult(
        result_type=IdempotencyResultType(row.result_object_type),
        result_object_id=row.result_object_id,
        completed_at=row.created_at,
    )


def find_completed_key(
    session: Session,
    *,
    actor_user_id: UUID,
    endpoint: IdempotencyEndpoint,
    key_digest: IdempotencyKeyDigest,
) -> IdempotencyKey | None:
    return session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.actor_user_id == actor_user_id,
            IdempotencyKey.endpoint == endpoint.value,
            IdempotencyKey.key_digest == key_digest.value,
        )
    )


def insert_or_resolve_key(
    session: Session,
    *,
    actor_user_id: UUID,
    endpoint: IdempotencyEndpoint,
    key_digest: IdempotencyKeyDigest,
    request_hash: _RequestHash,
    result_object_id: UUID,
    now: datetime | None,
) -> IdempotencyInsertResult:
    result_type = _result_type_for_request(
        endpoint=endpoint,
        request_hash=request_hash,
    )
    row_values = dict(
        actor_user_id=actor_user_id,
        endpoint=endpoint.value,
        key_digest=key_digest.value,
        request_hash=request_hash.value,
        result_object_type=result_type.value,
        result_object_id=result_object_id,
    )
    if now is not None:
        row_values["created_at"] = now
    row = IdempotencyKey(**row_values)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        if _constraint_name(exc) != _UNIQUE:
            raise
        existing = find_completed_key(
            session,
            actor_user_id=actor_user_id,
            endpoint=endpoint,
            key_digest=key_digest,
        )
        if existing is None:
            raise RuntimeError(
                "Expected idempotency conflict row is unavailable"
            ) from None
        if hmac.compare_digest(existing.request_hash, request_hash.value):
            return IdempotencyInsertResult(IdempotencyOutcome.REPLAY, existing)
        return IdempotencyInsertResult(IdempotencyOutcome.CONFLICT, None)
    return IdempotencyInsertResult(IdempotencyOutcome.NEW, row)


def _result_type_for_request(
    *,
    endpoint: IdempotencyEndpoint,
    request_hash: _RequestHash,
) -> IdempotencyResultType:
    if not isinstance(endpoint, IdempotencyEndpoint):
        raise TypeError("endpoint must be an IdempotencyEndpoint")
    if endpoint is IdempotencyEndpoint.SHOP_DEBTS_CREATE:
        if not isinstance(request_hash, CreateDebtRequestHash):
            raise TypeError(
                "request_hash must be a CreateDebtRequestHash for debt creation"
            )
        return IdempotencyResultType.DEBT
    if endpoint is IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE:
        if not isinstance(request_hash, CreatePaymentRequestHash):
            raise TypeError(
                "request_hash must be a CreatePaymentRequestHash for payment creation"
            )
        return IdempotencyResultType.PAYMENT
    if endpoint is IdempotencyEndpoint.SHOP_RISK_BAND_DISCLOSURES_CREATE:
        if not isinstance(request_hash, RiskBandDisclosureRequestHash):
            raise TypeError(
                "request_hash must be a RiskBandDisclosureRequestHash "
                "for disclosure creation"
            )
        return IdempotencyResultType.DISCLOSURE_VIEW
    if endpoint is IdempotencyEndpoint.ADMIN_DEBTS_WRITE_OFF:
        if not isinstance(request_hash, WriteOffDebtRequestHash):
            raise TypeError(
                "request_hash must be a WriteOffDebtRequestHash for write-off"
            )
        return IdempotencyResultType.DEBT
    if endpoint is IdempotencyEndpoint.SHOP_PAYMENTS_VOID:
        if not isinstance(request_hash, VoidPaymentRequestHash):
            raise TypeError(
                "request_hash must be a VoidPaymentRequestHash for payment void"
            )
        return IdempotencyResultType.PAYMENT
    raise ValueError("unsupported idempotency endpoint")


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
