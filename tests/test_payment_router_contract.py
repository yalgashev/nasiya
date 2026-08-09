from inspect import getsource
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.auth.deps import get_current_session_context, get_current_time
from app.main import create_app
from app.payment.dependencies import (
    get_detached_current_shop_payment_actor_context,
    get_detached_current_shop_payment_read_actor_context,
)
from app.payment.presentation import PAYMENT_ROUTE_CONTRACTS
from app.payment.router import router
from app.settings import Settings


def _application() -> FastAPI:
    return create_app(
        settings=Settings(
            _env_file=None,
            app_environment="testing",
            debug=False,
            database_url=(
                "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test"
            ),
            session_cookie_secure=False,
            rate_limit_hmac_key="test-payment-router-contract-key",
        )
    )


def test_six_frozen_payment_routes_are_registered_once_with_exact_names() -> None:
    observed = {
        (route.name, method, route.path_format)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    expected = {
        (contract.name, contract.method, contract.path)
        for contract in PAYMENT_ROUTE_CONTRACTS
    }

    assert observed == expected
    application = _application()
    included_payment_routers = [
        route
        for route in application.routes
        if getattr(route, "original_router", None) is router
    ]
    assert len(included_payment_routers) == 1


def test_payment_router_has_mode_specific_authority_and_one_coordinator_path() -> None:
    source = getsource(__import__("app.payment.router", fromlist=["router"]))

    assert source.count("get_detached_current_shop_payment_read_actor_context") == 3
    assert source.count("get_detached_current_shop_payment_actor_context") == 2
    assert "get_current_session_context" in source
    assert "get_own_customer_payment_history_view" in source
    assert "get_own_customer_payment_receipt_view" in source
    assert source.count("record_debt_payment(") == 1
    assert source.count("database_session_factory.begin()") == 1
    assert "void" not in source.casefold()
    assert '"/admin' not in source
    assert '"/api' not in source


def test_payment_routes_use_read_or_csrf_detached_boundaries() -> None:
    payment_routes = {
        route.name: route for route in router.routes if isinstance(route, APIRoute)
    }

    def calls(route: APIRoute) -> set[object]:
        return {
            dependency.call
            for dependency in route.dependant.dependencies
            if dependency.call is not None
        }

    for name in (
        "shop_debt_payment_list",
        "shop_payment_receipt",
    ):
        assert get_detached_current_shop_payment_read_actor_context in calls(
            payment_routes[name]
        )
    for name in (
        "shop_debt_payment_list",
        "shop_debt_payment_new",
        "shop_payment_receipt",
        "customer_debt_payment_list",
        "customer_payment_receipt",
    ):
        assert get_current_time in calls(payment_routes[name])
    assert get_current_session_context in calls(payment_routes["shop_debt_payment_new"])
    assert get_detached_current_shop_payment_actor_context in calls(
        payment_routes["shop_debt_payment_create"]
    )


def test_debt_detail_navigation_stays_mode_specific_and_localized() -> None:
    shop_detail = Path("app/templates/debt/shop_detail.html").read_text(
        encoding="utf-8"
    )
    customer_detail = Path("app/templates/debt/customer_detail.html").read_text(
        encoding="utf-8"
    )
    copy_source = Path("app/debt/web_presentation.py").read_text(encoding="utf-8")

    assert "/shop/debts/{{ detail.debt_id.as_uuid() }}/payments" in shop_detail
    assert "/shop/debts/{{ detail.debt_id.as_uuid() }}/payments/new" in shop_detail
    assert "can_record_payment" in shop_detail
    assert "/customer/debts/{{ detail.debt_id.as_uuid() }}/payments" in customer_detail
    assert "/shop/" not in customer_detail
    assert '"payment_history"' in copy_source
    assert '"record_payment"' in copy_source


def test_debt_ssr_uses_the_application_composed_progress_reader() -> None:
    source = Path("app/debt/router.py").read_text(encoding="utf-8")
    adapter = Path("app/debt/payment_progress.py").read_text(encoding="utf-8")

    assert "app.payment" not in source
    assert "debt_web_payment_progress_reader" in source
    assert "DebtWebPaymentProgressReader" in adapter
