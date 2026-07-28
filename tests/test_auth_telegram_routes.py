import base64
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from html import unescape
from uuid import UUID, uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.auth.router as auth_router_module
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time, validate_csrf
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.auth.telegram_reauth import (
    TELEGRAM_REAUTH_IP_SCOPE,
    TELEGRAM_REAUTH_USER_SCOPE,
)
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.context import resolve_current_shop
from app.shop.dependencies import require_shop_owner, require_shop_staff
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.telegram.bot_api import TelegramMessageEnvelope, TelegramUpdateEnvelope
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.polling_repository import load_or_create_polling_state
from app.telegram.qr import TelegramQrRenderError
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkLifecycleInternalError,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token
from app.telegram.update_processing import (
    TelegramUpdateOutcomeCode,
    process_telegram_update_tx_a,
)

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


def extract_attempt_id(html: str) -> UUID:
    match = re.search(r'data-attempt-id="(?P<attempt_id>[0-9a-f-]{36})"', html)
    assert match is not None
    return UUID(match.group("attempt_id"))


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
        "/auth/telegram/attempts/{attempt_id}/status",
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


def test_get_telegram_attempt_status_requires_authenticated_account_session(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get(
        f"/auth/telegram/attempts/{uuid4()}/status",
        follow_redirects=False,
    )

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
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
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
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
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


def test_non_htmx_issue_is_repeatable_mutation_free_prg(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    for _attempt in range(2):
        response = client.post(
            "/auth/telegram/link-token",
            data={"csrf_token": csrf_token(created)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == (
            "/auth/telegram?notice=javascript_required"
        )
        assert_auth_security_headers(response)

    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, AuthRateLimit) == 0


def test_wrong_password_changes_no_link_token_or_event(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    linked_at = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    now = linked_at + timedelta(minutes=5)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, linked_at)
    existing_token = TelegramLinkToken(
        user_id=user.id,
        token_hash="d" * 64,
        created_at=linked_at,
        expires_at=now + timedelta(minutes=5),
    )
    db_session.add(existing_token)
    db_session.commit()
    wrong_password = "WrongPassword123"

    relink_response = client.post(
        "/auth/telegram/relink-token",
        data={
            "csrf_token": csrf_token(created),
            "current_password": wrong_password,
        },
        headers={"HX-Request": "true"},
    )
    unlink_response = client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": wrong_password,
        },
        follow_redirects=False,
    )

    assert relink_response.status_code == 403
    assert relink_response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    assert "Joriy parol noto'g'ri." in unescape(relink_response.text)
    assert unlink_response.status_code == 303
    assert unlink_response.headers["location"] == (
        "/auth/telegram?error=current_password"
    )
    assert unlink_response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    stored_token = db_session.get(TelegramLinkToken, existing_token.id)
    assert stored_link is not None
    assert stored_link.telegram_chat_id == link.telegram_chat_id
    assert stored_link.unlinked_at is None
    assert stored_token is not None
    assert stored_token.invalidated_at is None
    assert count_table(db_session, TelegramLinkToken) == 1
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert wrong_password not in relink_response.text
    assert wrong_password not in unlink_response.text
    assert wrong_password not in caplog.text


def test_reauth_limiter_blocks_fifth_failure_and_keeps_session_usable(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now)

    responses = [
        client.post(
            "/auth/telegram/unlink",
            data={
                "csrf_token": csrf_token(created),
                "current_password": "WrongPassword123",
            },
            follow_redirects=False,
        )
        for _attempt in range(5)
    ]
    blocked_correct_password = client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        follow_redirects=False,
    )

    assert all(response.status_code == 303 for response in responses)
    assert all(
        response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
        for response in responses[:4]
    )
    assert responses[4].headers["x-error-code"] == ErrorCode.RATE_LIMITED.value
    assert responses[4].headers["location"] == (
        "/auth/telegram?error=reauth_rate_limit"
    )
    assert (
        blocked_correct_password.headers["x-error-code"] == ErrorCode.RATE_LIMITED.value
    )
    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    assert stored_link is not None
    assert stored_link.telegram_chat_id is not None
    assert count_table(db_session, TelegramLinkEvent) == 0
    assert client.get("/auth/telegram").status_code == 200


