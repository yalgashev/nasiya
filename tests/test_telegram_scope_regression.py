import re
import tomllib
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.main as app_main
from app.auth.deps import get_current_time, validate_csrf
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_activation.router import validate_activation_csrf
from app.db import Base, create_database_session_factory
from app.main import create_app
from app.settings import Settings
from app.telegram.models import (
    TelegramLink,
    TelegramLinkEvent,
    TelegramLinkToken,
    TelegramPollingState,
    TelegramUpdateFailure,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-telegram-scope-regression"

FORBIDDEN_PRODUCTION_DEPENDENCIES = {
    "aiogram",
    "apscheduler",
    "aiohttp",
    "celery",
    "dramatiq",
    "huey",
    "python-telegram-bot",
    "qrcode",
    "redis",
    "requests",
    "rq",
}
FORBIDDEN_APP_RUNTIME_MARKERS = (
    "setwebhook",
    "telegram.bot(",
    "aiogram",
    "python-telegram-bot",
    "apscheduler",
    "scheduler",
    "cron",
    "redis",
    "otpdeliveryprovider",
    "otp_code",
    "x-forwarded-for",
    "proxy_middleware",
)
FORBIDDEN_CUSTOMER_FEATURE_TEXT = (
    "telegram",
    "otp",
    "qr",
    "activation",
    "activated",
    "faollashtirish",
    "bot",
)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


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


def iter_dependency_calls(dependant: Dependant) -> Iterator[object]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from iter_dependency_calls(dependency)


def route_has_csrf_dependency(route: APIRoute) -> bool:
    return any(
        dependency_call in {validate_csrf, validate_activation_csrf}
        for dependency_call in iter_dependency_calls(route.dependant)
    )


def production_dependency_names() -> set[str]:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]
    names = set()
    for dependency in dependencies:
        name = re.split(r"[\[<>=~! ]", dependency, maxsplit=1)[0]
        names.add(name.casefold())
    return names


def app_source_text(*, excluded_prefixes: tuple[str, ...] = ()) -> str:
    source_parts = []
    for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        if relative_path.startswith(excluded_prefixes):
            continue
        source_parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(source_parts)


def template_text(*relative_paths: str) -> str:
    return "\n".join(
        (PROJECT_ROOT / "app" / "templates" / relative_path).read_text(encoding="utf-8")
        for relative_path in relative_paths
    )


def set_client_session_cookie(
    client: TestClient,
    settings: Settings,
    raw_token,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )


def test_no_production_telegram_route_webhook_callback_or_public_csrf_bypass(
    m2_test_database: Engine,
) -> None:
    settings = make_settings(m2_test_database)
    application = create_app(settings=settings)
    client = TestClient(application)
    routes = list(iter_api_routes(application))
    route_paths = {route.path_format for route in routes}
    allowed_auth_telegram_paths = {
        "/auth/telegram",
        "/auth/telegram/attempts/{attempt_id}/status",
        "/auth/telegram/status",
        "/auth/telegram/link-token",
        "/auth/telegram/relink-token",
        "/auth/telegram/unlink",
    }
    allowed_auth_otp_paths = {
        "/auth/otp",
        "/auth/otp/new-code",
        "/auth/otp/request",
        "/auth/otp/verify",
    }
    allowed_customer_activation_paths = {
        "/customer/activation",
        "/customer/activation/otp/request",
        "/customer/activation/otp/verify",
        "/customer/activation/otp/new-code",
    }
    allowed_otp_paths = allowed_auth_otp_paths | (
        allowed_customer_activation_paths - {"/customer/activation"}
    )

    assert allowed_auth_telegram_paths.issubset(route_paths)
    assert allowed_auth_otp_paths.issubset(route_paths)
    assert allowed_customer_activation_paths.issubset(route_paths)
    assert not any(
        path.startswith("/auth/telegram") and path not in allowed_auth_telegram_paths
        for path in route_paths
    )
    assert not any(
        "otp" in path.casefold() and path not in allowed_otp_paths
        for path in route_paths
    )
    assert not any("webhook" in path.casefold() for path in route_paths)
    assert not any("callback" in path.casefold() for path in route_paths)
    assert not any("qr" in path.casefold() for path in route_paths)
    assert not any(
        "activation" in path.casefold()
        and path not in allowed_customer_activation_paths
        for path in route_paths
    )
    assert not any("reauth" in path.casefold() for path in route_paths)

    for path in (
        "/auth/telegram/link",
        "/auth/telegram/callback",
        "/telegram/webhook",
        "/telegram/callback",
        "/webhook/telegram",
        "/auth/telegram/qr",
    ):
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 404

    unprotected_unsafe_routes = [
        f"{','.join(sorted((route.methods or set()) & UNSAFE_METHODS))} "
        f"{route.path_format}"
        for route in routes
        if (route.methods or set()) & UNSAFE_METHODS
        and not route_has_csrf_dependency(route)
    ]
    assert unprotected_unsafe_routes == ["POST /customer/identity/document"]
    document_router_source = (
        PROJECT_ROOT / "app" / "customer_identity" / "router.py"
    ).read_text(encoding="utf-8")
    assert "bounded_multipart_upload(" in document_router_source
    assert "session_context=security_context.csrf_context" in document_router_source


