import pytest

from app.auth.error_codes import ErrorCode
from app.debt.presentation import DebtWebLanguage
from app.payment.presentation import (
    PAYMENT_ROUTE_CONTRACTS,
    PaymentCustomerCapability,
    PaymentCustomerCapabilityContext,
    PaymentShopCapability,
    PaymentShopCapabilityContext,
    customer_payment_capabilities,
    get_payment_web_error_message,
    shop_payment_capabilities,
)
from app.shop.enums import ShopRole, ShopStatus


@pytest.mark.parametrize("role", list(ShopRole))
def test_active_owner_manager_and_cashier_can_read_and_create_payment(
    role: ShopRole,
) -> None:
    capabilities = shop_payment_capabilities(
        PaymentShopCapabilityContext(
            role=role,
            shop_status=ShopStatus.ACTIVE,
            has_active_membership=True,
        )
    )

    assert capabilities == frozenset(PaymentShopCapability)


@pytest.mark.parametrize("role", list(ShopRole))
def test_suspended_shop_is_payment_read_only_for_active_staff(role: ShopRole) -> None:
    capabilities = shop_payment_capabilities(
        PaymentShopCapabilityContext(
            role=role,
            shop_status=ShopStatus.SUSPENDED,
            has_active_membership=True,
        )
    )

    assert capabilities == {
        PaymentShopCapability.LIST,
        PaymentShopCapability.RECEIPT,
    }


def test_revoked_membership_and_platform_admin_flag_grant_no_payment_authority() -> (
    None
):
    capabilities = shop_payment_capabilities(
        PaymentShopCapabilityContext(
            role=ShopRole.OWNER,
            shop_status=ShopStatus.ACTIVE,
            has_active_membership=False,
            is_platform_admin=True,
        )
    )

    assert capabilities == frozenset()


@pytest.mark.parametrize("shop_status", list(ShopStatus))
def test_own_customer_payment_history_and_receipt_are_always_read_only(
    shop_status: ShopStatus,
) -> None:
    capabilities = customer_payment_capabilities(
        PaymentCustomerCapabilityContext(
            is_own_customer=True,
            shop_status=shop_status,
        )
    )

    assert capabilities == frozenset(PaymentCustomerCapability)
    assert set(PaymentCustomerCapability) == {
        PaymentCustomerCapability.LIST,
        PaymentCustomerCapability.RECEIPT,
    }


def test_non_owner_customer_has_no_payment_authority() -> None:
    assert (
        customer_payment_capabilities(
            PaymentCustomerCapabilityContext(
                is_own_customer=False,
                shop_status=ShopStatus.SUSPENDED,
            )
        )
        == frozenset()
    )


def test_customer_payment_authority_contract_excludes_repayment_risk_gates() -> None:
    field_names = set(PaymentCustomerCapabilityContext.__dataclass_fields__)

    assert field_names == {"is_own_customer", "shop_status"}
    forbidden = {"is_customer_active", "has_telegram", "list_status", "rating"}
    assert field_names.isdisjoint(forbidden)


def test_payment_routes_are_the_six_frozen_named_server_routes() -> None:
    assert [
        (route.name, route.method, route.path, route.form_fields)
        for route in PAYMENT_ROUTE_CONTRACTS
    ] == [
        ("shop_debt_payment_list", "GET", "/shop/debts/{debt_id}/payments", ()),
        ("shop_debt_payment_new", "GET", "/shop/debts/{debt_id}/payments/new", ()),
        (
            "shop_debt_payment_create",
            "POST",
            "/shop/debts/{debt_id}/payments",
            (
                "amount_uzs",
                "method",
                "idempotency_key",
                "expected_revision",
                "csrf_token",
            ),
        ),
        ("shop_payment_receipt", "GET", "/shop/payments/{payment_id}", ()),
        (
            "customer_debt_payment_list",
            "GET",
            "/customer/debts/{debt_id}/payments",
            (),
        ),
        ("customer_payment_receipt", "GET", "/customer/payments/{payment_id}", ()),
    ]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (DebtWebLanguage.UZ_LATN, "To'lov summasi qolgan qarzdan oshadi."),
        (DebtWebLanguage.RU, "Сумма платежа превышает остаток долга."),
    ],
)
def test_payment_error_messages_are_localized_and_safe(
    language: DebtWebLanguage,
    expected: str,
) -> None:
    assert (
        get_payment_web_error_message(
            language, ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE
        )
        == expected
    )
    assert get_payment_web_error_message(language, ErrorCode.PAYMENT_UNAVAILABLE)
    assert get_payment_web_error_message(language, ErrorCode.DEBT_CHANGED)
    assert get_payment_web_error_message(language, ErrorCode.DEBT_NOT_PAYABLE)
    assert get_payment_web_error_message(language, ErrorCode.OFFER_CHANGED) is None
