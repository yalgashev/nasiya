import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.auth.deps import get_current_time
from app.main import create_app
from app.otp.web_presentation import (
    OtpWebLanguage,
    get_otp_web_copy,
    resolve_otp_web_language,
)
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OTP_TEMPLATES = [
    PROJECT_ROOT / "app/templates/auth/otp_request.html",
    PROJECT_ROOT / "app/templates/auth/otp_verify.html",
]
APP_CSS = PROJECT_ROOT / "app/static/css/app.css"
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-otp-web-hardening"
TEST_OTP_HMAC_KEY = "test-otp-hmac-key-for-otp-web-hardening-at-least-32"


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
        otp_hmac_key=TEST_OTP_HMAC_KEY,
    )


def make_client(engine: Engine) -> TestClient:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: datetime(
        2026,
        7,
        29,
        22,
        0,
        tzinfo=UTC,
    )
    return TestClient(application, client=("203.0.113.90", 50_000))


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def iter_api_routes(routes: list[object]):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            yield from iter_api_routes(included_router.routes)
            continue
        nested_routes = getattr(route, "routes", None)
        if nested_routes:
            yield from iter_api_routes(nested_routes)


def assert_otp_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_otp_copy_catalog_has_matching_uz_and_ru_keys() -> None:
    uz_copy = get_otp_web_copy(OtpWebLanguage.UZ_LATN)
    ru_copy = get_otp_web_copy(OtpWebLanguage.RU)

    assert set(uz_copy) == set(ru_copy)
    assert uz_copy["verify_notice"].startswith("Agar kiritilgan telefon")
    assert ru_copy["verify_notice"].startswith("Если введенный телефон")


def test_otp_language_resolution_is_bounded_and_cookie_wins() -> None:
    assert resolve_otp_web_language("ru", "uz;q=1") is OtpWebLanguage.RU
    assert resolve_otp_web_language("uz-Latn", "ru;q=1") is OtpWebLanguage.UZ_LATN
    assert resolve_otp_web_language(None, "ru-RU,uz;q=0.5") is OtpWebLanguage.RU
    assert resolve_otp_web_language("fr", "fr;q=1") is OtpWebLanguage.UZ_LATN


def test_otp_route_family_is_exact_and_unsafe_routes_are_post_only() -> None:
    application = create_app(
        settings=Settings(
            _env_file=None,
            app_environment="testing",
            debug=False,
            database_url="postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test",
            session_cookie_secure=False,
            rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
            otp_hmac_key=TEST_OTP_HMAC_KEY,
        )
    )
    methods_by_path: dict[str, set[str]] = {}
    for route in iter_api_routes(application.routes):
        if route.path_format.startswith("/auth/otp"):
            methods_by_path.setdefault(route.path_format, set()).update(
                route.methods or set()
            )

    assert methods_by_path == {
        "/auth/otp": {"GET"},
        "/auth/otp/request": {"POST"},
        "/auth/otp/verify": {"GET", "POST"},
        "/auth/otp/new-code": {"POST"},
    }


def test_otp_templates_are_autoescaped_static_and_identifier_free() -> None:
    for template_path in OTP_TEMPLATES:
        template = template_path.read_text(encoding="utf-8")
        assert "<script" not in template
        assert "<style" not in template
        assert "style=" not in template
        assert "|safe" not in template
        assert "localStorage" not in template
        assert "sessionStorage" not in template
        assert 'type="number"' not in template
        for forbidden in (
            "challenge_id",
            "dispatch_id",
            "telegram_chat_id",
            "delivery_status",
            "session_token",
            "otp_hmac_key",
            "active_shop",
            "require_shop_staff",
            "resolve_current_shop",
            "OTP_INVALID",
            "OTP_EXPIRED",
            "OTP_BURNED",
        ):
            assert forbidden not in template


def test_otp_templates_have_accessible_labels_errors_and_mobile_inputs() -> None:
    request_template = OTP_TEMPLATES[0].read_text(encoding="utf-8")
    verify_template = OTP_TEMPLATES[1].read_text(encoding="utf-8")

    assert '<label for="otp-phone">' in request_template
    assert 'id="otp-request-error"' in request_template
    assert 'role="alert"' in request_template
    assert 'aria-live="polite"' in request_template
    assert '<label for="otp-code">' in verify_template
    assert 'id="otp-verify-error"' in verify_template
    assert 'role="alert"' in verify_template
    assert 'aria-live="polite"' in verify_template
    assert 'inputmode="numeric"' in verify_template
    assert 'autocomplete="one-time-code"' in verify_template
    assert 'maxlength="6"' in verify_template
    assert 'pattern="[0-9]{6}"' in verify_template


def test_otp_css_keeps_touch_targets_focus_and_small_viewports() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "min-height: 44px" in css
    assert "focus-visible" in css
    assert '[role="alert"]:not(:empty)::before' in css
    assert ".otp-page" in css
    assert "@media (max-width: 430px)" in css
    assert "@media (max-width: 320px)" in css
    assert "width: min(100% - 24px, 640px);" in css
    assert "width: min(100% - 16px, 640px);" in css


def test_otp_routes_preserve_no_store_security_headers_and_no_code_in_url(
    m2_test_database: Engine,
) -> None:
    client = make_client(m2_test_database)
    request_page = client.get("/auth/otp")
    verify_page = client.get("/auth/otp/verify")
    csrf_token = extract_hidden_csrf_token(request_page.text)
    request_post = client.post(
        "/auth/otp/request",
        data={"csrf_token": csrf_token, "phone": "+998900009999"},
        follow_redirects=False,
    )
    verify_post = client.post(
        "/auth/otp/verify",
        data={"csrf_token": csrf_token, "code": "123456"},
        follow_redirects=False,
    )
    new_code_post = client.post(
        "/auth/otp/new-code",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    for response in (
        request_page,
        verify_page,
        request_post,
        verify_post,
        new_code_post,
    ):
        assert_otp_security_headers(response)
        assert "123456" not in response.headers.get("location", "")
        assert "+998900009999" not in response.headers.get("location", "")
        assert "OTP_" not in response.text
