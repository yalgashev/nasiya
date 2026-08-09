from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from app.debt.enums import (
    DebtBalanceBasis,
    DebtExpirySource,
    DebtOverdueSource,
    DebtStatus,
)
from app.debt.values import (
    ClawbackIncreaseUZS,
    DebtRevision,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
)
from app.offers.enums import OfferLanguage
from app.payment.enums import PaymentMethod
from app.payment.values import PaymentAmountUZS
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
    DEBT_CREATED = "debt.created"
    DEBT_ACCEPTED = "debt.accepted"
    DEBT_REJECTED = "debt.rejected"
    DEBT_CANCELLED = "debt.cancelled"
    DEBT_EXPIRED = "debt.expired"
    DEBT_OVERDUE = "debt.overdue"
    DEBT_CLAWBACK_APPLIED = "debt.clawback_applied"
    PAYMENT_RECORDED = "payment.recorded"
    DEBT_PAID = "debt.paid"


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
    DEBT = "debt"
    PAYMENT = "payment"


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
        AuditEventType.DEBT_CREATED: AuditObjectType.DEBT,
        AuditEventType.DEBT_ACCEPTED: AuditObjectType.DEBT,
        AuditEventType.DEBT_REJECTED: AuditObjectType.DEBT,
        AuditEventType.DEBT_CANCELLED: AuditObjectType.DEBT,
        AuditEventType.DEBT_EXPIRED: AuditObjectType.DEBT,
        AuditEventType.DEBT_OVERDUE: AuditObjectType.DEBT,
        AuditEventType.DEBT_CLAWBACK_APPLIED: AuditObjectType.DEBT,
        AuditEventType.PAYMENT_RECORDED: AuditObjectType.PAYMENT,
        AuditEventType.DEBT_PAID: AuditObjectType.DEBT,
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


@dataclass(frozen=True, slots=True)
class DebtCreatedAuditPayload:
    original_amount: OriginalAmountUZS
    discount_basis_points: DiscountBasisPoints
    discounted_amount: DiscountedAmountUZS
    due_date: date
    pending_expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.original_amount, OriginalAmountUZS):
            raise ValueError("Debt created audit original amount is invalid")
        if not isinstance(self.discount_basis_points, DiscountBasisPoints):
            raise ValueError("Debt created audit discount is invalid")
        if not isinstance(self.discounted_amount, DiscountedAmountUZS):
            raise ValueError("Debt created audit discounted amount is invalid")
        if not isinstance(self.due_date, date) or isinstance(self.due_date, datetime):
            raise ValueError("Debt created audit due date is invalid")
        object.__setattr__(
            self,
            "pending_expires_at",
            _as_utc(self.pending_expires_at, field_name="pending_expires_at"),
        )

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "original_amount_uzs": int(self.original_amount.value),
                "discount_basis_points": self.discount_basis_points.value,
                "discounted_amount_uzs": int(self.discounted_amount.value),
                "due_date": self.due_date.isoformat(),
                "pending_expires_at": self.pending_expires_at.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class DebtAcceptedAuditPayload:
    offer_version_number: int
    language: OfferLanguage
    content_hash: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.offer_version_number, int)
            or isinstance(self.offer_version_number, bool)
            or self.offer_version_number < 1
        ):
            raise ValueError("Debt accepted audit offer version is invalid")
        if not isinstance(self.language, OfferLanguage):
            raise ValueError("Debt accepted audit language is invalid")
        if not isinstance(self.content_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.content_hash, flags=re.ASCII
        ):
            raise ValueError("Debt accepted audit content hash is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "offer_version_number": self.offer_version_number,
                "language": self.language.value,
                "content_hash": self.content_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class DebtRejectedAuditPayload:
    reason_provided: bool

    def __post_init__(self) -> None:
        if not isinstance(self.reason_provided, bool):
            raise ValueError("Debt rejected audit reason indicator is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType({"reason_provided": self.reason_provided})


@dataclass(frozen=True, slots=True)
class DebtCancelledAuditPayload:
    reason_provided: bool = True

    def __post_init__(self) -> None:
        if self.reason_provided is not True:
            raise ValueError("Debt cancelled audit requires a reason")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType({"reason_provided": True})


@dataclass(frozen=True, slots=True)
class DebtExpiredAuditPayload:
    source: DebtExpirySource

    def __post_init__(self) -> None:
        if not isinstance(self.source, DebtExpirySource):
            raise ValueError("Debt expired audit source is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType({"source": self.source.value})


@dataclass(frozen=True, slots=True)
class PaymentRecordedAuditPayload:
    """The closed, identifier-free audit payload for an immutable Payment."""

    amount: PaymentAmountUZS
    method: PaymentMethod
    from_status: DebtStatus
    to_status: DebtStatus
    debt_revision_after: DebtRevision

    def __post_init__(self) -> None:
        if not isinstance(self.amount, PaymentAmountUZS):
            raise ValueError("Payment recorded audit amount is invalid")
        if not isinstance(self.method, PaymentMethod):
            raise ValueError("Payment recorded audit method is invalid")
        allowed_targets = {
            DebtStatus.ACTIVE: frozenset({DebtStatus.ACTIVE, DebtStatus.PAID}),
            DebtStatus.OVERDUE: frozenset({DebtStatus.OVERDUE, DebtStatus.PAID}),
        }
        if self.to_status not in allowed_targets.get(self.from_status, frozenset()):
            raise ValueError("Payment recorded audit target status is invalid")
        if not isinstance(self.debt_revision_after, DebtRevision):
            raise ValueError("Payment recorded audit debt revision is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "amount_uzs": int(self.amount.value),
                "method": self.method.value,
                "from_status": self.from_status.value,
                "to_status": self.to_status.value,
                "debt_revision_after": self.debt_revision_after.value,
            }
        )


@dataclass(frozen=True, slots=True)
class DebtPaidAuditPayload:
    """The closed, identifier-free audit payload for an active-to-paid Debt."""

    debt_revision_after: DebtRevision
    source: str = field(default="payment", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.debt_revision_after, DebtRevision):
            raise ValueError("Debt paid audit debt revision is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "source": self.source,
                "debt_revision_after": self.debt_revision_after.value,
            }
        )


@dataclass(frozen=True, slots=True, repr=False)
class DebtOverdueAuditPayload:
    """Closed, identifier-free SYSTEM payload for one overdue transition."""

    source: DebtOverdueSource
    overdue_revision: DebtRevision
    business_date: date
    from_status: DebtStatus = field(default=DebtStatus.ACTIVE, init=False)
    to_status: DebtStatus = field(default=DebtStatus.OVERDUE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, DebtOverdueSource):
            raise ValueError("Debt overdue audit source is invalid")
        if not isinstance(self.overdue_revision, DebtRevision):
            raise ValueError("Debt overdue audit revision is invalid")
        if isinstance(self.business_date, datetime) or not isinstance(
            self.business_date, date
        ):
            raise ValueError("Debt overdue audit business date is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "source": self.source.value,
                "from_status": self.from_status.value,
                "to_status": self.to_status.value,
                "overdue_revision": self.overdue_revision.value,
                "business_date": self.business_date.isoformat(),
            }
        )

    def __repr__(self) -> str:
        return "DebtOverdueAuditPayload(<safe>)"


