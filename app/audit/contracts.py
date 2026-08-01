from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable
from uuid import UUID


class AuditEventType(StrEnum):
    PLATFORM_ADMIN_BOOTSTRAPPED = "platform_admin.bootstrapped"
    OFFER_VERSION_CREATED = "offer.version_created"
    OFFER_TEXT_UPDATED = "offer.text_updated"
    OFFER_VERSION_APPROVED = "offer.version_approved"
    OFFER_VERSION_MADE_CURRENT = "offer.version_made_current"
    OFFER_VERSION_DEMOTED = "offer.version_demoted"
    OFFER_REGISTRATION_ACCEPTED = "offer.registration_accepted"
    CUSTOMER_IDENTITY_SAVED = "customer.identity_saved"
    CUSTOMER_DOCUMENT_ATTACHED = "customer.document_attached"
    CUSTOMER_DOCUMENT_SUPERSEDED = "customer.document_superseded"
    CUSTOMER_DOCUMENT_ACCESS_GRANTED = "customer.document_access_granted"


class AuditObjectType(StrEnum):
    USER = "user"
    OFFER_VERSION = "offer_version"
    OFFER_TEXT = "offer_text"
    OFFER_ACCEPTANCE = "offer_acceptance"
    CUSTOMER_IDENTITY = "customer_identity"
    CUSTOMER_DOCUMENT = "customer_document"


class AuditActorKind(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"


_EVENT_OBJECT_TYPES: Final[Mapping[AuditEventType, AuditObjectType]] = MappingProxyType(
    {
        AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED: AuditObjectType.USER,
        AuditEventType.OFFER_VERSION_CREATED: AuditObjectType.OFFER_VERSION,
        AuditEventType.OFFER_TEXT_UPDATED: AuditObjectType.OFFER_TEXT,
        AuditEventType.OFFER_VERSION_APPROVED: AuditObjectType.OFFER_VERSION,
        AuditEventType.OFFER_VERSION_MADE_CURRENT: (AuditObjectType.OFFER_VERSION),
        AuditEventType.OFFER_VERSION_DEMOTED: AuditObjectType.OFFER_VERSION,
        AuditEventType.OFFER_REGISTRATION_ACCEPTED: (AuditObjectType.OFFER_ACCEPTANCE),
        AuditEventType.CUSTOMER_IDENTITY_SAVED: AuditObjectType.CUSTOMER_IDENTITY,
        AuditEventType.CUSTOMER_DOCUMENT_ATTACHED: AuditObjectType.CUSTOMER_DOCUMENT,
        AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED: (
            AuditObjectType.CUSTOMER_DOCUMENT
        ),
        AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED: (
            AuditObjectType.CUSTOMER_DOCUMENT
        ),
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class AuditEvent:
    event_type: AuditEventType
    actor_kind: AuditActorKind
    actor_user_id: UUID | None
    object_type: AuditObjectType
    object_id: UUID
    occurred_at: datetime
    candidate_metadata: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, AuditEventType):
            raise ValueError("Audit event type is invalid")
        if not isinstance(self.actor_kind, AuditActorKind):
            raise ValueError("Audit actor kind is invalid")
        if not isinstance(self.object_type, AuditObjectType):
            raise ValueError("Audit object type is invalid")
        if not isinstance(self.object_id, UUID):
            raise ValueError("Audit object id must be a UUID")
        if not isinstance(self.candidate_metadata, Mapping):
            raise ValueError("Audit candidate metadata must be a mapping")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Audit occurrence time must be timezone-aware")

        expected_object_type = _EVENT_OBJECT_TYPES[self.event_type]
        if self.object_type is not expected_object_type:
            raise ValueError("Audit event object type is invalid")

        is_bootstrap = self.event_type is AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED
        if is_bootstrap:
            if (
                self.actor_kind is not AuditActorKind.SYSTEM
                or self.actor_user_id is not None
            ):
                raise ValueError("Bootstrap audit actor must be SYSTEM")
        elif self.actor_kind is not AuditActorKind.USER or not isinstance(
            self.actor_user_id, UUID
        ):
            raise ValueError("Offer audit actor must be a user")

        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        object.__setattr__(
            self,
            "candidate_metadata",
            MappingProxyType(dict(self.candidate_metadata)),
        )

    def __repr__(self) -> str:
        return (
            "AuditEvent("
            f"event_type={self.event_type.value!r}, "
            f"actor_kind={self.actor_kind.value!r}, "
            f"object_type={self.object_type.value!r}, "
            "object_id=<redacted>, "
            f"occurred_at={self.occurred_at!r}, actor_user_id=<redacted>, "
            "candidate_metadata=<redacted>)"
        )


@runtime_checkable
class AuditWriter(Protocol):
    def append(self, *, event: AuditEvent) -> None: ...
