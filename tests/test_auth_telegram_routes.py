import base64
import inspect
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from html import unescape
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.auth.router as auth_router_module
import app.telegram.service as telegram_service_module
from app.auth.csrf import get_csrf_token
from app.auth.deps import (
    get_current_time,
    get_detached_mutation_session_context,
    validate_csrf,
)
from app.auth.error_codes import ErrorCode
from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.auth.service import create_user
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.auth.telegram_reauth import (
    TELEGRAM_REAUTH_IP_SCOPE,
    TELEGRAM_REAUTH_USER_SCOPE,
    TelegramReauthRateLimitPolicy,
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
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.inbound import TelegramUserIdentity
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.polling_repository import load_or_create_polling_state
from app.telegram.qr import TelegramQrRenderError
from app.telegram.rate_limit import (
    TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE,
    record_telegram_link_issuance_attempt,
)
from app.telegram.service import (
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkLifecycleInternalError,
)
from app.telegram.token import RawTelegramLinkToken, hash_telegram_link_token
from app.telegram.update_processing import (
    TelegramUpdateOutcomeCode,
)
from app.telegram.update_processing import (
    process_telegram_update_tx_a as _process_telegram_update_tx_a,
)

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-auth-telegram-routes"
TEST_CLIENT_HOST = "203.0.113.45"
TEST_BOT_USERNAME = "Nasiya_LinkBot"


def process_telegram_update_tx_a(*args, **kwargs):
    return _process_telegram_update_tx_a(
        *args,
        rate_limit_hmac_key=SecretStr(TEST_RATE_LIMIT_HMAC_KEY),
        **kwargs,
    )


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
        dependency_call in {validate_csrf, get_detached_mutation_session_context}
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


def test_unlink_domain_rollback_preserves_committed_reauth_prephase(
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
    assert scopes == {TELEGRAM_REAUTH_IP_SCOPE}


@pytest.mark.parametrize(
    ("path", "active_link_required", "expected_status", "expected_phase_count"),
    (
        ("/auth/telegram/link-token", False, 200, 3),
        ("/auth/telegram/relink-token", True, 200, 5),
        ("/auth/telegram/unlink", True, 303, 4),
    ),
)
def test_telegram_mutation_routes_close_auth_and_rate_prephases_before_domain(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
    active_link_required: bool,
    expected_status: int,
    expected_phase_count: int,
) -> None:
    now = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    client, settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    user_id = user.id
    auth_session_id = created.session.id
    if active_link_required:
        add_active_link(db_session, user, now - timedelta(minutes=1))

    stored_session = db_session.get(AuthSession, auth_session_id)
    assert stored_session is not None
    stored_session.last_seen_at = now - timedelta(minutes=10)
    client_ip = ResolvedClientIp(TEST_CLIENT_HOST)
    if path != "/auth/telegram/unlink":
        rate_result = record_telegram_link_issuance_attempt(
            db_session,
            settings,
            user,
            client_ip,
            now - timedelta(seconds=1),
        )
        assert rate_result.allowed
    if active_link_required:
        reauth_result = TelegramReauthRateLimitPolicy(
            db_session,
            settings,
        ).record_failure(
            user,
            client_ip,
            now - timedelta(seconds=1),
        )
        assert reauth_result.allowed
    db_session.commit()

    created_sessions: list[tuple[int, frozenset[int]]] = []
    closed_sessions: set[int] = set()

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._phase_number = len(created_sessions)
            created_sessions.append((self._phase_number, frozenset(closed_sessions)))

        def close(self) -> None:
            closed_sessions.add(self._phase_number)
            super().close()

    tracking_factory = sessionmaker(
        bind=m2_test_database,
        class_=TrackingSession,
    )
    client.app.state.database_session_factory = tracking_factory
    request_data = {"csrf_token": csrf_token(created)}
    headers: dict[str, str] = {}
    if path != "/auth/telegram/unlink":
        headers["HX-Request"] = "true"
    if active_link_required:
        request_data["current_password"] = "Password123"

    response = client.post(
        path,
        data=request_data,
        headers=headers,
        follow_redirects=False,
    )

    assert response.status_code == expected_status
    assert len(created_sessions) == expected_phase_count
    assert len({phase_number for phase_number, _closed in created_sessions}) == (
        expected_phase_count
    )
    for index, (_phase_number, closed_before_create) in enumerate(created_sessions):
        expected_closed = {
            phase_number for phase_number, _closed in created_sessions[:index]
        }
        assert expected_closed.issubset(closed_before_create)
    assert {phase_number for phase_number, _closed in created_sessions}.issubset(
        closed_sessions
    )

    db_session.expire_all()
    touched_session = db_session.get(AuthSession, auth_session_id)
    assert touched_session is not None
    assert touched_session.last_seen_at == now
    assert db_session.get(User, user_id) is not None


def test_link_issue_rate_row_barrier_stops_domain_after_closed_auth_prephase(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 28, 11, 30, tzinfo=UTC)
    client, settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    auth_session_id = created.session.id
    stored_session = db_session.get(AuthSession, auth_session_id)
    assert stored_session is not None
    stored_session.last_seen_at = now - timedelta(minutes=10)
    rate_result = record_telegram_link_issuance_attempt(
        db_session,
        settings,
        user,
        ResolvedClientIp(TEST_CLIENT_HOST),
        now - timedelta(seconds=1),
    )
    assert rate_result.allowed
    db_session.commit()

    session_factory = create_database_session_factory(m2_test_database)
    blocker = session_factory()
    blocked_row = blocker.scalar(
        select(AuthRateLimit)
        .where(AuthRateLimit.scope == TELEGRAM_LINK_RATE_LIMIT_USER_SCOPE)
        .with_for_update()
    )
    assert blocked_row is not None

    rate_phase_entered = Event()
    domain_phase_entered = Event()
    original_rate_phase = auth_router_module.record_link_token_issuance_rate_limit
    original_domain_phase = auth_router_module.issue_link_token_after_rate_limit

    def synchronized_rate_phase(*args, **kwargs):
        rate_phase_entered.set()
        return original_rate_phase(*args, **kwargs)

    def observe_domain_phase(*args, **kwargs):
        domain_phase_entered.set()
        return original_domain_phase(*args, **kwargs)

    monkeypatch.setattr(
        auth_router_module,
        "record_link_token_issuance_rate_limit",
        synchronized_rate_phase,
    )
    monkeypatch.setattr(
        auth_router_module,
        "issue_link_token_after_rate_limit",
        observe_domain_phase,
    )

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            client.post,
            "/auth/telegram/link-token",
            data={"csrf_token": csrf_token(created)},
            headers={"HX-Request": "true"},
        )
        assert rate_phase_entered.wait(timeout=5)
        assert not domain_phase_entered.is_set()
        with session_factory() as observer:
            visible_session = observer.get(AuthSession, auth_session_id)
            assert visible_session is not None
            assert visible_session.last_seen_at == now

        blocker.commit()
        response = future.result(timeout=10)
    finally:
        if blocker.in_transaction():
            blocker.rollback()
        blocker.close()
        executor.shutdown(wait=False, cancel_futures=True)

    assert response.status_code == 200
    assert domain_phase_entered.is_set()
    assert count_table(db_session, TelegramLinkToken) == 1


def test_reauth_rate_row_barrier_stops_domain_after_closed_password_phase(
    m2_test_database: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 28, 11, 45, tzinfo=UTC)
    client, settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    auth_session_id = created.session.id
    add_active_link(db_session, user, now - timedelta(minutes=1))
    stored_session = db_session.get(AuthSession, auth_session_id)
    assert stored_session is not None
    stored_session.last_seen_at = now - timedelta(minutes=10)
    reauth_result = TelegramReauthRateLimitPolicy(
        db_session,
        settings,
    ).record_failure(
        user,
        ResolvedClientIp(TEST_CLIENT_HOST),
        now - timedelta(seconds=1),
    )
    assert reauth_result.allowed
    db_session.commit()

    session_factory = create_database_session_factory(m2_test_database)
    blocker = session_factory()
    blocked_row = blocker.scalar(
        select(AuthRateLimit)
        .where(AuthRateLimit.scope == TELEGRAM_REAUTH_USER_SCOPE)
        .with_for_update()
    )
    assert blocked_row is not None

    rate_phase_entered = Event()
    password_phase_completed = Event()
    domain_phase_entered = Event()
    original_rate_check = TelegramReauthRateLimitPolicy.check_for_user_id
    original_password_phase = (
        auth_router_module._check_telegram_account_control_password_phase
    )
    original_domain_phase = auth_router_module._issue_telegram_link_token_domain_phase

    def synchronized_rate_check(self, *args, **kwargs):
        rate_phase_entered.set()
        return original_rate_check(self, *args, **kwargs)

    def observe_password_phase(*args, **kwargs):
        result = original_password_phase(*args, **kwargs)
        password_phase_completed.set()
        return result

    def observe_domain_phase(*args, **kwargs):
        domain_phase_entered.set()
        return original_domain_phase(*args, **kwargs)

    monkeypatch.setattr(
        TelegramReauthRateLimitPolicy,
        "check_for_user_id",
        synchronized_rate_check,
    )
    monkeypatch.setattr(
        auth_router_module,
        "_check_telegram_account_control_password_phase",
        observe_password_phase,
    )
    monkeypatch.setattr(
        auth_router_module,
        "_issue_telegram_link_token_domain_phase",
        observe_domain_phase,
    )

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(
            client.post,
            "/auth/telegram/relink-token",
            data={
                "csrf_token": csrf_token(created),
                "current_password": "Password123",
            },
            headers={"HX-Request": "true"},
        )
        assert rate_phase_entered.wait(timeout=5)
        assert password_phase_completed.is_set()
        assert not domain_phase_entered.is_set()
        with session_factory() as observer:
            visible_session = observer.get(AuthSession, auth_session_id)
            assert visible_session is not None
            assert visible_session.last_seen_at == now

        blocker.commit()
        response = future.result(timeout=10)
    finally:
        if blocker.in_transaction():
            blocker.rollback()
        blocker.close()
        executor.shutdown(wait=False, cancel_futures=True)

    assert response.status_code == 200
    assert domain_phase_entered.is_set()
    assert count_table(db_session, TelegramLinkToken) == 1


def test_telegram_mutation_runtime_call_graph_has_closed_prephases() -> None:
    assert not hasattr(telegram_service_module, "issue_link_token")
    assert not hasattr(telegram_service_module, "issue_relink_token")

    for route_callable in (
        auth_router_module.issue_telegram_link_token,
        auth_router_module.issue_telegram_relink_token,
        auth_router_module.unlink_telegram_account,
    ):
        route_source = inspect.getsource(route_callable)
        assert "get_detached_mutation_session_context" in route_source
        assert "Depends(get_database_session" not in route_source
        assert "Depends(get_current_session_context" not in route_source

    detached_source = inspect.getsource(get_detached_mutation_session_context)
    rate_source = inspect.getsource(auth_router_module._record_telegram_link_rate_phase)
    reauth_coordinator_source = inspect.getsource(
        auth_router_module._verify_telegram_account_control_password
    )
    reauth_rate_source = inspect.getsource(
        auth_router_module._decide_telegram_reauth_rate_phase
    )
    reauth_password_source = inspect.getsource(
        auth_router_module._check_telegram_account_control_password_phase
    )
    domain_sources = (
        inspect.getsource(auth_router_module._issue_telegram_link_token_domain_phase),
        inspect.getsource(auth_router_module._unlink_telegram_domain_phase),
    )
    assert "with session_factory.begin()" in detached_source
    assert "with session_factory.begin()" in rate_source
    assert "with session_factory.begin()" in reauth_rate_source
    for scalar_rate_operation in (
        "check_for_user_id",
        "record_failure_for_user_id",
        "clear_user_failures_after_success_for_user_id",
    ):
        assert scalar_rate_operation in reauth_rate_source
    assert "db.get(" not in reauth_rate_source
    assert "with session_factory.begin()" in reauth_password_source
    assert "TelegramReauthRateLimitPolicy" not in reauth_password_source
    assert "check_current_password" not in reauth_rate_source
    assert "with session_factory.begin()" not in reauth_coordinator_source
    assert reauth_coordinator_source.index(
        "_check_telegram_account_control_password_phase"
    ) < reauth_coordinator_source.index("_decide_telegram_reauth_rate_phase")
    assert all("with session_factory.begin()" in source for source in domain_sources)
    assert all(
        "record_link_token_issuance_rate_limit" not in source
        for source in domain_sources
    )
    assert "record_telegram_link_issuance_attempt" not in inspect.getsource(
        auth_router_module.issue_link_token_after_rate_limit
    )
    assert "record_telegram_link_issuance_attempt" not in inspect.getsource(
        auth_router_module.issue_relink_token_after_rate_limit
    )


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


def test_link_and_relink_reveals_require_explicit_external_activation(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    initial_page = client.get("/auth/telegram")
    initial_response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )
    add_active_link(db_session, user, now)
    linked_page = client.get("/auth/telegram")
    relink_response = client.post(
        "/auth/telegram/relink-token",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        headers={"HX-Request": "true"},
    )

    assert (
        'action="/auth/telegram/link-token"' in initial_page.text
        and 'hx-post="/auth/telegram/link-token"' in initial_page.text
        and 'hx-target="#telegram-link-reveal"' in initial_page.text
        and 'hx-swap="innerHTML"' in initial_page.text
        and '<button type="submit">' in initial_page.text
    )
    assert (
        'action="/auth/telegram/relink-token"' in linked_page.text
        and 'hx-post="/auth/telegram/relink-token"' in linked_page.text
        and 'hx-target="#telegram-link-reveal"' in linked_page.text
        and 'hx-swap="innerHTML"' in linked_page.text
        and '<button type="submit">' in linked_page.text
    )

    for response in (initial_response, relink_response):
        visible_html = unescape(response.text)
        deep_link_match = re.search(
            r'data-telegram-external-link="(?P<link>https://t\.me/[^"]+)"',
            visible_html,
        )

        assert response.status_code == 200
        assert response.headers.get("location") is None
        assert response.headers.get("hx-redirect") is None
        assert response.headers.get("hx-location") is None
        assert response.headers.get("refresh") is None
        assert response.headers["hx-push-url"] == "false"
        assert response.headers["content-type"].startswith("text/html")
        assert deep_link_match is not None
        assert '<a class="telegram-open-link" href="/auth/telegram"' in visible_html
        assert 'href="https://t.me/' not in visible_html
        assert "</a></p>" in visible_html
        assert "<form" not in visible_html
        assert "<button" not in visible_html
        assert 'http-equiv="refresh"' not in visible_html.casefold()
        assert "<script" not in visible_html.casefold()
        assert ".click(" not in visible_html
        assert "window.open" not in visible_html
        assert "location.href" not in visible_html
        assert 'data-telegram-attempt-status="WAITING"' in visible_html
        assert "data:image/png;base64," in visible_html

        raw_token = extract_start_token(response.text)
        assert raw_token in deep_link_match.group("link")
        assert raw_token not in str(response.request.url)
        assert raw_token not in caplog.text

    db_session.expire_all()
    stored_tokens = list(db_session.scalars(select(TelegramLinkToken)))
    assert len(stored_tokens) == 2
    for response, stored_token in zip(
        (initial_response, relink_response),
        stored_tokens,
        strict=True,
    ):
        raw_token = extract_start_token(response.text)
        assert raw_token not in stored_token.token_hash


def test_relink_attempt_owns_reveal_across_polls_until_contact_is_verified(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now, chat_id=9_900_501)

    stale_notice_page = client.get("/auth/telegram?notice=unlinked")
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
    status_url = f"/auth/telegram/attempts/{attempt_id}/status"

    assert issue_response.status_code == 200
    assert issue_response.headers["hx-push-url"] == "false"
    assert 'data-telegram-link-reveal="one-time"' in issue_response.text
    assert f'hx-get="{status_url}"' in issue_response.text
    assert "data:image/png;base64," in issue_response.text
    assert "Telegram bog'lanishi uzildi." not in unescape(stale_notice_page.text)

    account_status = client.get("/auth/telegram/status")
    assert 'data-telegram-link-status="LINKED"' in account_status.text
    assert "hx-retarget" not in account_status.headers
    assert "#telegram-link-reveal" not in account_status.text

    browser_fragment = issue_response.text
    attempt_pattern = re.compile(
        rf'<div id="telegram-attempt-status-{re.escape(str(attempt_id))}".*?</div>',
        re.DOTALL,
    )
    for _poll_interval in range(3):
        waiting_response = client.get(status_url)

        assert 'data-telegram-attempt-status="WAITING"' in waiting_response.text
        assert f'hx-get="{status_url}"' in waiting_response.text
        assert 'hx-trigger="every 3s"' in waiting_response.text
        assert waiting_response.headers.get("hx-retarget") is None
        assert raw_token not in waiting_response.text
        browser_fragment, replacement_count = attempt_pattern.subn(
            waiting_response.text,
            browser_fragment,
        )
        assert replacement_count == 1
        assert raw_token in unescape(browser_fragment)
        assert "data:image/png;base64," in browser_fragment

        db_session.expire_all()
        waiting_link = db_session.get(TelegramLink, link.id)
        assert waiting_link is not None
        assert waiting_link.telegram_chat_id == 9_900_501
        assert waiting_link.unlinked_at is None

    stored_token = db_session.get(TelegramLinkToken, attempt_id)
    assert stored_token is not None
    assert stored_token.token_hash == hash_telegram_link_token(
        RawTelegramLinkToken(raw_token)
    )
    assert raw_token not in stored_token.token_hash
    assert raw_token not in str(issue_response.request.url)
    assert raw_token not in str(account_status.request.url)
    assert raw_token not in caplog.text

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as worker_session:
        load_or_create_polling_state(worker_session)
    worker_result = process_telegram_update_tx_a(
        session_factory,
        update=TelegramUpdateEnvelope(
            update_id=810,
            message=TelegramMessageEnvelope(
                chat_id=9_900_502,
                chat_type="private",
                text=f"/start {raw_token}",
                structurally_valid=True,
                sender_identity=TelegramUserIdentity(9_900_502),
            ),
        ),
        now=now,
    )

    assert worker_result.outcome is TelegramUpdateOutcomeCode.CONTACT_REQUIRED
    pending_response = client.get(status_url)
    assert 'data-telegram-attempt-status="WAITING"' in pending_response.text
    assert f'hx-get="{status_url}"' in pending_response.text
    assert 'hx-trigger="every 3s"' in pending_response.text
    assert "hx-retarget" not in pending_response.headers
    assert raw_token not in pending_response.text

    db_session.expire_all()
    pending_link = db_session.get(TelegramLink, link.id)
    pending_token = db_session.get(TelegramLinkToken, attempt_id)
    assert pending_link is not None
    assert pending_link.telegram_chat_id == 9_900_501
    assert pending_link.unlinked_at is None
    assert pending_token is not None
    assert pending_token.pending_contact_binding_mac is not None
    assert pending_token.contact_requested_at == now
    assert pending_token.consumed_at is None


def test_unlink_then_initial_attempt_stays_waiting_after_start_binding(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now, chat_id=9_900_510)

    unlink_response = client.post(
        "/auth/telegram/unlink",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "Password123",
        },
        follow_redirects=False,
    )
    after_unlink_page = client.get(unlink_response.headers["location"])

    assert unlink_response.status_code == 303
    assert unlink_response.headers["location"] == "/auth/telegram?notice=unlinked"
    assert "Bog'lanmagan" in unescape(after_unlink_page.text)
    assert "Telegram bog'lanishi uzildi." in unescape(after_unlink_page.text)
    assert "Telegram hisobi boshqa Nasiya hisobiga bog'langan bo'lsa" in unescape(
        after_unlink_page.text
    )

    db_session.expire_all()
    unlinked = db_session.get(TelegramLink, link.id)
    assert unlinked is not None
    assert unlinked.user_id == user.id
    assert unlinked.telegram_chat_id is None
    assert unlinked.unlinked_at == now

    issue_response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )
    raw_token = extract_start_token(issue_response.text)
    attempt_id = extract_attempt_id(issue_response.text)
    status_url = f"/auth/telegram/attempts/{attempt_id}/status"

    assert issue_response.status_code == 200
    assert issue_response.headers["hx-push-url"] == "false"
    assert issue_response.headers["x-telegram-link-reveal"] == "one-time"
    assert f'hx-get="{status_url}"' in issue_response.text
    assert 'data-telegram-attempt-status="WAITING"' in issue_response.text
    assert "data:image/png;base64," in issue_response.text
    assert "Telegram hisobi boshqa" not in unescape(issue_response.text)

    db_session.expire_all()
    stored_token = db_session.get(TelegramLinkToken, attempt_id)
    assert stored_token is not None
    assert stored_token.user_id == user.id
    assert stored_token.consumed_at is None
    assert stored_token.invalidated_at is None
    assert stored_token.expires_at == now + timedelta(
        seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS
    )
    assert stored_token.token_hash == hash_telegram_link_token(
        RawTelegramLinkToken(raw_token)
    )
    assert raw_token not in stored_token.token_hash

    browser_fragment = issue_response.text
    attempt_pattern = re.compile(
        rf'<div id="telegram-attempt-status-{re.escape(str(attempt_id))}".*?</div>',
        re.DOTALL,
    )
    for _poll_interval in range(3):
        waiting_response = client.get(status_url)

        assert waiting_response.status_code == 200
        assert 'data-telegram-attempt-status="WAITING"' in waiting_response.text
        assert f'hx-get="{status_url}"' in waiting_response.text
        assert 'hx-trigger="every 3s"' in waiting_response.text
        assert waiting_response.headers.get("hx-retarget") is None
        assert waiting_response.headers.get("hx-reswap") is None
        assert "Telegram hisobi boshqa" not in unescape(waiting_response.text)
        assert raw_token not in waiting_response.text
        browser_fragment, replacement_count = attempt_pattern.subn(
            waiting_response.text,
            browser_fragment,
        )
        assert replacement_count == 1
        assert raw_token in unescape(browser_fragment)
        assert "data:image/png;base64," in browser_fragment

        db_session.expire_all()
        waiting_link = db_session.get(TelegramLink, link.id)
        waiting_token = db_session.get(TelegramLinkToken, attempt_id)
        assert waiting_link is not None
        assert waiting_link.telegram_chat_id is None
        assert waiting_link.unlinked_at == now
        assert waiting_token is not None
        assert waiting_token.consumed_at is None
        assert waiting_token.invalidated_at is None

    session_factory = create_database_session_factory(m2_test_database)
    with session_factory.begin() as worker_session:
        load_or_create_polling_state(worker_session)
    worker_result = process_telegram_update_tx_a(
        session_factory,
        update=TelegramUpdateEnvelope(
            update_id=811,
            message=TelegramMessageEnvelope(
                chat_id=9_900_511,
                chat_type="private",
                text=f"/start {raw_token}",
                structurally_valid=True,
                sender_identity=TelegramUserIdentity(9_900_511),
            ),
        ),
        now=now,
    )

    assert worker_result.outcome is TelegramUpdateOutcomeCode.CONTACT_REQUIRED
    pending_response = client.get(status_url)
    account_status = client.get("/auth/telegram/status")
    assert 'data-telegram-attempt-status="WAITING"' in pending_response.text
    assert f'hx-get="{status_url}"' in pending_response.text
    assert "hx-retarget" not in pending_response.headers
    assert 'data-telegram-link-status="UNLINKED"' in account_status.text
    assert raw_token not in pending_response.text
    assert raw_token not in account_status.text
    assert raw_token not in str(issue_response.request.url)
    assert raw_token not in caplog.text

    db_session.expire_all()
    pending_token = db_session.get(TelegramLinkToken, attempt_id)
    still_unlinked = db_session.get(TelegramLink, link.id)
    assert pending_token is not None
    assert pending_token.consumed_at is None
    assert pending_token.invalidated_at is None
    assert pending_token.pending_contact_binding_mac is not None
    assert pending_token.contact_requested_at == now
    assert still_unlinked is not None
    assert still_unlinked.telegram_chat_id is None
    assert still_unlinked.unlinked_at == now


