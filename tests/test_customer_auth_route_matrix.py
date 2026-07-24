from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import (
    CreatedSession,
    create_anonymous_session,
    create_authenticated_session,
    revoke_session,
)
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-customer-auth-matrix"
CUSTOMER_GET_PATHS = ("/customer/onboarding", "/customer/profile")


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


def commit_user(db_session: Session, phone: str = "+998901234580") -> User:
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
        "pytest-customer-auth-matrix",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def commit_anonymous_session(
    db_session: Session,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_anonymous_session(
        db_session,
        "pytest-customer-auth-matrix",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def count_customers(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(Customer)) or 0


def assert_customer_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_delete_cookie(response, settings: Settings) -> None:
    set_cookie = response.headers["set-cookie"]

    assert set_cookie.startswith(f"{settings.session_cookie_name}=")
    assert "Max-Age=0" in set_cookie
    assert "Path=/" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


@pytest.mark.parametrize("path", CUSTOMER_GET_PATHS)
def test_anonymous_get_customer_pages_redirect_to_login(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_customer_security_headers(response)
    assert "set-cookie" not in response.headers
    assert count_customers(db_session) == 0


def test_anonymous_post_start_without_session_is_csrf_rejected(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.post(
        "/customer/onboarding/start",
        data={},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 0


def test_anonymous_post_start_with_valid_anonymous_csrf_redirects_login(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": get_csrf_token(created.session).as_form_value()},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_customer_security_headers(response)
    assert "set-cookie" not in response.headers
    assert count_customers(db_session) == 0


@pytest.mark.parametrize("path", CUSTOMER_GET_PATHS)
def test_revoked_session_cannot_access_customer_pages(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234581")
    created = commit_authenticated_session(db_session, user, now, settings)
    revoke_session(db_session, created.session, now)
    db_session.commit()
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_delete_cookie(response, settings)
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 0


@pytest.mark.parametrize("path", CUSTOMER_GET_PATHS)
def test_expired_session_cannot_access_customer_pages(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    created_at = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    now = created_at + timedelta(minutes=31)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234582")
    created = commit_authenticated_session(db_session, user, created_at, settings)
    created.session.expires_at = now - timedelta(seconds=1)
    db_session.commit()
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.SESSION_EXPIRED.value
    assert_delete_cookie(response, settings)
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 0


def test_authenticated_no_draft_get_onboarding_renders_start_form(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234583")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )

    response = client.get("/customer/onboarding", follow_redirects=False)

    assert response.status_code == 200
    assert_customer_security_headers(response)
    assert '<form method="post" action="/customer/onboarding/start">' in response.text
    assert 'name="csrf_token"' in response.text
    assert count_customers(db_session) == 0


def test_authenticated_no_draft_get_profile_redirects_onboarding(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234584")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )

    response = client.get("/customer/profile", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/customer/onboarding"
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 0


@pytest.mark.parametrize("path", CUSTOMER_GET_PATHS)
def test_invalid_cookie_is_cleared_for_customer_get_pages(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    raw_cookie = "unknown-session-token"
    set_client_session_cookie(client, settings, raw_cookie)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_delete_cookie(response, settings)
    assert_customer_security_headers(response)
    assert raw_cookie not in response.text
    assert count_customers(db_session) == 0