def test_successful_unlink_clears_user_reauth_bucket_only(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    add_active_link(db_session, user, now)
    client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "WrongPassword123",
        },
        follow_redirects=False,
    )

    response = client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/telegram?notice=unlinked"
    records = list(db_session.scalars(select(AuthRateLimit)))
    assert [record.scope for record in records] == [TELEGRAM_REAUTH_IP_SCOPE]
    assert all(record.scope != TELEGRAM_REAUTH_USER_SCOPE for record in records)
    assert count_table(db_session, TelegramLinkEvent) == 1


def test_repeated_unlink_is_password_checked_safe_noop(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    response = client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == ("/auth/telegram?notice=already_unlinked")
    assert count_table(db_session, TelegramLinkEvent) == 0


def test_non_htmx_relink_does_not_verify_or_mutate(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now)

    for _attempt in range(2):
        response = client.post(
            "/auth/telegram/relink-token",
            data={
                "csrf_token": csrf_token(created),
                "current_password": "WrongPassword123",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == (
            "/auth/telegram?notice=javascript_required"
        )

    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    assert stored_link is not None
    assert stored_link.telegram_chat_id is not None
    assert count_table(db_session, TelegramLinkToken) == 0
    assert count_table(db_session, AuthRateLimit) == 0


def test_unlink_transaction_rolls_back_domain_and_reauth_clear(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now)
    client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "WrongPassword123",
        },
        follow_redirects=False,
    )

    def fail_after_mutation(db, current_user, current_time) -> None:
        stored_link = db.get(TelegramLink, link.id)
        assert stored_link is not None
        stored_link.telegram_chat_id = None
        stored_link.unlinked_at = current_time
        db.add(
            TelegramLinkEvent(
                user_id=current_user.id,
                action="unlinked",
                occurred_at=current_time,
            )
        )
        db.flush()
        raise TelegramLinkLifecycleInternalError("forced rollback")

    monkeypatch.setattr(auth_router_module, "unlink_telegram", fail_after_mutation)

    with pytest.raises(TelegramLinkLifecycleInternalError):
        client.post(
            "/auth/telegram/unlink",
            data={
                "csrf_token": csrf_token(created),
                "current_password": "Password123",
            },
            follow_redirects=False,
        )
    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    assert stored_link is not None
    assert stored_link.telegram_chat_id is not None
    assert stored_link.unlinked_at is None
    assert count_table(db_session, TelegramLinkEvent) == 0
    scopes = set(db_session.scalars(select(AuthRateLimit.scope)))
    assert scopes == {TELEGRAM_REAUTH_USER_SCOPE, TELEGRAM_REAUTH_IP_SCOPE}


def test_forged_htmx_header_does_not_bypass_auth_or_csrf(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    anonymous_client, _settings = make_client(m2_test_database, now)

    anonymous_response = anonymous_client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": "forged"},
        headers={"HX-Request": "true"},
    )

    assert anonymous_response.status_code == 403
    assert anonymous_response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_auth_security_headers(anonymous_response)

    client, _settings, _user, _created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    csrf_response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": "forged"},
        headers={"HX-Request": "true"},
    )

    assert csrf_response.status_code == 403
    assert csrf_response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert count_table(db_session, TelegramLinkToken) == 0