def test_m6_runtime_keeps_unapproved_bot_sdk_otp_and_scheduler_out() -> None:
    dependency_names = production_dependency_names()
    source_text = app_source_text(
        excluded_prefixes=("app/otp/", "app/customer_activation/")
    ).casefold()
    settings_fields = set(Settings.model_fields)
    main_env_keys = app_main.SETTINGS_ENV_KEYS
    env_example_text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    compose_text = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert dependency_names.isdisjoint(FORBIDDEN_PRODUCTION_DEPENDENCIES)
    assert "httpx" in dependency_names
    assert "segno" in dependency_names
    assert "telegram_bot_token" in settings_fields
    assert "telegram_bot_token" not in main_env_keys
    assert {"client_ip_mode", "trusted_proxy_cidrs"}.issubset(settings_fields)
    assert "TELEGRAM_BOT_TOKEN=" in env_example_text
    assert "TELEGRAM_BOT_TOKEN:" in compose_text
    web_compose = compose_text.split("  web:", 1)[1].split("  telegram-worker:", 1)[0]
    assert "TELEGRAM_BOT_TOKEN" not in web_compose
    assert "CLIENT_IP_MODE" not in env_example_text
    assert "TRUSTED_PROXY_CIDRS" not in env_example_text
    assert "invalidate_otp_challenges_for_link_change" in source_text

    for marker in FORBIDDEN_APP_RUNTIME_MARKERS:
        assert marker not in source_text


def test_m7_otp_package_keeps_dispatcher_narrow_without_routes_or_generic_queue() -> (
    None
):
    otp_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "app" / "otp").glob("*.py"))
    ).casefold()

    assert "otp_challenges" in otp_source
    assert "otp_dispatches" in otp_source
    assert "otp_challenge_events" in otp_source
    assert "otp_dispatcher_state" in otp_source
    for marker in (
        "fastapi",
        "apirouter",
        "httpx",
        "telegram_bot_token",
        "smsotpprovider",
        "providerregistry",
        "redis",
        "celery",
        "dramatiq",
        "scheduler",
        "outbox",
        "webhook",
        "create_all",
    ):
        assert marker not in otp_source


def test_otp_webhook_and_customer_activation_do_not_appear_in_ui() -> None:
    pre_activation_templates = template_text(
        "auth/login.html",
        "auth/sessions.html",
        "customer/onboarding.html",
    ).casefold()
    customer_profile = template_text("customer/profile.html").casefold()
    account_telegram_templates = template_text(
        "auth/account.html",
        "auth/telegram.html",
    ).casefold()
    static_paths = [
        path.relative_to(PROJECT_ROOT).as_posix().casefold()
        for path in sorted((PROJECT_ROOT / "app" / "static").rglob("*"))
        if path.is_file()
    ]

    for marker in FORBIDDEN_CUSTOMER_FEATURE_TEXT:
        assert marker not in pre_activation_templates

    assert 'href="/customer/activation"' in customer_profile
    for marker in ("webhook", "qr", "bot", "public registration"):
        assert marker not in customer_profile

    assert "auth/telegram" in account_telegram_templates
    assert "telegram" in account_telegram_templates
    for marker in (
        "otp",
        "webhook",
        "activation",
        "activated",
    ):
        assert marker not in account_telegram_templates
    assert not any("qr" in path for path in static_paths)