def test_rate_limited_initial_issue_after_unlink_has_visible_error_contract(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now, chat_id=9_900_512)
    issue_data = {
        "csrf_token": csrf_token(created),
        "current_password": "Password123",
    }
    prior_issues = [
        client.post(
            "/auth/telegram/relink-token",
            data=issue_data,
            headers={"HX-Request": "true"},
        )
        for _attempt in range(3)
    ]
    prior_raw_tokens = [extract_start_token(response.text) for response in prior_issues]

    unlink_response = client.post(
        "/auth/telegram/unlink",
        data=issue_data,
        follow_redirects=False,
    )
    rate_limited_response = client.post(
        "/auth/telegram/link-token",
        data={"csrf_token": csrf_token(created)},
        headers={"HX-Request": "true"},
    )
    after_unlink_page = client.get(unlink_response.headers["location"])

    assert all(response.status_code == 200 for response in prior_issues)
    assert unlink_response.status_code == 303
    assert rate_limited_response.status_code == 429
    assert rate_limited_response.headers["x-error-code"] == ErrorCode.RATE_LIMITED.value
    assert '<div role="alert">' in rate_limited_response.text
    assert "Juda ko'p urinish." in unescape(rate_limited_response.text)
    assert "data-telegram-link-reveal" not in rate_limited_response.text
    assert "data-telegram-attempt-status" not in rate_limited_response.text
    assert "Telegram hisobi boshqa" not in unescape(rate_limited_response.text)
    assert '"code":"[4]..","swap":true,"error":true' in after_unlink_page.text

    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    stored_tokens = list(
        db_session.scalars(
            select(TelegramLinkToken).where(TelegramLinkToken.user_id == user.id)
        )
    )
    assert stored_link is not None
    assert stored_link.telegram_chat_id is None
    assert stored_link.unlinked_at == now
    assert len(stored_tokens) == 3
    assert all(token.consumed_at is None for token in stored_tokens)
    assert all(token.invalidated_at == now for token in stored_tokens)
    for raw_token in prior_raw_tokens:
        assert raw_token not in rate_limited_response.text
        assert raw_token not in caplog.text


