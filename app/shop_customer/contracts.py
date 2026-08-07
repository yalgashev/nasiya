"""Immutable, redacted contracts for the bounded M12 ShopCustomer aggregate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.shop.values import ShopId, UserId
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.values import (
    DEFAULT_CREDIT_LIMIT_UZS,
    DEFAULT_MAX_OPEN_DEBTS,
    CreditLimitUzbekistanSom,
    CustomerId,
    MaxOpenDebts,
    ShopCustomerId,
)


class ShopCustomerLinkOutcome(StrEnum):
    CREATED = "created"
    ALREADY_LINKED = "already_linked"
    CUSTOMER_LINK_UNAVAILABLE = "CUSTOMER_LINK_UNAVAILABLE"


class ShopDefaultPolicyUpdateOutcome(StrEnum):
    CHANGED = "changed"
    NO_CHANGE = "no_change"
    STALE = "stale"


class ShopCustomerPolicyUpdateOutcome(StrEnum):
    CHANGED = "changed"
    NO_CHANGE = "no_change"
    SHOP_CUSTOMER_CHANGED = "SHOP_CUSTOMER_CHANGED"
    SHOP_CUSTOMER_UNAVAILABLE = "SHOP_CUSTOMER_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ShopCustomerRevision:
    value: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, int)
            or isinstance(self.value, bool)
            or self.value < 1
        ):
            raise ValueError("Shop customer revision must be positive")


@dataclass(frozen=True, slots=True)
class ShopCustomerPolicy:
    credit_limit: CreditLimitUzbekistanSom
    max_open_debts: MaxOpenDebts
    list_status: ShopCustomerListStatus

    def __post_init__(self) -> None:
        if not isinstance(self.credit_limit, CreditLimitUzbekistanSom):
            raise ValueError("Shop customer credit limit is invalid")
        if not isinstance(self.max_open_debts, MaxOpenDebts):
            raise ValueError("Shop customer maximum open debts is invalid")
        if not isinstance(self.list_status, ShopCustomerListStatus):
            raise ValueError("Shop customer list status is invalid")


@dataclass(frozen=True, slots=True)
class ShopCustomerCreationSnapshot:
    """The complete policy copied to a newly created relationship."""

    policy: ShopCustomerPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ShopCustomerPolicy):
            raise ValueError("Shop customer creation policy is invalid")
        if self.policy.list_status is not ShopCustomerListStatus.NORMAL:
            raise ValueError("New shop customer must start with normal list status")


@dataclass(frozen=True, slots=True)
class ShopDefaultCreditPolicy:
    """The complete pair copied only to subsequently created links."""

    credit_limit: CreditLimitUzbekistanSom = DEFAULT_CREDIT_LIMIT_UZS
    max_open_debts: MaxOpenDebts = DEFAULT_MAX_OPEN_DEBTS

    def __post_init__(self) -> None:
        if not isinstance(self.credit_limit, CreditLimitUzbekistanSom):
            raise ValueError("Shop default credit limit is invalid")
        if not isinstance(self.max_open_debts, MaxOpenDebts):
            raise ValueError("Shop default maximum open debts is invalid")

    def for_new_link(self) -> ShopCustomerCreationSnapshot:
        return ShopCustomerCreationSnapshot(
            policy=ShopCustomerPolicy(
                credit_limit=self.credit_limit,
                max_open_debts=self.max_open_debts,
                list_status=ShopCustomerListStatus.NORMAL,
            )
        )


@dataclass(frozen=True, slots=True)
class ExpectedShopUpdatedAt:
    """An optimistic token from the authoritative ``Shop.updated_at`` value."""

    value: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _as_utc(self.value, field_name="expected_shop_updated_at"),
        )


@dataclass(frozen=True, slots=True)
class ShopDefaultCreditPolicyUpdate:
    expected_updated_at: ExpectedShopUpdatedAt
    new_defaults: ShopDefaultCreditPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.expected_updated_at, ExpectedShopUpdatedAt):
            raise ValueError("Expected shop update token is invalid")
        if not isinstance(self.new_defaults, ShopDefaultCreditPolicy):
            raise ValueError("New shop defaults are invalid")


@dataclass(frozen=True, slots=True)
class ShopDefaultCreditPolicyUpdateResult:
    outcome: ShopDefaultPolicyUpdateOutcome
    defaults: ShopDefaultCreditPolicy | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ShopDefaultPolicyUpdateOutcome):
            raise ValueError("Shop default policy update outcome is invalid")
        if self.outcome is ShopDefaultPolicyUpdateOutcome.STALE:
            if self.defaults is not None:
                raise ValueError("Stale shop default result cannot disclose defaults")
        elif not isinstance(self.defaults, ShopDefaultCreditPolicy):
            raise ValueError("Shop default result requires a complete policy pair")

    @property
    def emits_audit_event(self) -> bool:
        return self.outcome is ShopDefaultPolicyUpdateOutcome.CHANGED

    @property
    def applies_to_new_links_only(self) -> bool:
        return True

    @property
    def changes_existing_shop_customers(self) -> bool:
        return False


@dataclass(frozen=True, slots=True, repr=False)
class ShopCustomerAggregate:
    """Trusted aggregate state; identifiers never appear in its representation."""

    id: ShopCustomerId = field(repr=False)
    shop_id: ShopId = field(repr=False)
    customer_id: CustomerId = field(repr=False)
    policy: ShopCustomerPolicy
    revision: ShopCustomerRevision
    created_by_user_id: UserId = field(repr=False)
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.id, ShopCustomerId):
            raise ValueError("Shop customer ID is invalid")
        _require_uuid(self.shop_id, field_name="shop_id")
        _require_uuid(self.customer_id, field_name="customer_id")
        if not isinstance(self.policy, ShopCustomerPolicy):
            raise ValueError("Shop customer policy is invalid")
        if not isinstance(self.revision, ShopCustomerRevision):
            raise ValueError("Shop customer revision is invalid")
        _require_uuid(self.created_by_user_id, field_name="created_by_user_id")
        created_at = _as_utc(self.created_at, field_name="created_at")
        updated_at = _as_utc(self.updated_at, field_name="updated_at")
        if updated_at < created_at:
            raise ValueError("Shop customer update time is invalid")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

    def to_projection(self) -> ShopCustomerProjection:
        return ShopCustomerProjection(
            policy=self.policy,
            revision=self.revision,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def __repr__(self) -> str:
        return (
            "ShopCustomerAggregate("
            "id=<redacted>, shop_id=<redacted>, customer_id=<redacted>, "
            f"policy={self.policy!r}, revision={self.revision!r}, "
            "created_by_user_id=<redacted>, "
            f"created_at={self.created_at!r}, updated_at={self.updated_at!r})"
        )


@dataclass(frozen=True, slots=True)
class ShopCustomerProjection:
    """Identifier-free relationship state safe for a trusted presentation adapter."""

    policy: ShopCustomerPolicy
    revision: ShopCustomerRevision
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ShopCustomerPolicy):
            raise ValueError("Shop customer policy is invalid")
        if not isinstance(self.revision, ShopCustomerRevision):
            raise ValueError("Shop customer revision is invalid")
        created_at = _as_utc(self.created_at, field_name="created_at")
        updated_at = _as_utc(self.updated_at, field_name="updated_at")
        if updated_at < created_at:
            raise ValueError("Shop customer update time is invalid")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)


@dataclass(frozen=True, slots=True, repr=False)
class ShopCustomerLinkResult:
    shop_customer_id: ShopCustomerId | None = field(repr=False)
    outcome: ShopCustomerLinkOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ShopCustomerLinkOutcome):
            raise ValueError("Shop customer link outcome is invalid")
        is_success = self.outcome in {
            ShopCustomerLinkOutcome.CREATED,
            ShopCustomerLinkOutcome.ALREADY_LINKED,
        }
        if is_success != isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Shop customer link result identity is invalid")

    @property
    def emits_audit_event(self) -> bool:
        return self.outcome is ShopCustomerLinkOutcome.CREATED

    @property
    def is_idempotent_replay(self) -> bool:
        return self.outcome is ShopCustomerLinkOutcome.ALREADY_LINKED

    @classmethod
    def unavailable(cls) -> ShopCustomerLinkResult:
        """Return the one public outcome for every ineligible target state."""

        return cls(
            shop_customer_id=None,
            outcome=ShopCustomerLinkOutcome.CUSTOMER_LINK_UNAVAILABLE,
        )

    def __repr__(self) -> str:
        return (
            "ShopCustomerLinkResult("
            "shop_customer_id=<redacted>, "
            f"outcome={self.outcome.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class DetachedShopCustomerAuthority:
    """Server-derived actor/current-shop state after the closed auth prephase."""

    actor_user_id: UserId = field(repr=False)
    current_shop_id: ShopId = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.actor_user_id, field_name="actor_user_id")
        _require_uuid(self.current_shop_id, field_name="current_shop_id")

    def __repr__(self) -> str:
        return (
            "DetachedShopCustomerAuthority("
            "actor_user_id=<redacted>, current_shop_id=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class TransientCanonicalShopCustomerPhone:
    """Canonical phone material retained only until locked target resolution."""

    _value: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            normalized = normalize_uzbekistan_phone(self._value)
        except (AttributeError, PhoneNormalizationError):
            raise ValueError("Shop customer phone is invalid") from None
        object.__setattr__(self, "_value", normalized)

    def for_server_lookup(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "TransientCanonicalShopCustomerPhone(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class LockedEligibleShopCustomerTarget:
    """Trusted IDs returned after the live locked eligibility check has succeeded."""

    user_id: UserId = field(repr=False)
    customer_id: CustomerId = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.user_id, field_name="target_user_id")
        _require_uuid(self.customer_id, field_name="target_customer_id")

    def __repr__(self) -> str:
        return (
            "LockedEligibleShopCustomerTarget("
            "user_id=<redacted>, customer_id=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class LinkShopCustomerCommand:
    """A target is resolved from transient phone, never a client-provided UUID."""

    authority: DetachedShopCustomerAuthority = field(repr=False)
    target_phone: TransientCanonicalShopCustomerPhone = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.authority, DetachedShopCustomerAuthority):
            raise ValueError("Shop customer authority is invalid")
        if not isinstance(self.target_phone, TransientCanonicalShopCustomerPhone):
            raise ValueError("Shop customer target phone is invalid")

    def __repr__(self) -> str:
        return "LinkShopCustomerCommand(authority=<redacted>, target_phone=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ShopCustomerPathLocator:
    """An untrusted path locator; the server still scopes it to the live shop."""

    shop_customer_id: ShopCustomerId = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.shop_customer_id, ShopCustomerId):
            raise ValueError("Shop customer locator is invalid")

    def __repr__(self) -> str:
        return "ShopCustomerPathLocator(shop_customer_id=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class UpdateShopCustomerPolicyCommand:
    locator: ShopCustomerPathLocator = field(repr=False)
    expected_revision: ShopCustomerRevision
    new_policy: ShopCustomerPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.locator, ShopCustomerPathLocator):
            raise ValueError("Shop customer locator is invalid")
        if not isinstance(self.expected_revision, ShopCustomerRevision):
            raise ValueError("Shop customer expected revision is invalid")
        if not isinstance(self.new_policy, ShopCustomerPolicy):
            raise ValueError("Shop customer replacement policy is invalid")

    def __repr__(self) -> str:
        return (
            "UpdateShopCustomerPolicyCommand("
            "locator=<redacted>, "
            f"expected_revision={self.expected_revision.value!r}, "
            f"new_policy={self.new_policy!r})"
        )


@dataclass(frozen=True, slots=True)
class ShopCustomerPolicyUpdateResult:
    outcome: ShopCustomerPolicyUpdateOutcome
    revision: ShopCustomerRevision | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ShopCustomerPolicyUpdateOutcome):
            raise ValueError("Shop customer policy update outcome is invalid")
        requires_revision = self.outcome in {
            ShopCustomerPolicyUpdateOutcome.CHANGED,
            ShopCustomerPolicyUpdateOutcome.NO_CHANGE,
        }
        if requires_revision != isinstance(self.revision, ShopCustomerRevision):
            raise ValueError("Shop customer policy update revision is invalid")

    @classmethod
    def changed(
        cls,
        command: UpdateShopCustomerPolicyCommand,
    ) -> ShopCustomerPolicyUpdateResult:
        _require_policy_update_command(command)
        return cls(
            outcome=ShopCustomerPolicyUpdateOutcome.CHANGED,
            revision=ShopCustomerRevision(command.expected_revision.value + 1),
        )

    @classmethod
    def no_change(
        cls,
        command: UpdateShopCustomerPolicyCommand,
    ) -> ShopCustomerPolicyUpdateResult:
        _require_policy_update_command(command)
        return cls(
            outcome=ShopCustomerPolicyUpdateOutcome.NO_CHANGE,
            revision=command.expected_revision,
        )

    @classmethod
    def stale(cls) -> ShopCustomerPolicyUpdateResult:
        return cls(outcome=ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_CHANGED)

    @classmethod
    def unavailable(cls) -> ShopCustomerPolicyUpdateResult:
        return cls(outcome=ShopCustomerPolicyUpdateOutcome.SHOP_CUSTOMER_UNAVAILABLE)

    @property
    def emits_audit_event(self) -> bool:
        return self.outcome is ShopCustomerPolicyUpdateOutcome.CHANGED

    @property
    def updates_timestamp(self) -> bool:
        return self.outcome is ShopCustomerPolicyUpdateOutcome.CHANGED


def _require_policy_update_command(command: object) -> None:
    if not isinstance(command, UpdateShopCustomerPolicyCommand):
        raise ValueError("Shop customer policy update command is invalid")


def _require_uuid(value: object, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"Shop customer {field_name} is invalid")


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"Shop customer {field_name} must be timezone-aware")
    return value.astimezone(UTC)
