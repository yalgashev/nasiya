from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-workspace"


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


def unique_phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def add_user(db_session: Session) -> User:
    user = User(phone=unique_phone())
    db_session.add(user)
    db_session.flush()
    return user


def add_shop(
    db_session: Session,
    *,
    name: str = "Router Shop",
    status: str = ShopStatus.ACTIVE.value,
) -> Shop:
    shop = Shop(name=name, phone=unique_phone(), status=status)
    db_session.add(shop)
    db_session.flush()
    return shop


def add_staff(
    db_session: Session,
    *,
    shop: Shop,
    user: User,
    role: str,
) -> ShopStaff:
    staff = ShopStaff(shop_id=shop.id, user_id=user.id, role=role)
    db_session.add(staff)
    db_session.flush()
    return staff


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
    active_shop: Shop | None = None,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-shop-workspace",
        now,
        settings=settings,
    )
    if active_shop is not None:
        created.session.active_shop_id = active_shop.id
    db_session.commit()
    return created


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


def commit_user_with_shop(
    db_session: Session,
    *,
    role: str,
    status: str = ShopStatus.ACTIVE.value,
) -> tuple[User, Shop, ShopStaff]:
    user = add_user(db_session)
    shop = add_shop(db_session, status=status)
    staff = add_staff(db_session, shop=shop, user=user, role=role)
    extra_user = add_user(db_session)
    add_staff(db_session, shop=shop, user=extra_user, role=ShopRole.CASHIER.value)
    db_session.commit()
    return user, shop, staff


def assert_shop_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.parametrize(
    ("role", "label"),
    [
        (ShopRole.OWNER.value, "egasi"),
        (ShopRole.MANAGER.value, "menejer"),
        (ShopRole.CASHIER.value, "kassir"),
    ],
)
def test_shop_workspace_renders_for_all_active_roles(
    m2_test_database: Engine,
    db_session: Session,
    role: str,
    label: str,
) -> None:
    now = datetime(2026, 7, 27, 16, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user, shop, _staff = commit_user_with_shop(db_session, role=role)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_shop_security_headers(response)
    html = unescape(response.text)
    assert shop.name in html
    assert "faol" in html
    assert label in html
    assert "Faol xodimlar" in html
    assert ">2<" in html
    assert "Mijozlar va qarzlar keyingi bosqichda paydo bo'ladi" in html
    assert "faqat ko'rish rejimi" not in html
    assert 'href="/shop/select"' not in html
    assert str(shop.id) not in html
    assert shop.phone not in html
    assert user.phone not in html


def test_shop_workspace_renders_suspended_read_only_state(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 16, 5, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user, shop, _staff = commit_user_with_shop(
        db_session,
        role=ShopRole.OWNER.value,
        status=ShopStatus.SUSPENDED.value,
    )
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop")

    assert response.status_code == 200
    assert_shop_security_headers(response)
    html = unescape(response.text)
    assert shop.name in html
    assert "to'xtatilgan" in html
    assert "faqat ko'rish rejimi" in html
    assert "Mijozlar va qarzlar keyingi bosqichda paydo bo'ladi" in html
    assert str(shop.id) not in html
    assert shop.phone not in html
    assert user.phone not in html


def test_shop_workspace_redirects_authenticated_user_without_membership_to_select(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 16, 10, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/select"
    assert_shop_security_headers(response)


def test_shop_workspace_redirects_anonymous_user_to_login(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 27, 16, 15, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/shop", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_shop_security_headers(response)


def test_shop_workspace_shows_switcher_link_for_multiple_memberships(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 16, 20, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    shop_a = add_shop(db_session, name="Workspace A")
    shop_b = add_shop(db_session, name="Workspace B")
    add_staff(db_session, shop=shop_a, user=user, role=ShopRole.OWNER.value)
    add_staff(db_session, shop=shop_b, user=user, role=ShopRole.MANAGER.value)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        active_shop=shop_a,
    )
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop")

    assert response.status_code == 200
    assert_shop_security_headers(response)
    html = unescape(response.text)
    assert "Workspace A" in html
    assert "Workspace B" not in html
    assert 'href="/shop/select"' in html
    stored_session = db_session.get(AuthSession, created.session.id)
    assert stored_session is not None
    db_session.refresh(stored_session)
    assert stored_session.active_shop_id == shop_a.id