def test_relink_attempt_terminal_states_and_foreign_uuid_are_safe(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    client, _settings, user, created = create_logged_in_client(
        m2_test_database,
        db_session,
        now,
    )
    link = add_active_link(db_session, user, now, chat_id=9_900_503)
    issue_data = {
        "csrf_token": csrf_token(created),
        "current_password": "Password123",
    }

    wrong_password_response = client.post(
        "/auth/telegram/relink-token",
        data={
            "csrf_token": csrf_token(created),
            "current_password": "WrongPassword123",
        },
        headers={"HX-Request": "true"},
    )
    assert wrong_password_response.status_code == 403
    assert count_table(db_session, TelegramLinkToken) == 0

    first_issue = client.post(
        "/auth/telegram/relink-token",
        data=issue_data,
        headers={"HX-Request": "true"},
    )
    first_attempt_id = extract_attempt_id(first_issue.text)
    first_raw_token = extract_start_token(first_issue.text)
    second_issue = client.post(
        "/auth/telegram/relink-token",
        data=issue_data,
        headers={"HX-Request": "true"},
    )
    second_attempt_id = extract_attempt_id(second_issue.text)
    second_raw_token = extract_start_token(second_issue.text)

    superseded_response = client.get(
        f"/auth/telegram/attempts/{first_attempt_id}/status"
    )
    waiting_response = client.get(f"/auth/telegram/attempts/{second_attempt_id}/status")
    assert 'data-telegram-attempt-status="SUPERSEDED"' in superseded_response.text
    assert 'data-telegram-attempt-status="WAITING"' in waiting_response.text

    expired_now = now + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS)
    client.app.dependency_overrides[get_current_time] = lambda: expired_now
    expired_response = client.get(f"/auth/telegram/attempts/{second_attempt_id}/status")
    assert 'data-telegram-attempt-status="EXPIRED"' in expired_response.text
    assert "hx-get" not in expired_response.text

    foreign_user = commit_user(db_session, "+998901234598")
    foreign_token = TelegramLinkToken(
        user_id=foreign_user.id,
        token_hash="e" * 64,
        created_at=now,
        expires_at=now + timedelta(minutes=20),
    )
    db_session.add(foreign_token)
    db_session.commit()
    foreign_response = client.get(f"/auth/telegram/attempts/{foreign_token.id}/status")

    assert 'data-telegram-attempt-status="UNAVAILABLE"' in foreign_response.text
    assert (
        foreign_response.text
        == client.get(f"/auth/telegram/attempts/{uuid4()}/status").text
    )
    for raw_token in (first_raw_token, second_raw_token):
        assert raw_token not in superseded_response.text
        assert raw_token not in waiting_response.text
        assert raw_token not in expired_response.text
        assert raw_token not in foreign_response.text
        assert raw_token not in caplog.text

    db_session.expire_all()
    stored_link = db_session.get(TelegramLink, link.id)
    assert stored_link is not None
    assert stored_link.telegram_chat_id == 9_900_503
    assert stored_link.unlinked_at is None


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
    deep_link_match = re.search(
        r'data-telegram-external-link="(?P<link>https://t\.me/[^"]+)"',
        visible_html,
    )
    image_match = re.search(
        r'src="data:image/png;base64,(?P<png>[A-Za-z0-9+/=]+)"',
        response.text,
    )
    assert deep_link_match is not None
    assert image_match is not None
    assert captured_links == [deep_link_match.group("link")]
    assert '<a class="telegram-open-link" href="/auth/telegram"' in visible_html
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


