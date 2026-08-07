"""Frozen M12 route and form contracts; this module deliberately has no router."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from app.shop_customer.contracts import (
    ExpectedShopUpdatedAt,
    ShopCustomerPolicy,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
    TransientCanonicalShopCustomerPhone,
)

SHOP_CUSTOMERS_PATH: Final = "/shop/customers"
SHOP_CUSTOMER_LINK_PATH: Final = "/shop/customers/link"
SHOP_CUSTOMER_POLICY_PATH_TEMPLATE: Final = "/shop/customers/{shop_customer_id}/policy"
SHOP_SETTINGS_CREDIT_PATH: Final = "/shop/settings/credit"
CUSTOMER_SHOPS_PATH: Final = "/customer/shops"
SHOP_CUSTOMER_ROSTER_PAGE_SIZE: Final = 50
SHOP_CUSTOMER_ROSTER_ORDER: Final = ("created_at", "id")


@dataclass(frozen=True, slots=True, repr=False)
class ShopCustomerLinkForm:
    phone: TransientCanonicalShopCustomerPhone = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.phone, TransientCanonicalShopCustomerPhone):
            raise ValueError("Shop customer link form phone is invalid")

    def __repr__(self) -> str:
        return "ShopCustomerLinkForm(phone=<redacted>)"


@dataclass(frozen=True, slots=True)
class ShopCustomerPolicyForm:
    expected_revision: ShopCustomerRevision
    new_policy: ShopCustomerPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.expected_revision, ShopCustomerRevision):
            raise ValueError("Shop customer policy form revision is invalid")
        if not isinstance(self.new_policy, ShopCustomerPolicy):
            raise ValueError("Shop customer policy form values are invalid")


@dataclass(frozen=True, slots=True)
class ShopDefaultCreditPolicyForm:
    expected_updated_at: ExpectedShopUpdatedAt
    new_defaults: ShopDefaultCreditPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.expected_updated_at, ExpectedShopUpdatedAt):
            raise ValueError("Shop default policy form token is invalid")
        if not isinstance(self.new_defaults, ShopDefaultCreditPolicy):
            raise ValueError("Shop default policy form values are invalid")
