import re
from collections.abc import Generator
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import escape
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer.router as customer_router
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer.view_model import CustomerDraftView
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEMPLATES_DIR = Path("app/templates")
CUSTOMER_TEMPLATE_DIR = TEMPLATES_DIR / "customer"
CUSTOMER_TEMPLATE_PATHS = tuple(sorted(CUSTOMER_TEMPLATE_DIR.glob("*.html")))
SAFE_VIEW_FIELDS = frozenset({"masked_phone", "onboarding_status_display"})
ALLOWED_CUSTOMER_TEMPLATE_EXPRESSIONS = frozenset(
    {
        "csrf_token",
        "customer_state.masked_phone",
        "customer_state.onboarding_status_display",
    }
)
FORBIDDEN_TEMPLATE_SNIPPETS = (
    "|safe",
    "|tojson",
    "__dict__",
    "__class__",
    "{% debug",
    "pprint",
    "context_dump",
    "debug_context",
    "{{ customer_state }}",
    "{{ customer }}",
    "{{ user }}",
)
FORBIDDEN_CONTEXT_KEYS = {
    "customer",
    "customer_id",
    "current_user",
    "db",
    "raw_phone",
    "request",
    "session",
    "user",
    "user_id",
}
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-customer-view-template"
TEST_PASSWORD = "Password123"


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


def make_client(engine: Engine, now: datetime) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application), settings


def commit_user(db_session: Session, phone: str) -> User:
    result = create_user(db_session, phone, TEST_PASSWORD)
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_customer(db_session: Session, user: User) -> Customer:
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )
    db_session.add(customer)
    db_session.commit()
    return customer


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-customer-view-template-safety",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def set_client_session_cookie(
    client: TestClient,
    settings: Settings,
    created: CreatedSession,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )


def static_url_for(name: str, **params: str) -> str:
    assert name == "static"
    return f"/static/{params['path']}"


def render_customer_template(template_name: str, **context: object) -> str:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(
            enabled_extensions=("html", "xml"),
            default=True,
        ),
    )
    environment.globals["url_for"] = static_url_for
    return environment.get_template(template_name).render(**context)


def iter_jinja_print_expressions(template_source: str) -> list[str]:
    return [
        expression.strip()
        for expression in re.findall(r"{{\s*(.*?)\s*}}", template_source)
    ]


def assert_no_raw_orm_or_internal_context(context: dict[str, object]) -> None:
    assert FORBIDDEN_CONTEXT_KEYS.isdisjoint(context)

    for value in context.values():
        assert not isinstance(value, Customer)
        assert not isinstance(value, User)
        if isinstance(value, CustomerDraftView):
            assert {field.name for field in fields(value)} == SAFE_VIEW_FIELDS


def test_customer_draft_view_model_has_explicit_safe_allowlist() -> None:
    customer_id = uuid4()
    user_id = uuid4()
    customer_state = CustomerDraftView(
        masked_phone="*** ** *** 99 99",
        onboarding_status_display="draft",
    )

    assert {field.name for field in fields(CustomerDraftView)} == SAFE_VIEW_FIELDS
    assert set(asdict(customer_state)) == SAFE_VIEW_FIELDS
    assert not hasattr(customer_state, "__dict__")
    assert not hasattr(customer_state, "id")
    assert not hasattr(customer_state, "user_id")
    assert not hasattr(customer_state, "raw_phone")
    assert str(customer_id) not in repr(customer_state)
    assert str(user_id) not in repr(customer_state)


def test_customer_profile_template_autoescapes_status_display_payload() -> None:
    status_payload = '<img src=x onerror=alert(1)>"&'
    phone_payload = '<svg onload=alert(1)>+"&'
    customer_state = CustomerDraftView(
        masked_phone=phone_payload,
        onboarding_status_display=status_payload,
    )

    rendered = render_customer_template(
        "customer/profile.html",
        customer_state=customer_state,
    )

    assert status_payload not in rendered
    assert phone_payload not in rendered
    assert "<img" not in rendered.casefold()
    assert "<svg" not in rendered.casefold()
    assert str(escape(status_payload)) in rendered
    assert str(escape(phone_payload)) in rendered


def test_customer_templates_use_only_safe_view_fields_and_no_context_dump() -> None:
    assert CUSTOMER_TEMPLATE_PATHS

    for template_path in CUSTOMER_TEMPLATE_PATHS:
        template_source = template_path.read_text(encoding="utf-8")

        for forbidden_snippet in FORBIDDEN_TEMPLATE_SNIPPETS:
            assert forbidden_snippet not in template_source

        assert set(iter_jinja_print_expressions(template_source)) <= (
            ALLOWED_CUSTOMER_TEMPLATE_EXPRESSIONS
        )


@pytest.mark.integration
def test_customer_routes_pass_only_safe_template_context(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 24, 10, 15, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998900000803")
    customer = commit_customer(db_session, user)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)
    captured_contexts: list[tuple[str, dict[str, object]]] = []

    def capture_template_response(
        _request: Request,
        template_name: str,
        context: dict[str, object],
        *_args: object,
        **_kwargs: object,
    ) -> HTMLResponse:
        captured_contexts.append((template_name, dict(context)))
        return HTMLResponse("<!doctype html><title>captured</title>")

    monkeypatch.setattr(
        customer_router.templates,
        "TemplateResponse",
        capture_template_response,
    )

    onboarding_response = client.get("/customer/onboarding")
    profile_response = client.get("/customer/profile")

    assert onboarding_response.status_code == 200
    assert profile_response.status_code == 200
    assert [name for name, _context in captured_contexts] == [
        "customer/onboarding.html",
        "customer/profile.html",
    ]

    onboarding_context = captured_contexts[0][1]
    profile_context = captured_contexts[1][1]

    assert set(onboarding_context) == {"customer_state", "csrf_token"}
    assert set(profile_context) == {"customer_state"}
    assert onboarding_context["csrf_token"] == (
        get_csrf_token(created.session).as_form_value()
    )

    for _template_name, context in captured_contexts:
        assert_no_raw_orm_or_internal_context(context)
        customer_state = context["customer_state"]
        assert isinstance(customer_state, CustomerDraftView)
        assert customer_state.masked_phone != user.phone
        assert customer_state.onboarding_status_display == "draft"
        assert str(user.id) not in repr(customer_state)
        assert str(customer.id) not in repr(customer_state)
