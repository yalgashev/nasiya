import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from html import unescape

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time, validate_csrf
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.context import resolve_current_shop
from app.shop.dependencies import require_shop_owner, require_shop_staff
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.service import TELEGRAM_LINK_TOKEN_TTL_SECONDS
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-telegram-routes"
TEST_CLIENT_HOST = "203.0.113.45"
TEST_BOT_USERNAME = "Nasiya_LinkBot"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Iterator[Session]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_settings(
    engine: Engine,
    *,
    bot_username: str | None = TEST_BOT_USERNAME,
    client_ip_mode: str = "direct",
    trusted_proxy_cidrs: list[str] | None = None,
) -> Settings:
    values: dict[str, object] = {
        "app_environment": "testing",
        "debug": False,
        "database_url": engine.url.render_as_string(hide_password=False),
        "session_cookie_secure": False,
        "telegram_bot_username": bot_username,
        "rate_limit_hmac_key": TEST_RATE_LIMIT_HMAC_KEY,
        "client_ip_mode": client_ip_mode,
    }
    if trusted_proxy_cidrs is not None:
        values["trusted_proxy_cidrs"] = trusted_proxy_cidrs
    return Settings(_env_file=None, **values)


def make_client(
    engine: Engine,
    now: datetime,
    *,
    bot_username: str | None = TEST_BOT_USERNAME,
    client_host: str = TEST_CLIENT_HOST,
    client_ip_mode: str = "direct",
    trusted_proxy_cidrs: list[str] | None = None,
) -> tuple[TestClient, Settings]:
    settings = make_settings(
        engine,
        bot_username=bot_username,
        client_ip_mode=client_ip_mode,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: now
    return TestClient(application, client=(client_host, 50_000)), settings


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
        "pytest-auth-telegram",
        now,
        settings=settings,
    )
    db_session.commit()
    return created


def create_logged_in_client(
    engine: Engine,
    db_session: Session,
    now: datetime,
    *,
    phone: str = "+998901234567",
    bot_username: str | None = TEST_BOT_USERNAME,
    client_host: str = TEST_CLIENT_HOST,
    client_ip_mode: str = "direct",
    trusted_proxy_cidrs: list[str] | None = None,
) -> tuple[TestClient, Settings, User, CreatedSession]:
    client, settings = make_client(
        engine,
        now,
        bot_username=bot_username,
        client_host=client_host,
        client_ip_mode=client_ip_mode,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
    )
    user = commit_user(db_session, phone)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(
        client,
        settings,
        created.raw_token.as_cookie_value(),
    )
    return client, settings, user, created


def add_active_link(
    db_session: Session,
    user: User,
    now: datetime,
    *,
    chat_id: int = 9_900_100,
) -> TelegramLink:
    link = TelegramLink(
        user_id=user.id,
        telegram_chat_id=chat_id,
        linked_at=now,
        updated_at=now,
    )
    db_session.add(link)
    db_session.commit()
    return link


