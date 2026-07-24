import re
from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time, validate_csrf
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-customer-onboarding-get"
FORBIDDEN_ONBOARDING_SCOPE_TEXT = (
    "active customer",
    "ro'yxatdan o'tish yakunlandi",
    "ro‘yxatdan o‘tish yakunlandi",
    "f.i.sh",
    "jshshir",
    "passport",
    "pasport",
    "document",
    "hujjat",
    "telegram",
    "otp",
    "offer",
    "taklif",
    "shop",
    "do'kon",
    "progress",
    "customer_id",
    "user_id",
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


def make_client(engine: Engine, now: datetime) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application), settings


def set_client_session_cookie(
    client: TestClient,
    settings: Settings,
    raw_cookie: str,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        raw_cookie,
        domain="testserver.local",
        path="/",
    )


def commit_user(db_session: Session, phone: str = "+998901234567") -> User:
    result = create_user(db_session, phone, "Password123")
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-customer-onboarding",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def count_customers(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(Customer)) or 0


def add_customer(db_session: Session, user: User) -> Customer:
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )
    db_session.add(customer)
    db_session.commit()
    return customer


def iter_api_routes(routes: list[object]):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue

        included_router = getattr(route, "original_router", None)
        if included_router is not None:
            yield from iter_api_routes(included_router.routes)

        nested_routes = getattr(route, "routes", None)
        if nested_routes:
            yield from iter_api_routes(nested_routes)


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def assert_customer_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_forbidden_onboarding_scope_absent(html: str) -> None:
    normalized_html = unescape(html).casefold()
    for forbidden_text in FORBIDDEN_ONBOARDING_SCOPE_TEXT:
        assert forbidden_text.casefold() not in normalized_html


def assert_no_inline_script_or_style(html: str) -> None:
    normalized_html = html.casefold()
    assert "<script" not in normalized_html
    assert "<style" not in normalized_html
    assert " style=" not in normalized_html
    assert " onclick=" not in normalized_html
    assert " onsubmit=" not in normalized_html