@dataclass(frozen=True, slots=True, repr=False)
class DebtClawbackAppliedAuditPayload:
    """Closed, identifier-free SYSTEM payload for overdue clawback."""

    source: DebtOverdueSource
    balance_increase_uzs: ClawbackIncreaseUZS
    overdue_revision: DebtRevision
    from_basis: DebtBalanceBasis = field(
        default=DebtBalanceBasis.DISCOUNTED, init=False
    )
    to_basis: DebtBalanceBasis = field(default=DebtBalanceBasis.ORIGINAL, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, DebtOverdueSource):
            raise ValueError("Debt clawback audit source is invalid")
        if not isinstance(self.balance_increase_uzs, ClawbackIncreaseUZS):
            raise ValueError("Debt clawback audit increase is invalid")
        if not isinstance(self.overdue_revision, DebtRevision):
            raise ValueError("Debt clawback audit revision is invalid")

    def as_candidate_metadata(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "source": self.source.value,
                "from_basis": self.from_basis.value,
                "to_basis": self.to_basis.value,
                "balance_increase_uzs": int(self.balance_increase_uzs.value),
                "overdue_revision": self.overdue_revision.value,
            }
        )

    def __repr__(self) -> str:
        return "DebtClawbackAppliedAuditPayload(<safe>)"


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

        is_system_event = self.event_type in {
            AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
            AuditEventType.DEBT_EXPIRED,
            AuditEventType.DEBT_OVERDUE,
            AuditEventType.DEBT_CLAWBACK_APPLIED,
        }
        if is_system_event:
            if (
                self.actor_kind is not AuditActorKind.SYSTEM
                or self.actor_user_id is not None
            ):
                if self.event_type is AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED:
                    raise ValueError("Bootstrap audit actor must be SYSTEM")
                if self.event_type is AuditEventType.DEBT_EXPIRED:
                    raise ValueError("Debt expiry audit actor must be SYSTEM")
                raise ValueError("Debt overdue audit actor must be SYSTEM")
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


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"Audit {field_name} must be timezone-aware")
    return value.astimezone(UTC)


@runtime_checkable
class AuditWriter(Protocol):
    def append(self, *, event: AuditEvent) -> None: ...