@pytest.mark.integration
def test_customer_pages_render_only_the_m11_activation_discovery_claim(
    db_session: Session,
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 25, 17, 10, tzinfo=UTC)
    settings = make_settings(m2_test_database)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    client = TestClient(application)
    user = User(phone="+998900012171")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Customer(
            user_id=user.id,
            onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
        )
    )
    created_session = create_authenticated_session(
        db_session,
        user.id,
        "pytest-telegram-scope-regression",
        now,
        settings=settings,
    )
    db_session.commit()
    set_client_session_cookie(client, settings, created_session.raw_token)

    onboarding_response = client.get("/customer/onboarding")
    profile_response = client.get("/customer/profile")

    assert onboarding_response.status_code == 200
    assert profile_response.status_code == 200
    onboarding_text = onboarding_response.text.casefold()
    profile_text = profile_response.text.casefold()
    for marker in FORBIDDEN_CUSTOMER_FEATURE_TEXT:
        assert marker not in onboarding_text
    assert 'href="/customer/activation"' in profile_text
    assert "faollashtirishga tayyorgarlik" in profile_text
    for marker in ("telegram", "otp", "qr", "bot", "webhook"):
        assert marker not in profile_text


def test_customer_schema_has_only_m11_extension_and_telegram_stays_scoped() -> None:
    telegram_tables = {
        table_name
        for table_name in Base.metadata.tables
        if table_name.startswith("telegram_")
    }
    customer_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Customer.__table__.constraints
        if hasattr(constraint, "sqltext")
    }

    assert telegram_tables == {
        "telegram_links",
        "telegram_link_tokens",
        "telegram_link_events",
        "telegram_polling_state",
        "telegram_update_failures",
    }
    assert Base.metadata.tables["telegram_links"] is TelegramLink.__table__
    assert Base.metadata.tables["telegram_link_tokens"] is TelegramLinkToken.__table__
    assert Base.metadata.tables["telegram_link_events"] is TelegramLinkEvent.__table__
    assert (
        Base.metadata.tables["telegram_polling_state"] is TelegramPollingState.__table__
    )
    assert (
        Base.metadata.tables["telegram_update_failures"]
        is TelegramUpdateFailure.__table__
    )
    assert set(Customer.__table__.columns.keys()) == {
        "id",
        "user_id",
        "onboarding_status",
        "activated_at",
        "created_at",
        "updated_at",
    }
    assert customer_constraints["ck_customers_onboarding_status_allowed"] == (
        "onboarding_status IN ('draft', 'active')"
    )
    assert customer_constraints["ck_customers_activation_state_consistent"] == (
        "(onboarding_status = 'draft' AND activated_at IS NULL) OR "
        "(onboarding_status = 'active' AND activated_at IS NOT NULL)"
    )


def test_readme_preserves_m4_baseline_and_documents_m6_integration() -> None:
    readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").casefold()
    m4_section = readme_text.split(
        "## secure telegram linking domain foundation (m4)",
        1,
    )[1].split("## production telegram account linking (m6)", 1)[0]

    assert "end-to-end telegram integratsiya emas" in m4_section
    for explicit_absence in (
        "real bot api",
        "production route/ui",
        "webhook",
        "worker",
        "qr",
        "otp",
        "customer activation",
        "hali yo'q",
        "telegram_bot_token",
        "mavjud emas",
        "talab qilinmaydi",
        "real telegram credential yoki network talab qilinmaydi",
    ):
        assert explicit_absence in m4_section

    for unsupported_route_or_claim in (
        "/auth/telegram",
        "telegram webhook url",
        "telegram bot token required",
        "telegram bot token is required",
        "telegram sdk required",
        "qr code available",
        "otp enabled",
        "customer activation enabled",
        "notification worker",
    ):
        assert unsupported_route_or_claim not in m4_section

    assert "/auth/telegram" in readme_text
    assert "python -m app.telegram.worker run" in readme_text
    assert "public registration" in readme_text
    assert "webhook scope'ga kirmaydi" in readme_text
