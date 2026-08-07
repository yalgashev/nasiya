from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from app.shop_customer.contracts import (
    ShopCustomerPolicy,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
)


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
    CUSTOMER_ACTIVATED = "customer.activated"
    SHOP_CUSTOMER_LINKED = "shop_customer.linked"
    SHOP_CUSTOMER_POLICY_UPDATED = "shop_customer.policy_updated"
    SHOP_CUSTOMER_DEFAULTS_UPDATED = "shop.customer_defaults_updated"


class AuditObjectType(StrEnum):
    USER = "user"
    OFFER_VERSION = "offer_version"
    OFFER_TEXT = "offer_text"
    OFFER_ACCEPTANCE = "offer_acceptance"
    CUSTOMER_IDENTITY = "customer_identity"
    CUSTOMER_DOCUMENT = "customer_document"
    CUSTOMER = "customer"
    SHOP_CUSTOMER = "shop_customer"
    SHOP = "shop"


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
        AuditEventType.CUSTOMER_ACTIVATED: AuditObjectType.CUSTOMER,
        AuditEventType.SHOP_CUSTOMER_LINKED: AuditObjectType.SHOP_CUSTOMER,
        AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED: AuditObjectType.SHOP_CUSTOMER,
        AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED: AuditObjectType.SHOP,
    }
)

CUSTOMER_ACTIVATION_FROM_STATUS: Final = "draft"
CUSTOMER_ACTIVATION_TO_STATUS: Final = "active"
CUSTOMER_ACTIVATION_METHOD: Final = "TELEGRAM_REGISTRATION_OTP"


@dataclass(frozen=True, slots=True)
class CustomerActivatedAuditPayload:
    from_status: str = field(default=CUSTOMER_ACTIVATION_FROM_STATUS, init=False)
    to_status: str = field(default=CUSTOMER_ACTIVATION_TO_STATUS, init=False)
    activation_method: str = field(default=CUSTOMER_ACTIVATION_METHOD, init=False)

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "from_status": self.from_status,
                "to_status": self.to_status,
                "activation_method": self.activation_method,
            }
        )


@dataclass(frozen=True, slots=True)
class ShopCustomerLinkedAuditPayload:
    policy: ShopCustomerPolicy
    revision: ShopCustomerRevision
    outcome: str = field(default="created", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ShopCustomerPolicy):
            raise ValueError("Shop customer linked audit policy is invalid")
        if not isinstance(self.revision, ShopCustomerRevision):
            raise ValueError("Shop customer linked audit revision is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "outcome": self.outcome,
                "credit_limit_uzs": int(self.policy.credit_limit.value),
                "max_open_debts": self.policy.max_open_debts.value,
                "list_status": self.policy.list_status,
                "revision": self.revision.value,
            }
        )


@dataclass(frozen=True, slots=True)
class ShopCustomerPolicyUpdatedAuditPayload:
    old_policy: ShopCustomerPolicy
    new_policy: ShopCustomerPolicy
    revision: ShopCustomerRevision

    def __post_init__(self) -> None:
        if not isinstance(self.old_policy, ShopCustomerPolicy):
            raise ValueError("Shop customer old audit policy is invalid")
        if not isinstance(self.new_policy, ShopCustomerPolicy):
            raise ValueError("Shop customer new audit policy is invalid")
        if self.old_policy == self.new_policy:
            raise ValueError("Shop customer audit policy change must be real")
        if not isinstance(self.revision, ShopCustomerRevision):
            raise ValueError("Shop customer policy audit revision is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "old_credit_limit_uzs": int(self.old_policy.credit_limit.value),
                "new_credit_limit_uzs": int(self.new_policy.credit_limit.value),
                "old_max_open_debts": self.old_policy.max_open_debts.value,
                "new_max_open_debts": self.new_policy.max_open_debts.value,
                "old_list_status": self.old_policy.list_status,
                "new_list_status": self.new_policy.list_status,
                "revision": self.revision.value,
            }
        )


@dataclass(frozen=True, slots=True)
class ShopCustomerDefaultsUpdatedAuditPayload:
    old_defaults: ShopDefaultCreditPolicy
    new_defaults: ShopDefaultCreditPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.old_defaults, ShopDefaultCreditPolicy):
            raise ValueError("Shop old defaults audit policy is invalid")
        if not isinstance(self.new_defaults, ShopDefaultCreditPolicy):
            raise ValueError("Shop new defaults audit policy is invalid")
        if self.old_defaults == self.new_defaults:
            raise ValueError("Shop defaults audit policy change must be real")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "old_default_credit_limit_uzs": int(
                    self.old_defaults.credit_limit.value
                ),
                "new_default_credit_limit_uzs": int(
                    self.new_defaults.credit_limit.value
                ),
                "old_default_max_open_debts": self.old_defaults.max_open_debts.value,
                "new_default_max_open_debts": self.new_defaults.max_open_debts.value,
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
