"""Redacted, idempotent command and audit contracts for risk-band disclosure."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final
from uuid import UUID

from app.auth.error_codes import ErrorCode
from app.idempotency.contracts import (
    CanonicalIdempotencyKey,
    IdempotencyOutcome,
    parse_idempotency_key,
)
from app.rating.enums import RiskBand, RiskBandDisclosurePurpose
from app.rating.values import DisclosureViewId, RiskBandDisclosureRequestHash
from app.shop.values import ShopId, UserId
from app.shop_customer.values import ShopCustomerId

__all__ = (
    "DISCLOSURE_AUDIT_EVENT_TYPE",
    "DISCLOSURE_AUDIT_OBJECT_TYPE",
    "DisclosureCommandAssembly",
    "DisclosureMutationResult",
    "RiskBandDisclosureAuditPayload",
    "RiskBandDisclosureCommand",
    "RiskBandDisclosureRawForm",
    "assemble_risk_band_disclosure_command",
    "create_risk_band_disclosure_request_hash_v1",
)

DISCLOSURE_AUDIT_EVENT_TYPE: Final = "disclosure.risk_band_viewed"
DISCLOSURE_AUDIT_OBJECT_TYPE: Final = "disclosure_view"
_DISCLOSURE_REQUEST_HASH_DOMAIN_V1: Final = (
    b"nasiya.m16.risk-band-disclosure.request.v1"
)


@dataclass(frozen=True, slots=True, repr=False)
class RiskBandDisclosureRawForm:
    """Only client-originated disclosure fields; CSRF is a route dependency."""

    purpose: str | None = field(repr=False)
    idempotency_key: str | None = field(repr=False)

    def __repr__(self) -> str:
        return "RiskBandDisclosureRawForm(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RiskBandDisclosureCommand:
    """Canonical command with server-injected actor, Shop, and tenant locator."""

    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)
    shop_customer_id: ShopCustomerId = field(repr=False)
    purpose: RiskBandDisclosurePurpose
    idempotency_key: CanonicalIdempotencyKey = field(repr=False)
    request_hash: RiskBandDisclosureRequestHash = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor_user_id, UUID):
            raise ValueError("Disclosure command actor is invalid")
        if not isinstance(self.current_shop_id, UUID):
            raise ValueError("Disclosure command Shop is invalid")
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Disclosure command ShopCustomer is invalid")
        if not isinstance(self.purpose, RiskBandDisclosurePurpose):
            raise ValueError("Disclosure command purpose is invalid")
        if not isinstance(self.idempotency_key, CanonicalIdempotencyKey):
            raise ValueError("Disclosure command idempotency key is invalid")
        if not isinstance(self.request_hash, RiskBandDisclosureRequestHash):
            raise ValueError("Disclosure command request hash is invalid")
        expected_hash = create_risk_band_disclosure_request_hash_v1(
            actor_user_id=self.actor_user_id,
            current_shop_id=self.current_shop_id,
            shop_customer_id=self.shop_customer_id,
            purpose=self.purpose,
        )
        if self.request_hash != expected_hash:
            raise ValueError("Disclosure command request hash is invalid")

    def __repr__(self) -> str:
        return "RiskBandDisclosureCommand(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DisclosureCommandAssembly:
    command: RiskBandDisclosureCommand | None = field(default=None, repr=False)
    error: ErrorCode | None = None

    def __post_init__(self) -> None:
        if (self.command is None) == (self.error is None):
            raise ValueError("Disclosure command assembly is invalid")
        if self.error not in {None, ErrorCode.VALIDATION_ERROR}:
            raise ValueError("Disclosure command assembly error is invalid")

    def __repr__(self) -> str:
        return f"DisclosureCommandAssembly(error={self.error!r}, command=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DisclosureMutationResult:
    outcome: IdempotencyOutcome
    disclosure_view_id: DisclosureViewId = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, IdempotencyOutcome) or self.outcome not in {
            IdempotencyOutcome.NEW,
            IdempotencyOutcome.REPLAY,
        }:
            raise ValueError("Disclosure mutation outcome is invalid")
        if not isinstance(self.disclosure_view_id, DisclosureViewId):
            raise ValueError("Disclosure mutation result identity is invalid")

    def __repr__(self) -> str:
        return (
            f"DisclosureMutationResult(outcome={self.outcome.value!r}, id=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RiskBandDisclosureAuditPayload:
    purpose: RiskBandDisclosurePurpose
    band: RiskBand

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, RiskBandDisclosurePurpose):
            raise ValueError("Disclosure audit purpose is invalid")
        if not isinstance(self.band, RiskBand):
            raise ValueError("Disclosure audit band is invalid")

    def as_candidate_metadata(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                "purpose": self.purpose.value,
                "band": self.band.value,
            }
        )

    def __repr__(self) -> str:
        return "RiskBandDisclosureAuditPayload(<safe>)"


def create_risk_band_disclosure_request_hash_v1(
    *,
    actor_user_id: UserId,
    current_shop_id: ShopId,
    shop_customer_id: ShopCustomerId,
    purpose: RiskBandDisclosurePurpose,
) -> RiskBandDisclosureRequestHash:
    if not isinstance(actor_user_id, UUID):
        raise ValueError("Disclosure request actor is invalid")
    if not isinstance(current_shop_id, UUID):
        raise ValueError("Disclosure request Shop is invalid")
    if not isinstance(shop_customer_id, ShopCustomerId):
        raise ValueError("Disclosure request ShopCustomer is invalid")
    if not isinstance(purpose, RiskBandDisclosurePurpose):
        raise ValueError("Disclosure request purpose is invalid")
    encoded = _length_safe_encode(
        (
            _DISCLOSURE_REQUEST_HASH_DOMAIN_V1,
            actor_user_id.bytes,
            current_shop_id.bytes,
            shop_customer_id.as_uuid().bytes,
            purpose.value.encode("ascii"),
        )
    )
    return RiskBandDisclosureRequestHash(hashlib.sha256(encoded).hexdigest())


def assemble_risk_band_disclosure_command(
    *,
    raw: RiskBandDisclosureRawForm,
    actor_user_id: UserId,
    current_shop_id: ShopId,
    shop_customer_id: ShopCustomerId,
) -> DisclosureCommandAssembly:
    if not isinstance(raw, RiskBandDisclosureRawForm):
        raise TypeError("raw must be a RiskBandDisclosureRawForm")
    try:
        purpose = RiskBandDisclosurePurpose(raw.purpose)
        key = parse_idempotency_key(raw.idempotency_key)  # type: ignore[arg-type]
        request_hash = create_risk_band_disclosure_request_hash_v1(
            actor_user_id=actor_user_id,
            current_shop_id=current_shop_id,
            shop_customer_id=shop_customer_id,
            purpose=purpose,
        )
        command = RiskBandDisclosureCommand(
            actor_user_id=actor_user_id,
            current_shop_id=current_shop_id,
            shop_customer_id=shop_customer_id,
            purpose=purpose,
            idempotency_key=key,
            request_hash=request_hash,
        )
    except (TypeError, ValueError):
        return DisclosureCommandAssembly(error=ErrorCode.VALIDATION_ERROR)
    return DisclosureCommandAssembly(command=command)


def _length_safe_encode(parts: tuple[bytes, ...]) -> bytes:
    return b"".join(len(part).to_bytes(4, byteorder="big") + part for part in parts)
