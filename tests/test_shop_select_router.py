from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
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

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-select"


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
    name: str = "Select Shop",
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
    is_active: bool = True,
) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=role,
        is_active=is_active,
        revoked_at=None if is_active else datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
    )
    db_session.add(staff)
    db_session.flush()
    return staff


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
    *,
    active_shop: Shop | None = None,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-shop-select",
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


def csrf_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def fetch_active_shop_id(db_session: Session, created: CreatedSession) -> UUID | None:
    stored_session = db_session.get(AuthSession, created.session.id)
    assert stored_session is not None
    db_session.refresh(stored_session)
    return stored_session.active_shop_id


def assert_shop_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_forbidden_select_response_is_safe(
    response,
    *,
    hidden_values: tuple[str, ...],
) -> None:
    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    assert_shop_security_headers(response)
    assert "Bu do'konni tanlash mumkin emas." in response.text
    for hidden_value in hidden_values:
        assert hidden_value not in response.text


def test_get_shop_select_with_zero_memberships_renders_message_without_side_effect(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 17, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop/select", follow_redirects=False)

    assert response.status_code == 200
    assert_shop_security_headers(response)
    html = unescape(response.text)
    assert "Siz hali hech bir do'konga bog'lanmagansiz." in html
    assert '<form method="post" action="/shop/select">' not in html
    assert fetch_active_shop_id(db_session, created) is None


def test_get_shop_select_with_one_membership_lists_it_without_auto_select(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 17, 5, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    shop = add_shop(db_session, name="Bitta Do'kon")
    add_staff(db_session, shop=shop, user=user, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop/select", follow_redirects=False)

    assert response.status_code == 200
    assert_shop_security_headers(response)
    html = unescape(response.text)
    assert "Bitta Do'kon" in html
    assert "Holat: faol" in html
    assert str(shop.id) in html
    assert shop.phone not in html
    assert fetch_active_shop_id(db_session, created) is None


def test_get_shop_select_with_multiple_memberships_lists_active_and_suspended(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 17, 10, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    active_shop = add_shop(db_session, name="Faol Do'kon")
    suspended_shop = add_shop(
        db_session,
        name="Suspend Do'kon",
        status=ShopStatus.SUSPENDED.value,
    )
    add_staff(db_session, shop=active_shop, user=user, role=ShopRole.OWNER.value)
    add_staff(db_session, shop=suspended_shop, user=user, role=ShopRole.CASHIER.value)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop/select", follow_redirects=False)

    assert response.status_code == 200
    assert_shop_security_headers(response)
    html = unescape(response.text)
    assert "Faol Do'kon" in html
    assert "Suspend Do'kon" in html
    assert "Holat: faol" in html
    assert "Holat: to'xtatilgan" in html
    assert str(active_shop.id) in html
    assert str(suspended_shop.id) in html
    assert active_shop.phone not in html
    assert suspended_shop.phone not in html
    assert fetch_active_shop_id(db_session, created) is None


@pytest.mark.parametrize(
    "target_status",
    [ShopStatus.ACTIVE.value, ShopStatus.SUSPENDED.value],
)
def test_post_shop_select_sets_active_or_suspended_membership_and_prg_to_workspace(
    m2_test_database: Engine,
    db_session: Session,
    target_status: str,
) -> None:
    now = datetime(2026, 7, 27, 17, 15, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    target_shop = add_shop(db_session, name="Target Shop", status=target_status)
    add_staff(db_session, shop=target_shop, user=user, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/select",
        data={"csrf_token": csrf_value(created), "shop_id": str(target_shop.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop"
    assert_shop_security_headers(response)
    assert fetch_active_shop_id(db_session, created) == target_shop.id


def test_post_shop_select_can_leave_suspended_shop_for_another_shop(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 17, 20, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    suspended_shop = add_shop(
        db_session,
        name="Old Suspended",
        status=ShopStatus.SUSPENDED.value,
    )
    next_shop = add_shop(db_session, name="Next Active")
    add_staff(db_session, shop=suspended_shop, user=user, role=ShopRole.OWNER.value)
    add_staff(db_session, shop=next_shop, user=user, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        active_shop=suspended_shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/select",
        data={"csrf_token": csrf_value(created), "shop_id": str(next_shop.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop"
    assert_shop_security_headers(response)
    assert fetch_active_shop_id(db_session, created) == next_shop.id


def test_post_shop_select_rejects_foreign_shop_without_existence_leak(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 17, 25, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    current_user = add_user(db_session)
    foreign_user = add_user(db_session)
    current_shop = add_shop(db_session, name="Current Shop")
    foreign_shop = add_shop(db_session, name="Foreign Shop")
    add_staff(
        db_session, shop=current_shop, user=current_user, role=ShopRole.OWNER.value
    )
    add_staff(
        db_session, shop=foreign_shop, user=foreign_user, role=ShopRole.OWNER.value
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        current_user,
        now,
        settings,
        active_shop=current_shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/select",
        data={"csrf_token": csrf_value(created), "shop_id": str(foreign_shop.id)},
        follow_redirects=False,
    )

    assert_forbidden_select_response_is_safe(
        response,
        hidden_values=(
            str(foreign_shop.id),
            foreign_shop.name,
            foreign_shop.phone,
            str(foreign_user.id),
            foreign_user.phone,
        ),
    )
    assert fetch_active_shop_id(db_session, created) == current_shop.id


def test_post_shop_select_rejects_revoked_membership_without_existence_leak(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 17, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    active_shop = add_shop(db_session, name="Active Shop")
    revoked_shop = add_shop(db_session, name="Revoked Shop")
    add_staff(db_session, shop=active_shop, user=user, role=ShopRole.OWNER.value)
    add_staff(
        db_session,
        shop=revoked_shop,
        user=user,
        role=ShopRole.MANAGER.value,
        is_active=False,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        active_shop=active_shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/select",
        data={"csrf_token": csrf_value(created), "shop_id": str(revoked_shop.id)},
        follow_redirects=False,
    )

    assert_forbidden_select_response_is_safe(
        response,
        hidden_values=(
            str(revoked_shop.id),
            revoked_shop.name,
            revoked_shop.phone,
            str(user.id),
            user.phone,
        ),
    )
    assert fetch_active_shop_id(db_session, created) == active_shop.id


def test_post_shop_select_without_csrf_does_not_mutate_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 17, 35, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    shop = add_shop(db_session)
    add_staff(db_session, shop=shop, user=user, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/select",
        data={"shop_id": str(shop.id)},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_shop_security_headers(response)
    assert fetch_active_shop_id(db_session, created) is None
