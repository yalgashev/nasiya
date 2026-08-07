"""Role capability and safe read-projection contracts for M12."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.shop.enums import ShopRole, ShopStatus
from app.shop_customer.contracts import ShopCustomerPolicy

_MASKED_PHONE_PATTERN = re.compile(r"\+998\*{7}[0-9]{2}", flags=re.ASCII)


class ShopCustomerCapability(StrEnum):
    READ_ROSTER = "read_roster"
    LINK_CUSTOMER = "link_customer"
    UPDATE_DEFAULTS = "update_defaults"
    UPDATE_POLICY = "update_policy"


@dataclass(frozen=True, slots=True)
class ShopCustomerAuthorizationContext:
    """Live membership-derived capability context; admin status grants nothing."""

    role: ShopRole | None
    shop_status: ShopStatus | None
    membership_active: bool
    is_platform_admin: bool

    def __post_init__(self) -> None:
        if self.role is not None and not isinstance(self.role, ShopRole):
            raise ValueError("Shop customer role is invalid")
        if self.shop_status is not None and not isinstance(
            self.shop_status,
            ShopStatus,
        ):
            raise ValueError("Shop customer shop status is invalid")
        if not isinstance(self.membership_active, bool):
            raise ValueError("Shop customer membership state is invalid")
        if not isinstance(self.is_platform_admin, bool):
            raise ValueError("Shop customer platform admin state is invalid")

    def allows(self, capability: ShopCustomerCapability) -> bool:
        if not isinstance(capability, ShopCustomerCapability):
            raise ValueError("Shop customer capability is invalid")
        if not self.membership_active or self.role is None or self.shop_status is None:
            return False
        if capability is ShopCustomerCapability.READ_ROSTER:
            return self.shop_status in {ShopStatus.ACTIVE, ShopStatus.SUSPENDED}
        if self.shop_status is not ShopStatus.ACTIVE:
            return False
        if capability is ShopCustomerCapability.LINK_CUSTOMER:
            return True
        if capability is ShopCustomerCapability.UPDATE_DEFAULTS:
            return self.role is ShopRole.OWNER
        return capability is ShopCustomerCapability.UPDATE_POLICY and self.role in {
            ShopRole.OWNER,
            ShopRole.MANAGER,
        }


@dataclass(frozen=True, slots=True)
class ShopCustomerRosterProjection:
    """The shop roster has only a masked phone and relationship policy values."""

    masked_phone: str
    policy: ShopCustomerPolicy

    def __post_init__(self) -> None:
        if (
            not isinstance(self.masked_phone, str)
            or _MASKED_PHONE_PATTERN.fullmatch(self.masked_phone) is None
        ):
            raise ValueError("Shop customer masked phone is invalid")
        if not isinstance(self.policy, ShopCustomerPolicy):
            raise ValueError("Shop customer roster policy is invalid")


@dataclass(frozen=True, slots=True)
class OwnCustomerShopProjection:
    """The customer-owned view exposes only the linked shop's name."""

    shop_name: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.shop_name, str)
            or not 2 <= len(self.shop_name) <= 120
            or self.shop_name != self.shop_name.strip()
        ):
            raise ValueError("Shop customer own-view shop name is invalid")
