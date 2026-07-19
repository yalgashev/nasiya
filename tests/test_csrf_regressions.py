from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time, validate_csrf
from app.auth.models import User
from app.auth.service import create_user
from app.auth.sessions import (
    CreatedSession,
    create_anonymous_session,
    create_authenticated_session,
    revoke_session,
    rotate_session,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-csrf-regressions"


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
        anonymous_session_ttl_minutes=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_client(engine: Engine, now: datetime) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now

    @application.post("/_test/csrf-regression")
    async def unsafe_route(
        request: Request,
        _csrf: Annotated[None, Depends(validate_csrf)],
    ) -> dict[str, str]:
        return {"body": (await request.body()).decode("utf-8")}

    @application.get("/_test/csrf-regression")
    def safe_route(
        _csrf: Annotated[None, Depends(validate_csrf)],
    ) -> dict[str, str]:
        return {"ok": "safe"}

    return TestClient(application), settings


def commit_user(db_session: Session, phone: str = "+998901234567") -> User:
    result = create_user(db_session, phone, "Password123")
    assert result.succeeded is True
    assert result.user is not None
    db_session.commit()
    return result.user


def commit_anonymous_session(
    db_session: Session,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_anonymous_session(
        db_session,
        "csrf-regression",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "csrf-regression",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def set_session_cookie(
    client: TestClient,
    settings: Settings,
    created: CreatedSession,
) -> None:
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
    )


def csrf_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def assert_csrf_failed(response) -> None:
    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert response.json()["detail"]["code"] == "CSRF_FAILED"


def assert_response_does_not_leak(
    response,
    *,
    token: str,
    created: CreatedSession,
) -> None:
    assert token not in response.text
    assert created.raw_token.as_cookie_value() not in response.text
    assert created.session.token_hash not in response.text
    assert created.session.csrf_secret not in response.text
    assert "Traceback" not in response.text


def test_a_session_token_is_rejected_for_b_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    session_a = commit_anonymous_session(db_session, now, settings)
    session_b = commit_anonymous_session(db_session, now, settings)
    token_a = csrf_value(session_a)
    set_session_cookie(client, settings, session_b)

    response = client.post(
        "/_test/csrf-regression",
        data={"csrf_token": token_a},
    )

    assert_csrf_failed(response)
    assert_response_does_not_leak(response, token=token_a, created=session_b)


def test_anonymous_old_token_is_rejected_after_login_rotation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    login_time = now + timedelta(minutes=1)
    client, settings = make_client(m2_test_database, login_time)
    user = commit_user(db_session)
    anonymous = commit_anonymous_session(db_session, now, settings)
    old_token = csrf_value(anonymous)
    rotated = rotate_session(
        db_session,
        anonymous.session,
        user.id,
        "csrf-regression-login",
        login_time,
        settings=settings,
    )
    db_session.commit()
    set_session_cookie(client, settings, rotated)

    response = client.post(
        "/_test/csrf-regression",
        data={"csrf_token": old_token},
    )

    assert_csrf_failed(response)
    assert_response_does_not_leak(response, token=old_token, created=rotated)


def test_revoked_session_token_is_rejected(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now + timedelta(minutes=2))
    created = commit_anonymous_session(db_session, now, settings)
    token = csrf_value(created)
    revoke_session(db_session, created.session, now + timedelta(minutes=1))
    db_session.commit()
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/csrf-regression",
        data={"csrf_token": token},
    )

    assert_csrf_failed(response)


def test_expired_session_token_is_rejected(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now + timedelta(minutes=2))
    created = commit_anonymous_session(db_session, now, settings)
    token = csrf_value(created)
    created.session.expires_at = now + timedelta(minutes=1)
    db_session.commit()
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/csrf-regression",
        data={"csrf_token": token},
    )

    assert_csrf_failed(response)


def test_missing_form_field_returns_403(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)

    response = client.post("/_test/csrf-regression", data={"field": "value"})

    assert_csrf_failed(response)


def test_wrong_header_returns_403(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/csrf-regression",
        headers={"X-CSRF-Token": "wrong-token"},
    )

    assert_csrf_failed(response)


def test_valid_form_token_passes(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/csrf-regression",
        data={"csrf_token": csrf_value(created), "field": "value"},
    )

    assert response.status_code == 200
    assert "field=value" in response.json()["body"]


def test_valid_header_token_passes(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_session_cookie(client, settings, created)
    raw_body = '{"action":"save"}'

    response = client.post(
        "/_test/csrf-regression",
        content=raw_body,
        headers={
            "content-type": "application/json",
            "X-CSRF-Token": csrf_value(created),
        },
    )

    assert response.status_code == 200
    assert response.json()["body"] == raw_body


def test_get_does_not_require_csrf_token(m2_test_database: Engine) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/_test/csrf-regression")

    assert response.status_code == 200
    assert response.json() == {"ok": "safe"}


def test_failure_response_does_not_expose_token_cookie_or_hash(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    token = "wrong-token-that-must-not-leak"
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/csrf-regression",
        data={"csrf_token": token},
        headers={"accept": "text/html"},
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert_response_does_not_leak(response, token=token, created=created)
