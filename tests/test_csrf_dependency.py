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
)
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-csrf-dependency"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(engine: Engine, *, debug: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=debug,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        session_ttl_days=10,
        anonymous_session_ttl_minutes=30,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def make_client(
    engine: Engine,
    now: datetime,
    *,
    debug: bool = False,
) -> tuple[TestClient, Settings]:
    settings = make_settings(engine, debug=debug)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now

    @application.api_route(
        "/_test/unsafe-body",
        methods=["POST", "PUT", "PATCH", "DELETE"],
    )
    async def unsafe_body_route(
        request: Request,
        _csrf: Annotated[None, Depends(validate_csrf)],
    ) -> dict[str, str]:
        return {
            "method": request.method,
            "body": (await request.body()).decode("utf-8"),
        }

    @application.api_route(
        "/_test/safe",
        methods=["GET", "HEAD", "OPTIONS"],
    )
    def safe_route(
        _csrf: Annotated[None, Depends(validate_csrf)],
    ) -> dict[str, str]:
        return {"ok": "safe"}

    return TestClient(application), settings


def commit_user(db_session: Session) -> User:
    result = create_user(db_session, "+998901234567", "Password123")
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
        "pytest",
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
        "pytest",
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


def get_csrf_form_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def assert_csrf_failed(response) -> None:
    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert response.json()["detail"]["code"] == "CSRF_FAILED"


def test_safe_methods_do_not_require_csrf_cookie_or_token(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    get_response = client.get("/_test/safe")
    head_response = client.head("/_test/safe")
    options_response = client.options("/_test/safe")

    assert get_response.status_code == 200
    assert get_response.json() == {"ok": "safe"}
    assert head_response.status_code == 200
    assert options_response.status_code == 200


def test_unsafe_method_requires_current_server_side_session(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.post(
        "/_test/unsafe-body",
        headers={"X-CSRF-Token": "token-without-session"},
    )

    assert_csrf_failed(response)


def test_form_csrf_token_allows_login_post_with_anonymous_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/unsafe-body",
        data={
            "csrf_token": get_csrf_form_value(created),
            "phone": "901234567",
        },
    )

    assert response.status_code == 200
    body = response.json()["body"]
    assert "csrf_token=" in body
    assert "phone=901234567" in body


def test_header_csrf_token_allows_json_without_consuming_body(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = commit_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_session_cookie(client, settings, created)
    raw_body = '{"secret":"route-body"}'

    response = client.post(
        "/_test/unsafe-body",
        content=raw_body,
        headers={
            "content-type": "application/json",
            "X-CSRF-Token": get_csrf_form_value(created),
        },
    )

    assert response.status_code == 200
    assert response.json()["body"] == raw_body


def test_form_and_header_tokens_must_match(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    csrf_token = get_csrf_form_value(created)
    set_session_cookie(client, settings, created)

    matching_response = client.post(
        "/_test/unsafe-body",
        data={"csrf_token": csrf_token, "value": "same"},
        headers={"X-CSRF-Token": csrf_token},
    )
    mismatch_response = client.post(
        "/_test/unsafe-body",
        data={"csrf_token": csrf_token, "value": "different"},
        headers={"X-CSRF-Token": "different-token"},
    )

    assert matching_response.status_code == 200
    assert_csrf_failed(mismatch_response)


@pytest.mark.parametrize("submitted_token", [None, "", "wrong-token"])
def test_missing_empty_or_wrong_token_fails(
    m2_test_database: Engine,
    db_session: Session,
    submitted_token: str | None,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)
    data = {"value": "without-token"}
    if submitted_token is not None:
        data["csrf_token"] = submitted_token

    response = client.post("/_test/unsafe-body", data=data)

    assert_csrf_failed(response)


def test_revoked_session_csrf_dependency_fails(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now + timedelta(minutes=2))
    created = commit_anonymous_session(db_session, now, settings)
    token = get_csrf_form_value(created)
    revoke_session(db_session, created.session, now + timedelta(minutes=1))
    db_session.commit()
    set_session_cookie(client, settings, created)

    response = client.post("/_test/unsafe-body", data={"csrf_token": token})

    assert_csrf_failed(response)


def test_expired_session_csrf_dependency_fails(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now + timedelta(minutes=2))
    created = commit_anonymous_session(db_session, now, settings)
    token = get_csrf_form_value(created)
    created.session.expires_at = now + timedelta(minutes=1)
    db_session.commit()
    set_session_cookie(client, settings, created)

    response = client.post("/_test/unsafe-body", data={"csrf_token": token})

    assert_csrf_failed(response)


def test_json_body_is_not_logged_when_csrf_fails(
    m2_test_database: Engine,
    db_session: Session,
    caplog,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)
    raw_body = '{"secret":"do-not-log-this"}'

    response = client.post(
        "/_test/unsafe-body",
        content=raw_body,
        headers={"content-type": "application/json"},
    )

    assert_csrf_failed(response)
    assert "do-not-log-this" not in caplog.text


def test_csrf_failure_returns_safe_html_response(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)
    raw_cookie = created.raw_token.as_cookie_value()

    response = client.post(
        "/_test/unsafe-body",
        data={"csrf_token": "wrong-token"},
        headers={"accept": "text/html"},
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert response.headers["content-type"].startswith("text/html")
    assert 'data-error-code="CSRF_FAILED"' in response.text
    assert "So&#x27;rov xavfsizlik tekshiruvidan o&#x27;tmadi." in response.text
    assert "wrong-token" not in response.text
    assert raw_cookie not in response.text
    assert created.session.token_hash not in response.text
    assert created.session.csrf_secret not in response.text
    assert "Traceback" not in response.text
    assert "stack" not in response.text.casefold()


def test_csrf_failure_returns_safe_htmx_fragment(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/unsafe-body",
        data={"csrf_token": "wrong-token"},
        headers={"HX-Request": "true", "accept": "text/html"},
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "CSRF_FAILED"
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (
        '<div role="alert" data-error-code="CSRF_FAILED">'
        "So&#x27;rov xavfsizlik tekshiruvidan o&#x27;tmadi."
        "</div>"
    )
    assert "wrong-token" not in response.text
    assert created.raw_token.as_cookie_value() not in response.text
    assert created.session.token_hash not in response.text
    assert created.session.csrf_secret not in response.text


@pytest.mark.parametrize("debug", [False, True])
def test_csrf_failure_never_exposes_internal_traceback(
    m2_test_database: Engine,
    db_session: Session,
    debug: bool,
) -> None:
    now = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now, debug=debug)
    created = commit_anonymous_session(db_session, now, settings)
    set_session_cookie(client, settings, created)

    response = client.post(
        "/_test/unsafe-body",
        data={"csrf_token": "wrong-token"},
        headers={"accept": "text/html"},
    )

    assert response.status_code == 403
    assert "Traceback" not in response.text
    assert "csrf validation failed" not in response.text
    assert "wrong-token" not in response.text
    assert created.raw_token.as_cookie_value() not in response.text
    assert created.session.token_hash not in response.text
