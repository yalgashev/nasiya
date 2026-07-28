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
from app.db import Base, create_database_session_factory
from app.main import create_app
from app.settings import Settings
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken

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
    "polling_state",
    "telegram.polling",
    "telegram.bot(",
    "aiogram",
    "python-telegram-bot",
    "qrcode",
    "qr_code",
    "apscheduler",
    "scheduler",
    "cron",
    "redis",
    "otpdeliveryprovider",
    "otp_challenge",
    "otp_code",
    "x-forwarded-for",
    "proxy_middleware",
    "current_password",
    "reauth",
    "re-auth",
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
        dependency_call is validate_csrf
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


def app_source_text() -> str:
    source_parts = []
    for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
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
        "/auth/telegram/status",
        "/auth/telegram/link-token",
        "/auth/telegram/relink-token",
        "/auth/telegram/unlink",
    }

    assert allowed_auth_telegram_paths.issubset(route_paths)
    assert not any(
        path.startswith("/auth/telegram") and path not in allowed_auth_telegram_paths
        for path in route_paths
    )
    assert not any("webhook" in path.casefold() for path in route_paths)
    assert not any("callback" in path.casefold() for path in route_paths)
    assert not any("qr" in path.casefold() for path in route_paths)
    assert not any("otp" in path.casefold() for path in route_paths)
    assert not any("activation" in path.casefold() for path in route_paths)
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
    assert unprotected_unsafe_routes == []


def test_m6_transport_keeps_unapproved_worker_qr_otp_and_scheduler_out() -> None:
    dependency_names = production_dependency_names()
    source_text = app_source_text().casefold()
    settings_fields = set(Settings.model_fields)
    main_env_keys = app_main.SETTINGS_ENV_KEYS
    env_example_text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    compose_text = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert dependency_names.isdisjoint(FORBIDDEN_PRODUCTION_DEPENDENCIES)
    assert "httpx" in dependency_names
    assert "telegram_bot_token" in settings_fields
    assert "telegram_bot_token" not in main_env_keys
    assert {"client_ip_mode", "trusted_proxy_cidrs"}.issubset(settings_fields)
    assert "TELEGRAM_BOT_TOKEN" not in env_example_text
    assert "TELEGRAM_BOT_TOKEN" not in compose_text
    assert "CLIENT_IP_MODE" not in env_example_text
    assert "TRUSTED_PROXY_CIDRS" not in env_example_text

    for marker in FORBIDDEN_APP_RUNTIME_MARKERS:
        assert marker not in source_text


def test_qr_otp_password_reauth_and_customer_activation_do_not_appear_in_ui() -> None:
    non_telegram_templates = template_text(
        "auth/login.html",
        "auth/sessions.html",
        "customer/onboarding.html",
        "customer/profile.html",
    ).casefold()
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
        assert marker not in non_telegram_templates

    assert "auth/telegram" in account_telegram_templates
    assert "telegram" in account_telegram_templates
    for marker in (
        "telegram",
        "otp",
        "qrcode",
        "qr_code",
        "webhook",
        "current_password",
        "reauth",
        "re-auth",
        "activation",
        "activated",
    ):
        if marker == "telegram":
            continue
        assert marker not in account_telegram_templates
    assert not any("qr" in path or "telegram" in path for path in static_paths)


@pytest.mark.integration
def test_m3_customer_pages_render_no_telegram_otp_qr_or_activation_claims(
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
    rendered_text = f"{onboarding_response.text} {profile_response.text}".casefold()
    for marker in FORBIDDEN_CUSTOMER_FEATURE_TEXT:
        assert marker not in rendered_text


def test_customer_schema_remains_draft_only_and_m4_has_exactly_three_tables() -> None:
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
    }
    assert Base.metadata.tables["telegram_links"] is TelegramLink.__table__
    assert Base.metadata.tables["telegram_link_tokens"] is TelegramLinkToken.__table__
    assert Base.metadata.tables["telegram_link_events"] is TelegramLinkEvent.__table__
    assert set(Customer.__table__.columns.keys()) == {
        "id",
        "user_id",
        "onboarding_status",
        "created_at",
        "updated_at",
    }
    assert customer_constraints["ck_customers_onboarding_status_draft_only"] == (
        "onboarding_status = 'draft'"
    )


def test_readme_documents_m4_as_domain_foundation_only() -> None:
    readme_text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").casefold()

    assert "secure telegram linking domain foundation (m4)" in readme_text
    assert "end-to-end telegram integratsiya emas" in readme_text
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
        assert explicit_absence in readme_text

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
        assert unsupported_route_or_claim not in readme_text
