from datetime import UTC, datetime
from decimal import Decimal

from app.auth.error_codes import ErrorCode
from app.shop_customer.contracts import (
    ExpectedShopUpdatedAt,
    ShopCustomerPolicy,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
    TransientCanonicalShopCustomerPhone,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.presentation import (
    ShopCustomerWebLanguage,
    get_shop_customer_web_error_message,
    resolve_shop_customer_web_language,
)
from app.shop_customer.values import CreditLimitUzbekistanSom, MaxOpenDebts
from app.shop_customer.web_contracts import (
    CUSTOMER_SHOPS_PATH,
    SHOP_CUSTOMER_LINK_PATH,
    SHOP_CUSTOMER_POLICY_PATH_TEMPLATE,
    SHOP_CUSTOMER_ROSTER_ORDER,
    SHOP_CUSTOMER_ROSTER_PAGE_SIZE,
    SHOP_CUSTOMERS_PATH,
    SHOP_SETTINGS_CREDIT_PATH,
    ShopCustomerLinkForm,
    ShopCustomerPolicyForm,
    ShopDefaultCreditPolicyForm,
)


def _policy() -> ShopCustomerPolicy:
    return ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal("1000000")),
        max_open_debts=MaxOpenDebts(2),
        list_status=ShopCustomerListStatus.NORMAL,
    )


def test_m12_route_and_form_contracts_are_exact_and_exclude_client_target_ids() -> None:
    assert (
        SHOP_CUSTOMERS_PATH,
        SHOP_CUSTOMER_LINK_PATH,
        SHOP_CUSTOMER_POLICY_PATH_TEMPLATE,
        SHOP_SETTINGS_CREDIT_PATH,
        CUSTOMER_SHOPS_PATH,
    ) == (
        "/shop/customers",
        "/shop/customers/link",
        "/shop/customers/{shop_customer_id}/policy",
        "/shop/settings/credit",
        "/customer/shops",
    )
    assert SHOP_CUSTOMER_ROSTER_PAGE_SIZE == 50
    assert SHOP_CUSTOMER_ROSTER_ORDER == ("created_at", "id")

    link = ShopCustomerLinkForm(TransientCanonicalShopCustomerPhone("901234567"))
    policy = ShopCustomerPolicyForm(ShopCustomerRevision(1), _policy())
    defaults = ShopDefaultCreditPolicyForm(
        ExpectedShopUpdatedAt(datetime.now(UTC)),
        ShopDefaultCreditPolicy(),
    )
    assert tuple(link.__dataclass_fields__) == ("phone",)
    assert tuple(policy.__dataclass_fields__) == ("expected_revision", "new_policy")
    assert tuple(defaults.__dataclass_fields__) == (
        "expected_updated_at",
        "new_defaults",
    )
    assert "customer_id" not in repr(link)
    assert "+998901234567" not in repr(link)


def test_m12_presentation_is_localized_and_never_discloses_target_gates() -> None:
    assert (
        resolve_shop_customer_web_language(None, "ru-RU,uz;q=0.8")
        is ShopCustomerWebLanguage.RU
    )
    assert (
        resolve_shop_customer_web_language(None, "uz-Latn,ru;q=0.8")
        is ShopCustomerWebLanguage.UZ_LATN
    )
    messages = {
        language: get_shop_customer_web_error_message(
            language,
            ErrorCode.CUSTOMER_LINK_UNAVAILABLE,
        )
        for language in ShopCustomerWebLanguage
    }
    assert all(message is not None for message in messages.values())
    rendered = repr(messages)
    for forbidden in ("phone", "Telegram", "draft", "disabled", "SQL", "UUID"):
        assert forbidden.casefold() not in rendered.casefold()
