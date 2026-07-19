from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.router import router as auth_router
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-logout-post"


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


def commit_user(db_session: Session) -> User:
    result = create_user(db_session, "+998901234567", "Password123")
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
        "pytest-logout",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def create_logged_in_client(
    engine: Engine,
    db_session: Session,
    now: datetime,
) -> tuple[TestClient, Settings, CreatedSession]:
    client, settings = make_client(engine, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created.raw_token.as_cookie_value())
    return client, settings, created


def post_logout(client: TestClient, csrf_token: str):
    return client.post(
        "/auth/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )


def assert_auth_security_headers(response) -> None:
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_no_store(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL


def assert_delete_cookie(response, settings: Settings) -> None:
    set_cookie = response.headers["set-cookie"]

    assert set_cookie.startswith(f"{settings.session_cookie_name}=")
    assert "Max-Age=0" in set_cookie
    assert "Path=/" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def assert_session_active(db_session: Session, session: AuthSession) -> None:
    db_session.expire_all()
    stored_session = db_session.get(AuthSession, session.id)
    assert stored_session is not None
    assert stored_session.revoked_at is None


def assert_session_revoked_at(
    db_session: Session,
    session: AuthSession,
    now: datetime,
) -> None:
    db_session.expire_all()
    stored_session = db_session.get(AuthSession, session.id)
    assert stored_session is not None
    assert stored_session.revoked_at == now


def test_post_logout_revokes_session_deletes_cookie_and_redirects_login(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    raw_cookie = created.raw_token.as_cookie_value()
    csrf_token = get_csrf_token(created.session).as_form_value()

    response = post_logout(client, csrf_token)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert_no_store(response)
    assert_auth_security_headers(response)
    assert_delete_cookie(response, settings)
    assert_session_revoked_at(db_session, created.session, now)
    assert raw_cookie not in response.text
    assert created.session.token_hash not in response.text
    assert created.session.csrf_secret not in response.text


def test_post_logout_old_cookie_cannot_access_protected_account(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    raw_cookie = created.raw_token.as_cookie_value()
    response = post_logout(client, get_csrf_token(created.session).as_form_value())
    assert response.status_code == 303
    set_client_session_cookie(client, settings, raw_cookie)

    protected_response = client.get("/auth/account", follow_redirects=False)

    assert protected_response.status_code == 303
    assert protected_response.headers["location"] == "/auth/login"
    assert_delete_cookie(protected_response, settings)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"csrf_token": "wrong-csrf-token"},
    ],
)
def test_post_logout_missing_or_wrong_csrf_fails_and_keeps_session_active(
    m2_test_database: Engine,
    db_session: Session,
    data: dict[str, str],
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    response = client.post("/auth/logout", data=data, follow_redirects=False)

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_auth_security_headers(response)
    assert_session_active(db_session, created.session)
    assert "wrong-csrf-token" not in response.text
    assert created.session.token_hash not in response.text
    assert created.session.csrf_secret not in response.text


def test_get_logout_is_not_available(m2_test_database: Engine) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/auth/logout", follow_redirects=False)

    assert response.status_code in {404, 405}
    assert_auth_security_headers(response)


def test_post_logout_with_revoked_session_is_safe(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    raw_cookie = created.raw_token.as_cookie_value()
    csrf_token = get_csrf_token(created.session).as_form_value()
    first_response = post_logout(client, csrf_token)
    assert first_response.status_code == 303
    set_client_session_cookie(client, _settings, raw_cookie)

    second_response = post_logout(client, csrf_token)

    assert second_response.status_code == 403
    assert second_response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_auth_security_headers(second_response)
    assert_session_revoked_at(db_session, created.session, now)
    assert raw_cookie not in second_response.text
    assert created.session.token_hash not in second_response.text
    assert created.session.csrf_secret not in second_response.text


def test_auth_router_has_post_logout_but_no_get_logout() -> None:
    logout_route_methods = set()
    for route in auth_router.routes:
        if isinstance(route, APIRoute) and route.path_format == "/auth/logout":
            logout_route_methods.update(route.methods or set())

    assert logout_route_methods == {"POST"}
