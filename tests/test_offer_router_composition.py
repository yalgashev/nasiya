from collections import Counter
from pathlib import Path

from fastapi.routing import APIRoute

from app.auth.router import router as auth_router
from app.main import create_app
from app.offers.router import router as offers_router

EXPECTED_OFFER_ROUTE_INVENTORY = {
    ("GET", "/admin/offers"),
    ("GET", "/admin/offers/new"),
    ("POST", "/admin/offers"),
    ("GET", "/admin/offers/{offer_version_id}"),
    ("POST", "/admin/offers/{offer_version_id}/texts/{language}"),
    ("POST", "/admin/offers/{offer_version_id}/approve"),
    ("POST", "/admin/offers/{offer_version_id}/make-current"),
    ("GET", "/auth/registration-offer"),
    ("POST", "/auth/registration-offer/accept"),
}


def _route_inventory(routes: list[object]) -> set[tuple[str, str]]:
    return {
        (method, route.path_format)
        for route in routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_offer_router_owns_the_exact_m9_route_inventory() -> None:
    assert _route_inventory(offers_router.routes) == EXPECTED_OFFER_ROUTE_INVENTORY
    assert not {
        item
        for item in _route_inventory(auth_router.routes)
        if item[1].startswith("/admin/offers")
        or item[1].startswith("/auth/registration-offer")
    }


def test_create_app_includes_offer_router_exactly_once() -> None:
    application = create_app()
    included_routers = [
        route.original_router
        for route in application.routes
        if getattr(route, "original_router", None) is not None
    ]

    assert included_routers.count(auth_router) == 1
    assert included_routers.count(offers_router) == 1
    composed_offer_inventory = Counter(
        item
        for included_router in included_routers
        for item in _route_inventory(included_router.routes)
        if item in EXPECTED_OFFER_ROUTE_INVENTORY
    )
    assert composed_offer_inventory == Counter(
        {item: 1 for item in EXPECTED_OFFER_ROUTE_INVENTORY}
    )


def test_offer_router_composition_is_explicit_and_not_route_list_mutation() -> None:
    main_source = Path("app/main.py").read_text(encoding="utf-8")
    auth_source = Path("app/auth/router.py").read_text(encoding="utf-8")

    assert "from app.offers.router import router as offers_router" in main_source
    assert "application.include_router(offers_router)" in main_source
    assert "from app.offers.router" not in auth_source
    assert "routes.extend" not in auth_source
