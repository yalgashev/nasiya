import re
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from html import unescape
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.auth.service import create_user
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-customer-web-idor"
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


def make_client(engine: Engine, now: datetime) -> TestClient:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application)


def commit_user(db_session: Session, phone: str) -> User:
    result = create_user(db_session, phone, TEST_PASSWORD)
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def login_client(client: TestClient, phone: str) -> None:
    login_page = client.get("/auth/login")
    assert login_page.status_code == 200
    csrf_token = extract_hidden_csrf_token(login_page.text)

    response = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_token,
            "phone": phone,
            "password": TEST_PASSWORD,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/account"


def extract_hidden_csrf_token(html: str) -> str:
    match = re.search(
        r'name="csrf_token"\s+value="(?P<token>[^"]+)"',
        html,
    )
    assert match is not None
    return match.group("token")


def get_customer_by_user_id(db_session: Session, user_id: UUID) -> Customer | None:
    return db_session.scalar(select(Customer).where(Customer.user_id == user_id))


def count_customers(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(Customer)) or 0


def assert_not_exposed(html: str, *values: object) -> None:
    visible_html = unescape(html)
    for value in values:
        assert str(value) not in visible_html


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


def test_customer_web_flow_is_own_only_for_two_authenticated_users(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client_a = make_client(m2_test_database, now)
    client_b = make_client(m2_test_database, now)
    user_a = commit_user(db_session, "+998901234590")
    user_b = commit_user(db_session, "+998901234591")
    masked_a = mask_phone_for_display(user_a.phone)
    masked_b = mask_phone_for_display(user_b.phone)

    login_client(client_a, user_a.phone)
    login_client(client_b, user_b.phone)

    a_onboarding = client_a.get("/customer/onboarding")
    a_csrf = extract_hidden_csrf_token(a_onboarding.text)
    a_start = client_a.post(
        "/customer/onboarding/start",
        data={
            "csrf_token": a_csrf,
            "user_id": str(user_b.id),
            "customer_id": str(uuid4()),
        },
        follow_redirects=False,
    )

    assert a_start.status_code == 303
    assert a_start.headers["location"] == "/customer/profile"
    a_customer = get_customer_by_user_id(db_session, user_a.id)
    assert a_customer is not None
    assert get_customer_by_user_id(db_session, user_b.id) is None
    assert count_customers(db_session) == 1
    assert_not_exposed(a_start.text, user_a.id, user_b.id, a_customer.id)

    b_profile_before_start = client_b.get("/customer/profile", follow_redirects=False)

    assert b_profile_before_start.status_code == 303
    assert b_profile_before_start.headers["location"] == "/customer/onboarding"
    assert_not_exposed(
        b_profile_before_start.text,
        user_a.phone,
        masked_a,
        user_a.id,
        user_b.id,
        a_customer.id,
    )

    b_onboarding = client_b.get("/customer/onboarding")
    b_csrf = extract_hidden_csrf_token(b_onboarding.text)
    assert masked_a not in unescape(b_onboarding.text)
    assert_not_exposed(
        b_onboarding.text,
        user_a.phone,
        user_b.phone,
        user_a.id,
        user_b.id,
        a_customer.id,
    )

    b_start = client_b.post(
        "/customer/onboarding/start",
        data={
            "csrf_token": b_csrf,
            "user_id": str(user_a.id),
            "customer_id": str(a_customer.id),
        },
        follow_redirects=False,
    )

    assert b_start.status_code == 303
    assert b_start.headers["location"] == "/customer/profile"
    b_customer = get_customer_by_user_id(db_session, user_b.id)
    assert b_customer is not None
    assert b_customer.id != a_customer.id
    assert count_customers(db_session) == 2
    assert_not_exposed(b_start.text, user_a.id, user_b.id, a_customer.id, b_customer.id)

    b_profile_after_start = client_b.get("/customer/profile")
    a_profile_after_b_start = client_a.get("/customer/profile")

    assert b_profile_after_start.status_code == 200
    assert masked_b in unescape(b_profile_after_start.text)
    assert masked_a not in unescape(b_profile_after_start.text)
    assert_not_exposed(
        b_profile_after_start.text,
        user_a.phone,
        user_b.phone,
        user_a.id,
        user_b.id,
        a_customer.id,
        b_customer.id,
    )

    assert a_profile_after_b_start.status_code == 200
    assert masked_a in unescape(a_profile_after_b_start.text)
    assert masked_b not in unescape(a_profile_after_b_start.text)
    assert_not_exposed(
        a_profile_after_b_start.text,
        user_a.phone,
        user_b.phone,
        user_a.id,
        user_b.id,
        a_customer.id,
        b_customer.id,
    )


def test_customer_id_urls_are_not_routable() -> None:
    application = create_app()
    customer_routes = [
        route
        for route in iter_api_routes(application)
        if route.path_format.startswith("/customer")
    ]

    assert all("{" not in route.path_format for route in customer_routes)
    assert all(route.dependant.path_params == [] for route in customer_routes)

    client = TestClient(application)
    response = client.get(f"/customer/{uuid4()}", follow_redirects=False)

    assert response.status_code == 404
