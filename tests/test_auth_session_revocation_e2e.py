import re
from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.service import create_user
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-session-revocation-e2e"
PHONE = "+998901234567"
PASSWORD = "Password123"


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
        session_touch_interval_minutes=60,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_application(engine: Engine, now: datetime):
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return application


def make_client(application) -> TestClient:
    return TestClient(application)


def commit_user(db_session: Session) -> User:
    result = create_user(db_session, PHONE, PASSWORD)
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def login(client: TestClient) -> None:
    login_page = client.get("/auth/login")
    assert login_page.status_code == 200
    csrf_token = extract_csrf_token(login_page.text)

    response = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_token,
            "phone": PHONE,
            "password": PASSWORD,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/account"


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="(?P<token>[^"]+)"', html)
    assert match is not None
    return match.group("token")


def extract_revoke_session_ids(html: str) -> list[UUID]:
    return [
        UUID(value)
        for value in re.findall(
            r'action="/auth/sessions/(?P<session_id>[0-9a-f-]{36})/revoke"',
            html,
        )
    ]


def get_active_session_ids(db_session: Session, user: User) -> set[UUID]:
    db_session.expire_all()
    return set(
        db_session.scalars(
            select(AuthSession.id).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        )
    )


def get_revoked_session_ids(db_session: Session, user: User) -> set[UUID]:
    db_session.expire_all()
    return set(
        db_session.scalars(
            select(AuthSession.id).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_not(None),
            )
        )
    )


def test_two_clients_can_revoke_one_session_without_logging_out_current_client(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    user = commit_user(db_session)
    application = make_application(m2_test_database, now)
    client_a = make_client(application)
    client_b = make_client(application)
    login(client_a)
    login(client_b)

    sessions_page = client_a.get("/auth/sessions")
    csrf_token = extract_csrf_token(sessions_page.text)
    visible_revoke_ids = set(extract_revoke_session_ids(sessions_page.text))
    active_before = get_active_session_ids(db_session, user)

    assert sessions_page.status_code == 200
    assert len(active_before) == 2
    assert visible_revoke_ids.issubset(active_before)
    assert len(visible_revoke_ids) == 1
    session_to_revoke = next(iter(visible_revoke_ids))

    revoke_response = client_a.post(
        f"/auth/sessions/{session_to_revoke}/revoke",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert revoke_response.status_code == 303
    assert revoke_response.headers["location"] == "/auth/sessions"
    assert client_b.get("/auth/account", follow_redirects=False).status_code == 303
    account_a = client_a.get("/auth/account", follow_redirects=False)
    assert account_a.status_code == 200
    assert get_active_session_ids(db_session, user) == active_before - {
        session_to_revoke
    }


def test_revoke_others_revokes_second_and_third_clients_but_keeps_current(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    user = commit_user(db_session)
    application = make_application(m2_test_database, now)
    client_a = make_client(application)
    client_b = make_client(application)
    client_c = make_client(application)
    login(client_a)
    login(client_b)
    login(client_c)
    active_before = get_active_session_ids(db_session, user)
    sessions_page = client_a.get("/auth/sessions")
    csrf_token = extract_csrf_token(sessions_page.text)

    response = client_a.post(
        "/auth/sessions/revoke-others",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/sessions"
    assert client_a.get("/auth/account", follow_redirects=False).status_code == 200
    assert client_b.get("/auth/account", follow_redirects=False).status_code == 303
    assert client_c.get("/auth/account", follow_redirects=False).status_code == 303
    active_after = get_active_session_ids(db_session, user)
    revoked_after = get_revoked_session_ids(db_session, user)
    assert len(active_before) == 3
    assert len(active_after) == 1
    assert active_after.issubset(active_before)
    assert revoked_after == active_before - active_after


def test_current_session_revoke_logs_out_that_client_only(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    user = commit_user(db_session)
    application = make_application(m2_test_database, now)
    client_a = make_client(application)
    client_b = make_client(application)
    login(client_a)
    login(client_b)
    sessions_page = client_a.get("/auth/sessions")
    csrf_token = extract_csrf_token(sessions_page.text)
    active_before = get_active_session_ids(db_session, user)
    other_session_id = set(extract_revoke_session_ids(sessions_page.text)).pop()
    current_session_id = next(iter(active_before - {other_session_id}))

    response = client_a.post(
        f"/auth/sessions/{current_session_id}/revoke",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert client_a.get("/auth/account", follow_redirects=False).status_code == 303
    assert client_b.get("/auth/account", follow_redirects=False).status_code == 200
    assert get_active_session_ids(db_session, user) == {other_session_id}
