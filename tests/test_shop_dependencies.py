from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings
from app.shop.context import CurrentShopContext
from app.shop.dependencies import require_shop_owner, require_shop_staff
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-dependencies"


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

    @application.get("/_test/shop/staff")
    def read_staff_context(
        context: Annotated[CurrentShopContext, Depends(require_shop_staff)],
    ) -> dict[str, str]:
        assert context.shop is not None
        assert context.staff_id is not None
        assert context.role is not None
        assert context.status is not None
        return {
            "shop_id": str(context.shop.id),
            "shop_name": context.shop.name,
            "staff_id": str(context.staff_id),
            "role": context.role.value,
            "status": context.status.value,
        }

    @application.get("/_test/shop/owner")
    def read_owner_context(
        context: Annotated[CurrentShopContext, Depends(require_shop_owner)],
    ) -> dict[str, str]:
        assert context.shop is not None
        assert context.staff_id is not None
        assert context.role is not None
        assert context.status is not None
        return {
            "shop_id": str(context.shop.id),
            "shop_name": context.shop.name,
            "staff_id": str(context.staff_id),
            "role": context.role.value,
            "status": context.status.value,
        }

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
    name: str = "Dependency Shop",
    status: str = ShopStatus.ACTIVE.value,
) -> Shop:
    shop = Shop(name=name, phone=unique_phone(), status=status)
    db_session.add(shop)
    db_session.flush()
    return shop


def add_staff(
    db_session: Session,
    shop: Shop,
    user: User,
    *,
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
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-shop-dependencies",
        now,
        settings=settings,
    )
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
    )


def commit_user_with_membership(
    db_session: Session,
    *,
    role: str,
    status: str = ShopStatus.ACTIVE.value,
) -> tuple[User, Shop, ShopStaff]:
    user = add_user(db_session)
    shop = add_shop(db_session, status=status)
    staff = add_staff(db_session, shop, user, role=role)
    db_session.commit()
    return user, shop, staff


def assert_forbidden_body_is_safe(
    response,
    *,
    user: User,
    shop: Shop,
    staff: ShopStaff,
) -> None:
    body = response.text

    assert response.status_code == 403
    assert response.headers["x-error-code"] == "FORBIDDEN"
    assert "FORBIDDEN" in body
    assert shop.name not in body
    assert str(shop.id) not in body
    assert str(user.id) not in body
    assert str(staff.id) not in body
    assert user.phone not in body


def test_anonymous_staff_dependency_redirects_to_login(
    m2_test_database: Engine,
) -> None:
    now = datetime(2026, 7, 27, 15, 0, tzinfo=UTC)
    client, _settings = make_client(m2_test_database, now)

    response = client.get("/_test/shop/staff", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


def test_authenticated_user_without_membership_redirects_to_shop_select(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 15, 5, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/_test/shop/staff", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/select"


@pytest.mark.parametrize(
    "role",
    [
        ShopRole.OWNER.value,
        ShopRole.MANAGER.value,
        ShopRole.CASHIER.value,
    ],
)
def test_staff_dependency_returns_read_context_for_active_membership_roles(
    m2_test_database: Engine,
    db_session: Session,
    role: str,
) -> None:
    now = datetime(2026, 7, 27, 15, 10, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user, shop, staff = commit_user_with_membership(db_session, role=role)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/_test/shop/staff")

    assert response.status_code == 200
    assert response.json() == {
        "shop_id": str(shop.id),
        "shop_name": shop.name,
        "staff_id": str(staff.id),
        "role": role,
        "status": ShopStatus.ACTIVE.value,
    }


def test_owner_dependency_allows_owner(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 15, 15, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user, shop, staff = commit_user_with_membership(
        db_session,
        role=ShopRole.OWNER.value,
    )
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/_test/shop/owner")

    assert response.status_code == 200
    assert response.json() == {
        "shop_id": str(shop.id),
        "shop_name": shop.name,
        "staff_id": str(staff.id),
        "role": ShopRole.OWNER.value,
        "status": ShopStatus.ACTIVE.value,
    }


@pytest.mark.parametrize("role", [ShopRole.MANAGER.value, ShopRole.CASHIER.value])
def test_owner_dependency_rejects_manager_and_cashier_with_safe_body(
    m2_test_database: Engine,
    db_session: Session,
    role: str,
) -> None:
    now = datetime(2026, 7, 27, 15, 20, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user, shop, staff = commit_user_with_membership(db_session, role=role)
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/_test/shop/owner")

    assert_forbidden_body_is_safe(response, user=user, shop=shop, staff=staff)


def test_suspended_owner_gets_read_context(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 15, 25, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user, shop, staff = commit_user_with_membership(
        db_session,
        role=ShopRole.OWNER.value,
        status=ShopStatus.SUSPENDED.value,
    )
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.get("/_test/shop/owner")

    assert response.status_code == 200
    assert response.json() == {
        "shop_id": str(shop.id),
        "shop_name": shop.name,
        "staff_id": str(staff.id),
        "role": ShopRole.OWNER.value,
        "status": ShopStatus.SUSPENDED.value,
    }