def test_one_time_reveal_has_attempt_polling_and_history_fences(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    page_response = client.get("/auth/telegram")
    response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )

    raw_token = extract_start_token(response.text)
    attempt_id = extract_attempt_id(response.text)
    assert response.text.count(raw_token) == 1
    assert response.headers["hx-push-url"] == "false"
    assert 'hx-history="false"' in response.text
    assert f'hx-get="/auth/telegram/attempts/{attempt_id}/status"' in response.text
    assert 'hx-trigger="load delay:3s"' in response.text
    assert "localStorage" not in response.text
    assert "sessionStorage" not in response.text
    assert "<script" not in response.text
    assert raw_token not in str(response.request.url)
    assert "/static/vendor/htmx-2.0.4.min.js" in page_response.text


def test_one_time_qr_encodes_the_exact_button_link(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    captured_links: list[str] = []
    fake_png = b"\x89PNG\r\n\x1a\nsame-link-png"

    def fake_qr_renderer(start_link: str) -> bytes:
        captured_links.append(start_link)
        return fake_png

    monkeypatch.setattr(
        auth_router_module,
        "render_telegram_start_link_qr_png",
        fake_qr_renderer,
    )

    response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )

    visible_html = unescape(response.text)
    href_match = re.search(r'href="(?P<link>https://t\.me/[^"]+)"', visible_html)
    image_match = re.search(
        r'src="data:image/png;base64,(?P<png>[A-Za-z0-9+/=]+)"',
        response.text,
    )
    assert href_match is not None
    assert image_match is not None
    assert captured_links == [href_match.group("link")]
    assert base64.b64decode(image_match.group("png")) == fake_png
    assert 'alt="Telegram bog&#x27;lash havolasi uchun QR-kod"' in response.text
    assert "/auth/telegram/qr" not in response.text
    assert_auth_security_headers(response)


def test_qr_failure_keeps_button_and_does_not_reissue_token(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )

    def fail_qr_rendering(_start_link: str) -> bytes:
        raise TelegramQrRenderError()

    monkeypatch.setattr(
        auth_router_module,
        "render_telegram_start_link_qr_png",
        fail_qr_rendering,
    )

    response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )

    raw_token = extract_start_token(response.text)
    assert response.status_code == 200
    assert "Telegramda ochish" in response.text
    assert "data:image/png" not in response.text
    assert count_table(db_session, TelegramLinkToken) == 1
    assert raw_token not in caplog.text


