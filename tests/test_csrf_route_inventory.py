from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.auth.deps import validate_csrf
from app.main import create_app
from app.settings import Settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-route-inventory"


def make_application() -> FastAPI:
    return create_app(
        settings=Settings(
            _env_file=None,
            app_environment="testing",
            debug=False,
            database_url="postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test",
            session_cookie_secure=False,
            rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        )
    )


def iter_unsafe_routes(application: FastAPI) -> Iterator[APIRoute]:
    for route in application.routes:
        if not isinstance(route, APIRoute):
            continue
        if (route.methods or set()) & UNSAFE_METHODS:
            yield route


def route_has_csrf_dependency(route: APIRoute) -> bool:
    return any(
        dependency_call is validate_csrf
        for dependency_call in iter_dependency_calls(route.dependant)
    )


def iter_dependency_calls(dependant: Dependant) -> Iterator[object]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from iter_dependency_calls(dependency)


def route_label(route: APIRoute) -> str:
    unsafe_methods = sorted((route.methods or set()) & UNSAFE_METHODS)
    return f"{','.join(unsafe_methods)} {route.path_format}"


def test_production_unsafe_routes_are_csrf_protected() -> None:
    application = make_application()

    unprotected_routes = [
        route_label(route)
        for route in iter_unsafe_routes(application)
        if not route_has_csrf_dependency(route)
    ]

    assert unprotected_routes == []


def test_safe_and_test_only_routes_are_not_in_unsafe_inventory() -> None:
    application = make_application()

    audited_route_paths = {
        route.path_format for route in iter_unsafe_routes(application)
    }

    assert "/" not in audited_route_paths
    assert "/health" not in audited_route_paths
    assert not any(path.startswith("/_test/") for path in audited_route_paths)
