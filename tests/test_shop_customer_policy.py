from decimal import Decimal

import pytest

from app.shop.enums import ShopRole, ShopStatus
from app.shop_customer.contracts import ShopCustomerPolicy
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.policy import (
    OwnCustomerShopProjection,
    ShopCustomerAuthorizationContext,
    ShopCustomerCapability,
    ShopCustomerRosterProjection,
)
from app.shop_customer.values import CreditLimitUzbekistanSom, MaxOpenDebts


def _policy() -> ShopCustomerPolicy:
    return ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal("1000000")),
        max_open_debts=MaxOpenDebts(2),
        list_status=ShopCustomerListStatus.NORMAL,
    )


@pytest.mark.parametrize("role", list(ShopRole))
@pytest.mark.parametrize("is_platform_admin", (False, True))
def test_every_active_role_can_read_and_link_only(
    role: ShopRole, is_platform_admin: bool
) -> None:
    context = ShopCustomerAuthorizationContext(
        role=role,
        shop_status=ShopStatus.ACTIVE,
        membership_active=True,
        is_platform_admin=is_platform_admin,
    )

    assert context.allows(ShopCustomerCapability.READ_ROSTER) is True
    assert context.allows(ShopCustomerCapability.LINK_CUSTOMER) is True
    assert context.allows(ShopCustomerCapability.UPDATE_DEFAULTS) is (
        role is ShopRole.OWNER
    )
    assert context.allows(ShopCustomerCapability.UPDATE_POLICY) is (
        role in {ShopRole.OWNER, ShopRole.MANAGER}
    )


@pytest.mark.parametrize("role", list(ShopRole))
def test_suspended_shop_is_role_scoped_read_only(role: ShopRole) -> None:
    context = ShopCustomerAuthorizationContext(
        role=role,
        shop_status=ShopStatus.SUSPENDED,
        membership_active=True,
        is_platform_admin=True,
    )

    assert context.allows(ShopCustomerCapability.READ_ROSTER) is True
    for capability in (
        ShopCustomerCapability.LINK_CUSTOMER,
        ShopCustomerCapability.UPDATE_DEFAULTS,
        ShopCustomerCapability.UPDATE_POLICY,
    ):
        assert context.allows(capability) is False


@pytest.mark.parametrize("capability", list(ShopCustomerCapability))
def test_no_live_membership_denies_every_capability(
    capability: ShopCustomerCapability,
) -> None:
    context = ShopCustomerAuthorizationContext(
        role=None,
        shop_status=ShopStatus.ACTIVE,
        membership_active=False,
        is_platform_admin=True,
    )

    assert context.allows(capability) is False


def test_read_projections_are_minimal_and_never_contain_m10_identity_fields() -> None:
    roster = ShopCustomerRosterProjection(
        masked_phone="+998*******67",
        policy=_policy(),
    )
    own_view = OwnCustomerShopProjection(shop_name="Samarqand savdo nuqtasi")

    assert tuple(roster.__dataclass_fields__) == ("masked_phone", "policy")
    assert tuple(own_view.__dataclass_fields__) == ("shop_name",)
    exposed = set(roster.__dataclass_fields__) | set(own_view.__dataclass_fields__)
    assert not exposed & {
        "customer_id",
        "user_id",
        "identity",
        "document",
        "jshshir",
        "telegram_link",
    }

    with pytest.raises(ValueError, match="masked phone is invalid"):
        ShopCustomerRosterProjection(masked_phone="+998901234567", policy=_policy())
    with pytest.raises(ValueError, match="shop name is invalid"):
        OwnCustomerShopProjection(shop_name=" shop ")
