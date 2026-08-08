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
    CreateDebtRequestHash,
    IdempotencyEndpoint,
    IdempotencyKeyDigest,
    IdempotencyOutcome,
    IdempotencyResultType,
)
from app.idempotency.models import IdempotencyKey

_UNIQUE = "uq_idempotency_keys_actor_user_id_endpoint_key_digest"


@dataclass(frozen=True, slots=True, repr=False)
class IdempotencyInsertResult:
    outcome: IdempotencyOutcome
    row: IdempotencyKey | None

    def __repr__(self) -> str:
        return "IdempotencyInsertResult(row=<redacted>)"


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
    request_hash: CreateDebtRequestHash,
    result_object_id: UUID,
    now: datetime,
) -> IdempotencyInsertResult:
    row = IdempotencyKey(
        actor_user_id=actor_user_id,
        endpoint=endpoint.value,
        key_digest=key_digest.value,
        request_hash=request_hash.value,
        result_object_type=IdempotencyResultType.DEBT.value,
        result_object_id=result_object_id,
        created_at=now,
    )
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


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