def test_attempt_polling_handles_supersession_binding_and_foreign_uuid(
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
                sender_identity=TelegramUserIdentity(9_900_200),
            ),
        ),
        now=now,
    )
    assert worker_result.outcome is TelegramUpdateOutcomeCode.CONTACT_REQUIRED

    first_terminal_response = client.get(
        f"/auth/telegram/attempts/{first_attempt_id}/status"
    )
    pending_response = client.get(f"/auth/telegram/attempts/{second_attempt_id}/status")
    assert 'data-telegram-attempt-status="SUPERSEDED"' in first_terminal_response.text
    assert "hx-get" not in first_terminal_response.text
    assert first_terminal_response.headers["hx-retarget"] == "#telegram-link-reveal"
    assert 'data-telegram-attempt-status="WAITING"' in pending_response.text
    assert "hx-get" in pending_response.text
    assert "hx-retarget" not in pending_response.headers
    db_session.expire_all()
    pending_token = db_session.get(TelegramLinkToken, second_attempt_id)
    assert pending_token is not None
    assert pending_token.pending_contact_binding_mac is not None
    assert pending_token.contact_requested_at == now
    assert pending_token.consumed_at is None

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


def test_password_protected_relink_start_only_binds_pending_contact(
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
                sender_identity=TelegramUserIdentity(9_900_402),
            ),
        ),
        now=now,
    )
    assert worker_result.outcome is TelegramUpdateOutcomeCode.CONTACT_REQUIRED

    status_response = client.get(f"/auth/telegram/attempts/{attempt_id}/status")
    assert 'data-telegram-attempt-status="WAITING"' in status_response.text
    db_session.expire_all()
    after_binding = db_session.get(TelegramLink, link.id)
    pending_token = db_session.get(TelegramLinkToken, attempt_id)
    assert after_binding is not None
    assert after_binding.telegram_chat_id == 9_900_401
    assert after_binding.unlinked_at is None
    assert pending_token is not None
    assert pending_token.pending_contact_binding_mac is not None
    assert pending_token.contact_requested_at == now
    assert pending_token.consumed_at is None
    assert (
        list(
            db_session.scalars(
                select(TelegramLinkEvent.action).where(
                    TelegramLinkEvent.user_id == user.id
                )
            )
        )
        == []
    )


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