def test_get_customer_onboarding_not_started_renders_start_view_without_side_effect(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    raw_cookie = created.raw_token.as_cookie_value()
    set_client_session_cookie(client, settings, raw_cookie)

    response = client.get("/customer/onboarding")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_customer_security_headers(response)
    assert extract_hidden_csrf_token(response.text) == (
        get_csrf_token(created.session).as_form_value()
    )
    assert '<form method="post" action="/customer/onboarding/start">' in response.text
    assert 'name="user_id"' not in response.text
    assert 'name="customer_id"' not in response.text
    assert count_customers(db_session) == 0

    visible_html = unescape(response.text)
    assert "Customer onboarding qoralamasi" in visible_html
    assert "Draft hali boshlanmagan" in visible_html
    assert "Bu sahifa faqat onboarding qoralamasini boshlash uchun." in visible_html
    assert 'href="/auth/account"' in response.text
    assert "<button" in response.text
    assert 'type="submit"' in response.text
    assert "min-height: 44px" in (
        Path("app/static/css/app.css").read_text(encoding="utf-8")
    )
    assert user.phone not in visible_html
    assert str(user.id) not in visible_html
    assert raw_cookie not in visible_html
    assert created.session.token_hash not in visible_html
    assert_forbidden_onboarding_scope_absent(response.text)
    assert_no_inline_script_or_style(response.text)


def test_get_customer_onboarding_anonymous_redirects_to_login(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/customer/onboarding", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_customer_security_headers(response)


def test_get_customer_onboarding_existing_draft_renders_safe_state(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234568")
    other_user = commit_user(db_session, "+998901234569")
    customer = add_customer(db_session, user)
    other_customer = add_customer(db_session, other_user)
    created = commit_authenticated_session(db_session, user, now, settings)
    raw_cookie = created.raw_token.as_cookie_value()
    set_client_session_cookie(client, settings, raw_cookie)

    response = client.get("/customer/onboarding")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 2

    visible_html = unescape(response.text)
    assert "Draft mavjud" in visible_html
    assert "Status: Qoralama" in visible_html
    assert 'href="/customer/profile"' in response.text
    assert (
        '<form method="post" action="/customer/onboarding/start">'
        not in response.text
    )
    assert 'name="csrf_token"' not in response.text
    assert 'type="submit"' not in response.text
    assert 'href="/auth/account"' not in response.text
    assert "Draft hali boshlanmagan" not in visible_html
    assert user.phone not in visible_html
    assert other_user.phone not in visible_html
    assert str(user.id) not in visible_html
    assert str(other_user.id) not in visible_html
    assert str(customer.id) not in visible_html
    assert str(other_customer.id) not in visible_html
    assert raw_cookie not in visible_html
    assert created.session.token_hash not in visible_html
    assert_forbidden_onboarding_scope_absent(response.text)
    assert_no_inline_script_or_style(response.text)


def test_customer_onboarding_route_inventory_is_get_only_without_customer_id() -> None:
    application = create_app()
    customer_onboarding_routes = [
        route
        for route in iter_api_routes(application.routes)
        if route.path_format == "/customer/onboarding"
    ]

    assert len(customer_onboarding_routes) == 1
    route = customer_onboarding_routes[0]
    assert route.methods == {"GET"}
    assert "{" not in route.path_format
    assert route.dependant.path_params == []
    assert [
        query_param.name
        for query_param in route.dependant.query_params
        if "customer" in query_param.name
    ] == []


def test_post_customer_onboarding_start_creates_draft_and_redirects(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234570")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )
    csrf_token = get_csrf_token(created.session).as_form_value()

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customer/profile"
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 1

    customer = db_session.scalar(select(Customer).where(Customer.user_id == user.id))
    assert customer is not None
    assert customer.user_id == user.id
    assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
    assert str(customer.id) not in response.text


def test_post_customer_onboarding_start_is_repeated_post_safe_prg_get_refresh(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234571")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )
    csrf_token = get_csrf_token(created.session).as_form_value()

    first_response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    first_customer = db_session.scalar(
        select(Customer).where(Customer.user_id == user.id)
    )
    assert first_customer is not None
    redirect_get_response = client.get(
        first_response.headers["location"],
        follow_redirects=False,
    )
    second_response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    second_customer = db_session.scalar(
        select(Customer).where(Customer.user_id == user.id)
    )

    assert first_response.status_code == 303
    assert second_response.status_code == 303
    assert first_response.headers["location"] == "/customer/profile"
    assert second_response.headers["location"] == "/customer/profile"
    assert redirect_get_response.request.method == "GET"
    assert count_customers(db_session) == 1
    assert second_customer is not None
    assert second_customer.id == first_customer.id
    assert str(first_customer.id) not in first_response.text
    assert str(first_customer.id) not in second_response.text


@pytest.mark.parametrize("data", [{}, {"csrf_token": "wrong-csrf-token"}])
def test_post_customer_onboarding_start_requires_csrf(
    m2_test_database: Engine,
    db_session: Session,
    data: dict[str, str],
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234572")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )

    response = client.post(
        "/customer/onboarding/start",
        data=data,
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 0
    assert "wrong-csrf-token" not in response.text
    assert created.session.token_hash not in response.text
    assert created.session.csrf_secret not in response.text


def test_post_customer_onboarding_start_rejects_cross_session_csrf(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234573")
    current_session = commit_authenticated_session(db_session, user, now, settings)
    other_session = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(
        client,
        settings,
        current_session.raw_token.as_cookie_value(),
    )

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": get_csrf_token(other_session.session).as_form_value()},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 0
    assert current_session.session.token_hash not in response.text
    assert current_session.session.csrf_secret not in response.text
    assert other_session.session.token_hash not in response.text
    assert other_session.session.csrf_secret not in response.text


def test_customer_onboarding_start_route_contract() -> None:
    application = create_app()
    customer_start_routes = [
        route
        for route in iter_api_routes(application.routes)
        if route.path_format == "/customer/onboarding/start"
    ]

    assert len(customer_start_routes) == 1
    route = customer_start_routes[0]
    assert route.methods == {"POST"}
    assert "{" not in route.path_format
    assert route.dependant.path_params == []
    assert [
        query_param.name
        for query_param in route.dependant.query_params
        if "customer" in query_param.name or "user" in query_param.name
    ] == []
    assert [
        body_param.name
        for body_param in route.dependant.body_params
        if "customer" in body_param.name or "user" in body_param.name
    ] == []
    assert any(
        dependency.call is validate_csrf
        for dependency in route.dependant.dependencies
    )


def test_customer_router_does_not_own_transactions_or_query_orm_directly() -> None:
    router_source = Path("app/customer/router.py").read_text(encoding="utf-8")

    assert ".commit(" not in router_source
    assert ".rollback(" not in router_source
    assert "select(" not in router_source
    assert "session.execute" not in router_source
    assert "Customer(" not in router_source
