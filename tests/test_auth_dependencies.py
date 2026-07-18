from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import (
    CurrentSessionContext,
    CurrentSessionStatus,
    get_current_session_context,
    get_current_time,
    require_user,
)
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import (
    CreatedSession,
    create_anonymous_session,
    create_authenticated_session,
    revoke_session,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-deps"


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
        session_ttl_days=10,
        session_touch_interval_minutes=5,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_client(engine: Engine, now: datetime) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now

    @application.get("/_test/current-session")
    def read_current_session(
        context: Annotated[
            CurrentSessionContext,
            Depends(get_current_session_context),
        ],
    ) -> dict[str, str | bool | None]:
        return {
            "status": context.status.value,
            "authenticated": context.is_authenticated,
            "session_id": str(context.session_id) if context.session_id else None,
            "user_id": str(context.user_id) if context.user_id else None,
        }

    @application.get("/_test/protected")
    def read_protected(
        user: Annotated[User, Depends(require_user)],
    ) -> dict[str, str]:
        return {"user_id": str(user.id)}

    return TestClient(application), settings


def set_session_cookie(
    client: TestClient,
    settings: Settings,
    created: CreatedSession,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
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
        "pytest",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def get_session_times(engine: Engine, session_id) -> tuple[datetime, datetime]:
    session_factory = create_database_session_factory(engine)
    with session_factory() as session:
        stored_session = session.get(AuthSession, session_id)
        assert stored_session is not None
        return stored_session.last_seen_at, stored_session.expires_at


def test_current_session_context_treats_missing_cookie_as_anonymous(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/_test/current-session")

    assert response.status_code == 200
    assert response.json() == {
        "status": CurrentSessionStatus.ANONYMOUS,
        "authenticated": False,
        "session_id": None,
        "user_id": None,
    }


def test_require_user_redirects_missing_cookie_to_fixed_login_path(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get(
        "/_test/protected?next=https://evil.example",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_current_session_context_resolves_valid_cookie_and_touches_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    request_time = created_at + timedelta(minutes=10)
    client, settings = make_client(m2_test_database, request_time)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, created_at, settings)
    set_session_cookie(client, settings, created)

    response = client.get("/_test/protected")

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user.id)}
    last_seen_at, expires_at = get_session_times(
        m2_test_database,
        created.session.id,
    )
    assert last_seen_at == request_time
    assert expires_at == request_time + timedelta(days=settings.session_ttl_days)


def test_current_session_context_separates_invalid_cookie_reason_safely(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    raw_token = "not-a-real-session-token"
    client.cookies.set(settings.session_cookie_name, raw_token)

    response = client.get("/_test/current-session")

    assert response.status_code == 200
    assert response.json()["status"] == CurrentSessionStatus.INVALID
    assert raw_token not in response.text


def test_current_session_context_separates_revoked_cookie_reason(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    request_time = created_at + timedelta(minutes=2)
    client, settings = make_client(m2_test_database, request_time)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, created_at, settings)
    revoke_session(db_session, created.session, created_at + timedelta(minutes=1))
    db_session.commit()
    set_session_cookie(client, settings, created)

    response = client.get("/_test/current-session")

    assert response.status_code == 200
    assert response.json()["status"] == CurrentSessionStatus.REVOKED
    protected_response = client.get("/_test/protected", follow_redirects=False)
    assert protected_response.status_code == 303


def test_current_session_context_separates_expired_cookie_reason(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    request_time = created_at + timedelta(minutes=2)
    client, settings = make_client(m2_test_database, request_time)
    user = commit_user(db_session)
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest",
        created_at,
        settings=settings,
    )
    created.session.expires_at = request_time
    db_session.commit()
    set_session_cookie(client, settings, created)

    response = client.get("/_test/current-session")

    assert response.status_code == 200
    assert response.json()["status"] == CurrentSessionStatus.EXPIRED
    protected_response = client.get("/_test/protected", follow_redirects=False)
    assert protected_response.status_code == 303


def test_current_session_context_treats_anonymous_session_as_anonymous(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    created_at = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    request_time = created_at + timedelta(minutes=2)
    client, settings = make_client(m2_test_database, request_time)
    created = create_anonymous_session(
        db_session,
        "pytest",
        created_at,
        settings=settings,
    )
    db_session.commit()
    set_session_cookie(client, settings, created)

    response = client.get("/_test/current-session")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == CurrentSessionStatus.ANONYMOUS
    assert body["authenticated"] is False
    assert body["session_id"] == str(created.session.id)
    assert body["user_id"] is None
    protected_response = client.get("/_test/protected", follow_redirects=False)
    assert protected_response.status_code == 303
