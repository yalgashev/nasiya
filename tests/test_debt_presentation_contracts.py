from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.auth.error_codes import ErrorCode
from app.debt.contracts import DebtAggregate
from app.debt.presentation import (
    DEBT_ROUTE_CONTRACTS,
    DebtCustomerCapability,
    DebtCustomerCapabilityContext,
    DebtSafeDetailProjection,
    DebtSafeListProjection,
    DebtShopCapability,
    DebtShopCapabilityContext,
    DebtWebLanguage,
    customer_capabilities,
    get_debt_web_error_message,
    present_debt_detail,
    present_debt_list_item,
    shop_capabilities,
)
from app.debt.values import (
    DebtId,
    DiscountBasisPoints,
    DiscountedAmountUZS,
    OriginalAmountUZS,
    ShopCustomerId,
    UserId,
)
from app.shop.enums import ShopRole, ShopStatus


def _debt() -> DebtAggregate:
    return DebtAggregate.create_pending(
        debt_id=DebtId(uuid4()),
        shop_customer_id=ShopCustomerId(uuid4()),
        created_by_user_id=UserId(uuid4()),
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discount_basis_points=DiscountBasisPoints(100),
        discounted_amount=DiscountedAmountUZS(Decimal("990")),
        due_date=date(2026, 5, 4),
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )


def test_shop_and_customer_capabilities_keep_suspension_and_admin_boundaries() -> None:
    active_staff = DebtShopCapabilityContext(
        role=ShopRole.CASHIER,
        shop_status=ShopStatus.ACTIVE,
        has_active_membership=True,
    )
    assert shop_capabilities(active_staff) == frozenset(DebtShopCapability)
    assert shop_capabilities(
        DebtShopCapabilityContext(
            role=ShopRole.OWNER,
            shop_status=ShopStatus.SUSPENDED,
            has_active_membership=True,
        )
    ) == {DebtShopCapability.LIST, DebtShopCapability.DETAIL}
    assert not shop_capabilities(
        DebtShopCapabilityContext(
            role=None,
            shop_status=ShopStatus.ACTIVE,
            has_active_membership=False,
            is_platform_admin=True,
        )
    )

    suspended_customer = DebtCustomerCapabilityContext(
        is_own_customer=True,
        is_customer_active=True,
        shop_status=ShopStatus.SUSPENDED,
    )
    assert DebtCustomerCapability.REJECT in customer_capabilities(suspended_customer)
    assert DebtCustomerCapability.ACCEPT not in customer_capabilities(
        suspended_customer
    )
    assert not customer_capabilities(
        DebtCustomerCapabilityContext(
            is_own_customer=False,
            is_customer_active=True,
            shop_status=ShopStatus.ACTIVE,
            is_platform_admin=True,
        )
    )


def test_safe_debt_projections_exclude_internal_ids_keys_and_reasons() -> None:
    debt = _debt()
    list_item = present_debt_list_item(debt)
    detail = present_debt_detail(debt)

    assert isinstance(list_item, DebtSafeListProjection)
    assert isinstance(detail, DebtSafeDetailProjection)
    forbidden = {"id", "reason", "key", "user", "shop_customer", "creator"}
    for projection in (DebtSafeListProjection, DebtSafeDetailProjection):
        assert forbidden.isdisjoint(field.name for field in fields(projection))
    assert str(debt.id.as_uuid()) not in repr(detail)


def test_exact_nine_debt_routes_and_post_form_contracts_are_frozen() -> None:
    assert tuple((route.method, route.path) for route in DEBT_ROUTE_CONTRACTS) == (
        ("GET", "/shop/customers/{shop_customer_id}/debts"),
        ("GET", "/shop/customers/{shop_customer_id}/debts/new"),
        ("POST", "/shop/customers/{shop_customer_id}/debts"),
        ("GET", "/shop/debts/{debt_id}"),
        ("POST", "/shop/debts/{debt_id}/cancel"),
        ("GET", "/customer/debts"),
        ("GET", "/customer/debts/{debt_id}"),
        ("POST", "/customer/debts/{debt_id}/accept"),
        ("POST", "/customer/debts/{debt_id}/reject"),
    )
    post_forms = {
        route.path: route.form_fields
        for route in DEBT_ROUTE_CONTRACTS
        if route.method == "POST"
    }
    assert post_forms["/shop/customers/{shop_customer_id}/debts"] == (
        "original_amount_uzs",
        "discount_percent",
        "due_date",
        "idempotency_key",
        "csrf_token",
    )
    assert post_forms["/shop/debts/{debt_id}/cancel"] == (
        "expected_revision",
        "reason",
        "csrf_token",
    )
    assert post_forms["/customer/debts/{debt_id}/accept"] == (
        "expected_revision",
        "language",
        "displayed_offer_text_id",
        "csrf_token",
    )
    assert post_forms["/customer/debts/{debt_id}/reject"] == (
        "expected_revision",
        "reason",
        "csrf_token",
    )


def test_exact_m13_errors_have_safe_feature_local_uzbek_and_russian_copy() -> None:
    m13_errors = (
        ErrorCode.CUSTOMER_NOT_ACTIVE,
        ErrorCode.CUSTOMER_BLACKLISTED,
        ErrorCode.CUSTOMER_RATING_BLOCKED,
        ErrorCode.CREDIT_LIMIT_EXCEEDED,
        ErrorCode.MAX_OPEN_DEBTS,
        ErrorCode.DEBT_UNAVAILABLE,
        ErrorCode.DEBT_NOT_PENDING,
        ErrorCode.DEBT_EXPIRED,
        ErrorCode.IDEMPOTENCY_CONFLICT,
    )
    for language in DebtWebLanguage:
        messages = tuple(
            get_debt_web_error_message(language, error_code)
            for error_code in m13_errors
        )
        assert all(messages)
        assert all("{" not in message for message in messages if message is not None)