def test_attempt_polling_handles_supersession_link_and_foreign_uuid(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    issue_headers = {"HX-Request": "true"}

    first_response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers=issue_headers,
    )
    first_attempt_id = extract_attempt_id(first_response.text)
    second_response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers=issue_headers,
    )
    second_attempt_id = extract_attempt_id(second_response.text)
    second_raw_token = extract_start_token(second_response.text)

    superseded_response = client.get(
        f"/auth/telegram/attempts/{first_attempt_id}/status"
    )
    waiting_response = client.get(f"/auth/telegram/attempts/{second_attempt_id}/status")

    assert 'data-telegram-attempt-status="SUPERSEDED"' in superseded_response.text
    assert superseded_response.headers["hx-retarget"] == "#telegram-link-reveal"
    assert superseded_response.headers["hx-reswap"] == "innerHTML"
    assert "hx-get" not in superseded_response.text
    assert 'data-telegram-attempt-status="WAITING"' in waiting_response.text
    assert 'hx-trigger="every 3s"' in waiting_response.text
    assert "hx-retarget" not in waiting_response.headers
    assert second_raw_token not in waiting_response.text

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as worker_session:
        load_or_create_polling_state(worker_session)
    worker_result = process_telegram_update_tx_a(
        session_factory,
        update=TelegramUpdateEnvelope(
            update_id=700,
            message=TelegramMessageEnvelope(
                chat_id=9_900_200,
                chat_type="private",
                text=f"/start {second_raw_token}",
                structurally_valid=True,
            ),
        ),
        now=now,
    )
    assert worker_result.outcome is TelegramUpdateOutcomeCode.LINKED

    for attempt_id in (first_attempt_id, second_attempt_id):
        linked_response = client.get(f"/auth/telegram/attempts/{attempt_id}/status")
        assert 'data-telegram-attempt-status="LINKED"' in linked_response.text
        assert "hx-get" not in linked_response.text
        assert linked_response.headers["hx-retarget"] == "#telegram-link-reveal"

    foreign_user = commit_user(db_session, "+998901234599")
    foreign_token = TelegramLinkToken(
        user_id=foreign_user.id,
        token_hash="f" * 64,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    db_session.add(foreign_token)
    db_session.commit()

    foreign_response = client.get(f"/auth/telegram/attempts/{foreign_token.id}/status")
    unknown_response = client.get(f"/auth/telegram/attempts/{uuid4()}/status")

    for unavailable_response in (foreign_response, unknown_response):
        assert 'data-telegram-attempt-status="UNAVAILABLE"' in unavailable_response.text
        assert str(user.id) not in unavailable_response.text
        assert user.phone not in unavailable_response.text
        assert "hx-get" not in unavailable_response.text
    assert foreign_response.text == unknown_response.text

    malformed_response = client.get("/auth/telegram/attempts/not-a-uuid/status")
    assert malformed_response.status_code == 200
    assert malformed_response.text == unknown_response.text
    assert_auth_security_headers(malformed_response)


def test_telegram_account_flow_ignores_active_suspended_and_revoked_shop(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    shop = Shop(
        name="Telegram Isolation Shop",
        phone="+998907770001",
        status=ShopStatus.ACTIVE.value,
    )
    db_session.add(shop)
    db_session.flush()
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=ShopRole.CASHIER.value,
    )
    db_session.add(staff)
    auth_session = db_session.get(AuthSession, created.session.id)
    assert auth_session is not None
    auth_session.active_shop_id = shop.id
    db_session.commit()

    for expected_status in (
        ShopStatus.ACTIVE.value,
        ShopStatus.SUSPENDED.value,
    ):
        shop.status = expected_status
        db_session.commit()
        response = client.get("/auth/telegram")
        assert response.status_code == 200
        assert "Bog'lanmagan" in unescape(response.text)
        db_session.expire_all()
        stored_session = db_session.get(AuthSession, created.session.id)
        assert stored_session is not None
        assert stored_session.active_shop_id == shop.id

    staff = db_session.get(ShopStaff, staff.id)
    assert staff is not None
    staff.is_active = False
    staff.revoked_at = now
    db_session.commit()

    revoked_response = client.get("/auth/telegram")
    assert revoked_response.status_code == 200
    db_session.expire_all()
    stored_session = db_session.get(AuthSession, created.session.id)
    assert stored_session is not None
    assert stored_session.active_shop_id == shop.id


def test_protected_relink_and_unlink_ignore_suspended_and_revoked_shop(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now, chat_id=9_900_300)
    shop = Shop(
        name="Protected Telegram Shop",
        phone="+998907770002",
        status=ShopStatus.SUSPENDED.value,
    )
    db_session.add(shop)
    db_session.flush()
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=ShopRole.CASHIER.value,
    )
    db_session.add(staff)
    auth_session = db_session.get(AuthSession, created.session.id)
    assert auth_session is not None
    auth_session.active_shop_id = shop.id
    db_session.commit()

    first_relink = client.post(
        "/auth/telegram/relink-token",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        headers={"HX-Request": "true"},
    )
    assert first_relink.status_code == 200
    first_attempt_id = extract_attempt_id(first_relink.text)

    staff = db_session.get(ShopStaff, staff.id)
    assert staff is not None
    staff.is_active = False
    staff.revoked_at = now
    db_session.commit()

    second_relink = client.post(
        "/auth/telegram/relink-token",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        headers={"HX-Request": "true"},
    )
    assert second_relink.status_code == 200
    second_attempt_id = extract_attempt_id(second_relink.text)
    assert second_attempt_id != first_attempt_id

    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    stored_session = db_session.get(AuthSession, created.session.id)
    first_token = db_session.get(TelegramLinkToken, first_attempt_id)
    assert stored_link is not None
    assert stored_link.telegram_chat_id == 9_900_300
    assert stored_link.unlinked_at is None
    assert stored_session is not None
    assert stored_session.active_shop_id == shop.id
    assert first_token is not None
    assert first_token.invalidated_at == now

    unlink_response = client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        follow_redirects=False,
    )

    assert unlink_response.status_code == 303
    db_session.expire_all()
    stored_session = db_session.get(AuthSession, created.session.id)
    stored_link = db_session.get(TelegramLink, link.id)
    assert stored_session is not None
    assert stored_session.active_shop_id == shop.id
    assert stored_link is not None
    assert stored_link.telegram_chat_id is None


