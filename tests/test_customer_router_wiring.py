from collections.abc import Iterator
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.auth.deps import validate_csrf
from app.auth.router import router as auth_router
from app.customer.router import router as customer_router
from app.main import create_app

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
EXPECTED_CUSTOMER_ROUTES = {
    "/customer/onboarding": {"GET"},
    "/customer/onboarding/start": {"POST"},
    "/customer/profile": {"GET"},
}


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
    assert customer_routes == EXPECTED_CUSTOMER_ROUTES


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

    assert {route.path_format: route.methods for route in customer_routes} == (
        EXPECTED_CUSTOMER_ROUTES
    )


def test_customer_routes_forbid_external_ids_and_scope_drift() -> None:
    application = create_app()
    customer_routes = [
        route
        for route in iter_api_routes(application)
        if route.path_format.startswith("/customer")
    ]

    assert {route.path_format: route.methods for route in customer_routes} == (
        EXPECTED_CUSTOMER_ROUTES
    )
    for route in customer_routes:
        assert route.dependant.path_params == []
        assert "{" not in route.path_format
        assert [
            query_param.name
            for query_param in route.dependant.query_params
            if query_param.name in {"customer_id", "user_id"}
        ] == []
        assert [
            body_param.name
            for body_param in route.dependant.body_params
            if body_param.name in {"customer_id", "user_id"}
        ] == []

    all_route_paths = {route.path_format for route in iter_api_routes(application)}
    assert "/register" not in all_route_paths
    assert "/auth/register" not in all_route_paths
    assert not any("activation" in path for path in all_route_paths)

    get_customer_routes = {
        route.path_format
        for route in customer_routes
        if route.methods == {"GET"}
    }
    assert get_customer_routes == {"/customer/onboarding", "/customer/profile"}
    assert "/customer/onboarding/start" not in get_customer_routes

    customer_unsafe_routes = [
        route
        for route in customer_routes
        if (route.methods or set()) & UNSAFE_METHODS
    ]
    assert [route.path_format for route in customer_unsafe_routes] == [
        "/customer/onboarding/start"
    ]
    assert any(
        dependency.call is validate_csrf
        for dependency in customer_unsafe_routes[0].dependant.dependencies
    )


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
