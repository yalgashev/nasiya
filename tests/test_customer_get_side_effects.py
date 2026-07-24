from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-customer-get-effects"
GET_SIDE_EFFECT_PATHS = (
    "/customer/onboarding",
    "/customer/profile",
    "/auth/account",
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
    created: CreatedSession,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )


def commit_user(db_session: Session, phone: str = "+998901234601") -> User:
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
        "pytest-customer-get-effects",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def count_customers(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(Customer)) or 0


def get_customer_for_user(db_session: Session, user: User) -> Customer | None:
    return db_session.scalar(select(Customer).where(Customer.user_id == user.id))


def assert_head_options_framework_behavior(
    client: TestClient,
    path: str,
) -> None:
    head_response = client.head(path, follow_redirects=False)
    options_response = client.options(path, follow_redirects=False)

    assert head_response.status_code == 405
    assert head_response.headers["allow"] == "GET"
    assert options_response.status_code == 405
    assert options_response.headers["allow"] == "GET"


def test_no_draft_customer_gets_redirects_and_discovery_do_not_create_rows(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)
    csrf_token = get_csrf_token(created.session).as_form_value()

    assert count_customers(db_session) == 0

    for _ in range(3):
        onboarding_response = client.get(
            "/customer/onboarding",
            follow_redirects=False,
        )
        profile_response = client.get("/customer/profile", follow_redirects=False)

        assert onboarding_response.status_code == 200
        assert '<form method="post" action="/customer/onboarding/start">' in (
            onboarding_response.text
        )
        assert profile_response.status_code == 303
        assert profile_response.headers["location"] == "/customer/onboarding"
        assert count_customers(db_session) == 0

    account_response = client.get("/auth/account", follow_redirects=False)

    assert account_response.status_code == 200
    assert 'href="/customer/onboarding"' in account_response.text
    assert "Customer draft onboarding" in unescape(account_response.text)
    assert count_customers(db_session) == 0

    for path in GET_SIDE_EFFECT_PATHS:
        assert_head_options_framework_behavior(client, path)
        assert count_customers(db_session) == 0

    post_response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert post_response.status_code == 303
    assert post_response.headers["location"] == "/customer/profile"
    assert count_customers(db_session) == 1

    customer = get_customer_for_user(db_session, user)
    assert customer is not None
    assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT


def test_customer_gets_do_not_touch_existing_draft_timestamps(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234602")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    post_response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": get_csrf_token(created.session).as_form_value()},
        follow_redirects=False,
    )
    assert post_response.status_code == 303

    customer = get_customer_for_user(db_session, user)
    assert customer is not None
    original_created_at = customer.created_at
    original_updated_at = customer.updated_at

    for _ in range(3):
        onboarding_response = client.get(
            "/customer/onboarding",
            follow_redirects=False,
        )
        profile_response = client.get("/customer/profile", follow_redirects=False)
        account_response = client.get("/auth/account", follow_redirects=False)

        assert onboarding_response.status_code == 200
        assert profile_response.status_code == 200
        assert account_response.status_code == 200
        assert count_customers(db_session) == 1

    for path in GET_SIDE_EFFECT_PATHS:
        assert_head_options_framework_behavior(client, path)

    db_session.refresh(customer)

    assert count_customers(db_session) == 1
    assert customer.created_at == original_created_at
    assert customer.updated_at == original_updated_at
    assert customer.onboarding_status == CUSTOMER_ONBOARDING_STATUS_DRAFT