def test_password_protected_relink_is_atomic_through_fake_worker(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now, chat_id=9_900_401)

    issue_response = client.post(
        "/auth/telegram/relink-token",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        headers={"HX-Request": "true"},
    )
    raw_token = extract_start_token(issue_response.text)
    attempt_id = extract_attempt_id(issue_response.text)

    db_session.expire_all()
    before_consume = db_session.get(TelegramLink, link.id)
    assert before_consume is not None
    assert before_consume.telegram_chat_id == 9_900_401
    assert before_consume.unlinked_at is None

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as worker_session:
        load_or_create_polling_state(worker_session)
    worker_result = process_telegram_update_tx_a(
        session_factory,
        update=TelegramUpdateEnvelope(
            update_id=800,
            message=TelegramMessageEnvelope(
                chat_id=9_900_402,
                chat_type="private",
                text=f"/start {raw_token}",
                structurally_valid=True,
            ),
        ),
        now=now,
    )
    assert worker_result.outcome is TelegramUpdateOutcomeCode.RELINKED

    status_response = client.get(f"/auth/telegram/attempts/{attempt_id}/status")
    assert 'data-telegram-attempt-status="LINKED"' in status_response.text
    db_session.expire_all()
    after_consume = db_session.get(TelegramLink, link.id)
    assert after_consume is not None
    assert after_consume.telegram_chat_id == 9_900_402
    assert after_consume.unlinked_at is None
    assert list(
        db_session.scalars(
            select(TelegramLinkEvent.action).where(TelegramLinkEvent.user_id == user.id)
        )
    ) == ["relinked"]


def test_telegram_page_and_attempt_status_support_russian(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, _user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    language_headers = {"Accept-Language": "ru-RU,uz;q=0.8"}

    page_response = client.get("/auth/telegram", headers=language_headers)
    issue_response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={**language_headers, "HX-Request": "true"},
    )
    attempt_id = extract_attempt_id(issue_response.text)
    status_response = client.get(
        f"/auth/telegram/attempts/{attempt_id}/status",
        headers=language_headers,
    )

    assert '<html lang="ru">' in page_response.text
    assert "Не подключен" in page_response.text
    assert "Открыть в Telegram" in issue_response.text
    assert "Ожидаем подтверждение в Telegram." in status_response.text
    for response in (page_response, issue_response, status_response):
        assert_auth_security_headers(response)


def test_protected_account_controls_support_russian_messages(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    add_active_link(db_session, user, now)
    language_headers = {"Accept-Language": "ru-RU"}

    page_response = client.get("/auth/telegram", headers=language_headers)
    wrong_password_response = client.post(
        "/auth/telegram/relink-token",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "WrongPassword123",
        },
        headers={**language_headers, "HX-Request": "true"},
    )
    unlink_response = client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        headers=language_headers,
        follow_redirects=False,
    )
    notice_response = client.get(
        unlink_response.headers["location"],
        headers=language_headers,
    )

    assert "Текущий пароль" in page_response.text
    assert "Текущий пароль указан неверно." in wrong_password_response.text
    assert "Telegram отключен." in notice_response.text
    assert "WrongPassword123" not in wrong_password_response.text
