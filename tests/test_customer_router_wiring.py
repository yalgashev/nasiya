from collections.abc import Iterator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.auth.router import router as auth_router
from app.customer.router import router as customer_router
from app.main import create_app


def iter_api_routes(application: FastAPI) -> Iterator[APIRoute]:
    yield from iter_routes(application.routes)


def iter_routes(routes: list[object]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue

        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            yield from iter_routes(included_router.routes)

        nested_routes = getattr(route, "routes", None)
        if nested_routes:
            yield from iter_routes(nested_routes)


def test_customer_router_has_customer_prefix_and_expected_onboarding_routes() -> None:
    assert customer_router.prefix == "/customer"
    customer_routes = {
        route.path_format: route.methods
        for route in customer_router.routes
        if isinstance(route, APIRoute)
    }
    assert customer_routes == {
        "/customer/onboarding": {"GET"},
        "/customer/onboarding/start": {"POST"},
        "/customer/profile": {"GET"},
    }


def test_create_app_wires_customer_router_without_connecting_to_database() -> None:
    included_routers = []

    original_include_router = FastAPI.include_router

    def capture_include_router(self: FastAPI, router, *args, **kwargs):
        included_routers.append(router)
        return original_include_router(self, router, *args, **kwargs)

    with (
        patch.object(FastAPI, "include_router", capture_include_router),
        patch.object(Engine, "connect") as connect_mock,
    ):
        application = create_app()

    connect_mock.assert_not_called()
    assert auth_router in included_routers
    assert customer_router in included_routers
    assert application.state.database_engine is not None


def test_customer_router_adds_only_onboarding_routes() -> None:
    application = create_app()

    customer_routes = [
        route
        for route in iter_api_routes(application)
        if route.path_format.startswith("/customer")
    ]

    assert {
        route.path_format: route.methods for route in customer_routes
    } == {
        "/customer/onboarding": {"GET"},
        "/customer/onboarding/start": {"POST"},
        "/customer/profile": {"GET"},
    }


def test_m1_routes_still_respond_and_auth_route_inventory_is_unchanged() -> None:
    client = TestClient(create_app())

    home_response = client.get("/")
    health_response = client.get("/health")
    auth_routes: dict[str, set[str]] = {}
    for route in iter_api_routes(client.app):  # type: ignore[arg-type]
        if route.path_format.startswith("/auth"):
            auth_routes.setdefault(route.path_format, set()).update(
                route.methods or set(),
            )

    assert home_response.status_code == 200
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert auth_routes["/auth/login"] == {"GET", "POST"}
    assert auth_routes["/auth/account"] == {"GET"}
    assert auth_routes["/auth/logout"] == {"POST"}
    assert auth_routes["/auth/sessions"] == {"GET"}
    assert auth_routes["/auth/sessions/{session_id}/revoke"] == {"POST"}
    assert auth_routes["/auth/sessions/revoke-others"] == {"POST"}
