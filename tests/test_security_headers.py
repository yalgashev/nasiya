from fastapi import Response
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.main import create_app
from app.security_headers import (
    AUTH_NO_STORE_CACHE_CONTROL,
    CONTENT_SECURITY_POLICY,
    STRICT_TRANSPORT_SECURITY,
    mark_auth_response_no_store,
)
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-security-headers"


def make_settings(
    *,
    environment: str = "testing",
    secure_cookie: bool = False,
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment=environment,
        debug=False,
        database_url="postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test",
        session_cookie_secure=secure_cookie,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def assert_common_security_headers(response) -> None:
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def make_client_with_test_auth_route(*, environment: str = "testing") -> TestClient:
    application = create_app(
        settings=make_settings(
            environment=environment,
            secure_cookie=environment == "production",
        )
    )

    @application.get("/_test/auth-page", response_class=HTMLResponse)
    def read_test_auth_page() -> Response:
        response = HTMLResponse("<main><form method='post'></form></main>")
        return mark_auth_response_no_store(response)

    @application.get("/_test/error")
    def read_test_error() -> None:
        raise RuntimeError("internal test failure secret=hidden")

    return TestClient(application, raise_server_exceptions=False)


def test_security_headers_are_set_on_json_response() -> None:
    client = TestClient(create_app(settings=make_settings()))

    response = client.get("/health")

    assert response.status_code == 200
    assert_common_security_headers(response)


def test_security_headers_are_set_on_html_response_without_inline_assets() -> None:
    client = TestClient(create_app(settings=make_settings()))

    response = client.get("/")

    assert response.status_code == 200
    assert_common_security_headers(response)
    assert "<script" not in response.text.casefold()
    assert "<style" not in response.text.casefold()
    assert "style=" not in response.text.casefold()


def test_security_headers_are_set_on_not_found_response() -> None:
    client = TestClient(create_app(settings=make_settings()))

    response = client.get("/missing")

    assert response.status_code == 404
    assert_common_security_headers(response)


def test_security_headers_are_set_on_static_response() -> None:
    client = TestClient(create_app(settings=make_settings()))

    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert_common_security_headers(response)


def test_hsts_is_not_set_for_local_development_or_testing() -> None:
    client = TestClient(
        create_app(
            settings=make_settings(
                environment="development",
                secure_cookie=False,
            )
        )
    )

    response = client.get("/health")

    assert "strict-transport-security" not in response.headers


def test_hsts_is_set_only_for_production() -> None:
    client = TestClient(
        create_app(
            settings=make_settings(
                environment="production",
                secure_cookie=True,
            )
        )
    )

    response = client.get("/health")

    assert_common_security_headers(response)
    assert response.headers["strict-transport-security"] == STRICT_TRANSPORT_SECURITY


def test_security_headers_do_not_enable_cors() -> None:
    client = TestClient(create_app(settings=make_settings()))

    response = client.get("/health")

    assert "access-control-allow-origin" not in response.headers


def test_auth_html_response_can_be_marked_no_store() -> None:
    client = make_client_with_test_auth_route()

    response = client.get("/_test/auth-page")

    assert response.status_code == 200
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert_common_security_headers(response)


def test_public_home_does_not_require_no_store() -> None:
    client = TestClient(create_app(settings=make_settings()))

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers.get("cache-control") != AUTH_NO_STORE_CACHE_CONTROL


def test_csp_does_not_allow_inline_script_or_style() -> None:
    client = TestClient(create_app(settings=make_settings()))

    response = client.get("/health")
    csp = response.headers["content-security-policy"]

    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "'unsafe-inline'" not in csp


def test_security_headers_are_set_on_error_responses_without_internal_detail() -> None:
    client = make_client_with_test_auth_route()

    response = client.get("/_test/error")

    assert response.status_code == 500
    assert_common_security_headers(response)
    assert "secret=hidden" not in response.text
    assert "internal test failure" not in response.text