def csrf_token(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def count_table(db_session: Session, model) -> int:
    db_session.expire_all()
    return db_session.scalar(select(func.count()).select_from(model)) or 0


def fetch_only_token(db_session: Session) -> TelegramLinkToken:
    db_session.expire_all()
    token = db_session.scalar(select(TelegramLinkToken))
    assert token is not None
    return token


def extract_start_token(html: str) -> str:
    visible_html = unescape(html)
    match = re.search(
        r"https://t\.me/nasiya_linkbot\?start=(?P<token>[A-Za-z0-9_-]+)",
        visible_html,
    )
    assert match is not None
    return match.group("token")


def assert_auth_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def iter_api_routes(routes: list[object]) -> Iterator[APIRoute]:
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


def iter_dependency_calls(dependant: Dependant) -> Iterator[object]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from iter_dependency_calls(dependency)


def route_has_csrf_dependency(route: APIRoute) -> bool:
    return any(
        dependency_call is validate_csrf
        for dependency_call in iter_dependency_calls(route.dependant)
    )


def test_telegram_routes_are_account_scoped_and_csrf_protected(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)
    application = client.app
    routes = [
        route
        for route in iter_api_routes(application.routes)
        if route.path_format.startswith("/auth/telegram")
    ]
    route_paths = {route.path_format for route in routes}

    assert route_paths == {
        "/auth/telegram",
        "/auth/telegram/status",
        "/auth/telegram/link-token",
        "/auth/telegram/relink-token",
        "/auth/telegram/unlink",
    }

    forbidden_shop_dependencies = {
        require_shop_staff,
        require_shop_owner,
        resolve_current_shop,
    }
    for route in routes:
        dependency_calls = set(iter_dependency_calls(route.dependant))
        assert forbidden_shop_dependencies.isdisjoint(dependency_calls)
        if (route.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}:
            assert route_has_csrf_dependency(route)


def test_get_telegram_page_requires_authenticated_account_session(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/auth/telegram", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_auth_security_headers(response)


def test_get_telegram_page_renders_without_shop_context(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    response = client.get("/auth/telegram")

    assert response.status_code == 200
    assert_auth_security_headers(response)
    visible_html = unescape(response.text)
    assert "Telegram" in visible_html
    assert "Bog'lanmagan" in visible_html
    assert 'action="/auth/telegram/link-token"' in response.text
    assert 'hx-post="/auth/telegram/link-token"' in response.text
    assert "active_shop_id" not in response.text
    assert "require_shop" not in response.text
    assert str(user.id) not in response.text
    assert user.phone not in response.text
    assert created.session.token_hash not in response.text


def test_link_token_reveal_is_one_time_no_store_and_hash_only(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert_auth_security_headers(response)
    assert response.headers["x-telegram-link-reveal"] == "one-time"
    assert 'data-telegram-link-reveal="one-time"' in response.text
    raw_token = extract_start_token(response.text)
    token = fetch_only_token(db_session)
    assert token.user_id == user.id
    assert token.token_hash == hash_telegram_link_token(RawTelegramLinkToken(raw_token))
    assert token.created_at == now
    assert token.expires_at == now + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS)
    assert raw_token not in token.token_hash
    assert str(user.id) not in response.text
    assert user.phone not in response.text
    assert token.token_hash not in response.text
    assert created.session.token_hash not in response.text


def test_link_token_without_bot_username_fails_closed_without_token(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
        bot_username=None,
    )
    page_response = client.get("/auth/telegram")
    assert page_response.status_code == 200
    assert "Telegram bog'lash havolasi sozlanmagan." in unescape(page_response.text)

    response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 422
    assert response.headers["x-error-code"] == ErrorCode.VALIDATION_ERROR.value
    assert_auth_security_headers(response)
    assert "Telegram bot havolasi hali sozlanmagan." in unescape(response.text)
    assert count_table(db_session, TelegramLinkToken) == 0


def test_trusted_proxy_link_issue_uses_one_x_real_ip(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
        client_host="10.10.1.2",
        client_ip_mode="trusted_proxy",
        trusted_proxy_cidrs=["10.0.0.0/8"],
    )

    response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true", "X-Real-IP": "203.0.113.90"},
    )

    assert response.status_code == 200
    assert response.headers["x-telegram-link-reveal"] == "one-time"
    assert count_table(db_session, TelegramLinkToken) == 1


def test_untrusted_proxy_link_issue_fails_before_rate_limit_or_token_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
        client_host="192.0.2.40",
        client_ip_mode="trusted_proxy",
        trusted_proxy_cidrs=["10.0.0.0/8"],
    )

    response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true", "X-Real-IP": "203.0.113.91"},
    )

    assert response.status_code == 422
    assert response.headers["x-error-code"] == ErrorCode.VALIDATION_ERROR.value
    assert "203.0.113.91" not in response.text
    assert "192.0.2.40" not in response.text
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, AuthRateLimit) == 0


def test_telegram_posts_require_csrf_and_do_not_mutate_on_failure(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, _created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    response = client.post(
        "/auth/telegram/link-token",
        data={},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_auth_security_headers(response)
    assert count_table(db_session, TelegramLinkToken) == 0


def test_relink_and_unlink_use_current_account_only(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    linked_at = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    now = linked_at + timedelta(minutes=5)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
        phone="+998901234568",
    )
    link = add_active_link(db_session, user, linked_at)

    relink_response = client.post(
        "/auth/telegram/relink-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )

    assert relink_response.status_code == 200
    relink_raw_token = extract_start_token(relink_response.text)
    relink_token = fetch_only_token(db_session)
    assert relink_token.user_id == user.id
    assert relink_token.token_hash == hash_telegram_link_token(
        RawTelegramLinkToken(relink_raw_token)
    )

    unlink_response = client.post(
        "/auth/telegram/unlink",
        data={"csrf_token": csrf_token(created)},
        follow_redirects=False,
    )

    assert unlink_response.status_code == 303
    assert unlink_response.headers["location"] == "/auth/telegram?notice=unlinked"
    assert_auth_security_headers(unlink_response)
    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    assert stored_link is not None
    assert stored_link.user_id == user.id
    assert stored_link.telegram_chat_id is None
    assert stored_link.unlinked_at == now
    assert stored_link.updated_at == now
    stored_token = db_session.get(TelegramLinkToken, relink_token.id)
    assert stored_token is not None
    assert stored_token.invalidated_at == now
    event_actions = db_session.scalars(
        select(TelegramLinkEvent.action).where(TelegramLinkEvent.user_id == user.id)
    ).all()
    assert event_actions == ["unlinked"]
