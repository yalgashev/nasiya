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
    rotate_session,
)
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-customer-csrf-matrix"


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


def commit_user(db_session: Session, phone: str = "+998901234592") -> User:
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
        "pytest-customer-csrf-matrix",
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
        "pytest-customer-csrf-matrix",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def csrf_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def count_customers(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(Customer)) or 0


def assert_customer_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_csrf_failed_without_customer(
    response,
    db_session: Session,
    *secrets: str,
) -> None:
    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 0
    for secret in secrets:
        if secret:
            assert secret not in response.text
    assert "Traceback" not in response.text


def test_customer_start_accepts_valid_same_session_form_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234592")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": csrf_value(created)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customer/profile"
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 1

    customer = db_session.scalar(select(Customer).where(Customer.user_id == user.id))
    assert customer is not None
    assert str(customer.id) not in response.text


@pytest.mark.parametrize(
    ("data", "submitted_token"),
    [
        ({}, ""),
        ({"csrf_token": "not-a-valid-csrf-token"}, "not-a-valid-csrf-token"),
    ],
)
def test_customer_start_rejects_missing_or_malformed_form_token(
    m2_test_database: Engine,
    db_session: Session,
    data: dict[str, str],
    submitted_token: str,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234593")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/customer/onboarding/start",
        data=data,
        follow_redirects=False,
    )

    assert_csrf_failed_without_customer(
        response,
        db_session,
        submitted_token,
        created.session.token_hash,
        created.session.csrf_secret,
    )


def test_customer_start_rejects_other_anonymous_session_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234594")
    current = commit_authenticated_session(db_session, user, now, settings)
    other_anonymous = commit_anonymous_session(db_session, now, settings)
    other_token = csrf_value(other_anonymous)
    set_client_session_cookie(client, settings, current)

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": other_token},
        follow_redirects=False,
    )

    assert_csrf_failed_without_customer(
        response,
        db_session,
        other_token,
        current.session.token_hash,
        current.session.csrf_secret,
        other_anonymous.session.token_hash,
        other_anonymous.session.csrf_secret,
    )


def test_customer_start_rejects_other_authenticated_session_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    current_user = commit_user(db_session, "+998901234595")
    other_user = commit_user(db_session, "+998901234596")
    current = commit_authenticated_session(db_session, current_user, now, settings)
    other_authenticated = commit_authenticated_session(
        db_session,
        other_user,
        now,
        settings,
    )
    other_token = csrf_value(other_authenticated)
    set_client_session_cookie(client, settings, current)

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": other_token},
        follow_redirects=False,
    )

    assert_csrf_failed_without_customer(
        response,
        db_session,
        other_token,
        current.session.token_hash,
        current.session.csrf_secret,
        other_authenticated.session.token_hash,
        other_authenticated.session.csrf_secret,
    )


def test_customer_start_rejects_rotated_old_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    rotated_at = created_at + timedelta(minutes=1)
    client, settings = make_client(m2_test_database, rotated_at)
    user = commit_user(db_session, "+998901234597")
    old_session = commit_authenticated_session(db_session, user, created_at, settings)
    old_token = csrf_value(old_session)
    rotated = rotate_session(
        db_session,
        old_session.session,
        user.id,
        "pytest-customer-csrf-matrix-rotated",
        rotated_at,
        settings=settings,
    )
    db_session.commit()
    set_client_session_cookie(client, settings, rotated)

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": old_token},
        follow_redirects=False,
    )

    assert_csrf_failed_without_customer(
        response,
        db_session,
        old_token,
        old_session.session.token_hash,
        old_session.session.csrf_secret,
        rotated.session.token_hash,
        rotated.session.csrf_secret,
    )


def test_customer_start_rejects_revoked_session_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now + timedelta(minutes=1))
    user = commit_user(db_session, "+998901234598")
    created = commit_authenticated_session(db_session, user, now, settings)
    token = csrf_value(created)
    revoke_session(db_session, created.session, now + timedelta(seconds=30))
    db_session.commit()
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert_csrf_failed_without_customer(
        response,
        db_session,
        token,
        created.session.token_hash,
        created.session.csrf_secret,
    )


def test_customer_start_rejects_expired_session_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    now = created_at + timedelta(minutes=31)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234599")
    created = commit_authenticated_session(db_session, user, created_at, settings)
    token = csrf_value(created)
    created.session.expires_at = now - timedelta(seconds=1)
    db_session.commit()
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/customer/onboarding/start",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert_csrf_failed_without_customer(
        response,
        db_session,
        token,
        created.session.token_hash,
        created.session.csrf_secret,
    )


def test_customer_start_accepts_valid_same_session_header_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session, "+998901234600")
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/customer/onboarding/start",
        headers={"X-CSRF-Token": csrf_value(created)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customer/profile"
    assert_customer_security_headers(response)
    assert count_customers(db_session) == 1

    customer = db_session.scalar(select(Customer).where(Customer.user_id == user.id))
    assert customer is not None
    assert str(customer.id) not in response.text
