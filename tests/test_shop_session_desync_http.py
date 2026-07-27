from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-session-desync"
NOW = datetime(2026, 7, 27, 21, 0, tzinfo=UTC)


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


def make_client(engine: Engine) -> tuple[TestClient, Settings]:
    settings = make_settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
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
    name: str = "Desync Shop",
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


def authenticate(
    db_session: Session,
    *,
    user: User,
    shop: Shop,
    settings: Settings,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-shop-session-desync",
        NOW,
        settings=settings,
    )
    created.session.active_shop_id = shop.id
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
        domain="testserver.local",
        path="/",
    )


def csrf_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def fetch_auth_session(
    db_session: Session,
    created: CreatedSession,
) -> AuthSession:
    db_session.expire_all()
    auth_session = db_session.get(AuthSession, created.session.id)
    assert auth_session is not None
    return auth_session


def fetch_staff(db_session: Session, staff_id: UUID) -> ShopStaff:
    db_session.expire_all()
    staff = db_session.get(ShopStaff, staff_id)
    assert staff is not None
    return staff


def has_active_membership(
    db_session: Session,
    *,
    shop: Shop,
    user: User,
) -> bool:
    db_session.expire_all()
    return (
        db_session.scalar(
            select(ShopStaff).where(
                ShopStaff.shop_id == shop.id,
                ShopStaff.user_id == user.id,
                ShopStaff.is_active.is_(True),
            )
        )
        is not None
    )


def post_add_staff(
    client: TestClient,
    *,
    created: CreatedSession,
    target: User,
):
    return client.post(
        "/shop/staff/add",
        data={
            "csrf_token": csrf_value(created),
            "phone": target.phone,
            "role": ShopRole.CASHIER.value,
        },
        follow_redirects=False,
    )


def test_revoked_membership_takes_effect_on_the_next_request(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    owner = add_user(db_session)
    current_user = add_user(db_session)
    shop = add_shop(db_session)
    add_staff(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    current_staff = add_staff(
        db_session,
        shop=shop,
        user=current_user,
        role=ShopRole.CASHIER.value,
    )
    db_session.commit()
    created = authenticate(
        db_session,
        user=current_user,
        shop=shop,
        settings=settings,
    )
    set_session_cookie(client, settings, created)

    before_revoke = client.get("/shop", follow_redirects=False)
    assert before_revoke.status_code == 200
    assert shop.name in before_revoke.text

    current_staff = fetch_staff(db_session, current_staff.id)
    current_staff.is_active = False
    current_staff.revoked_at = NOW
    db_session.commit()

    after_revoke = client.get("/shop", follow_redirects=False)
    assert after_revoke.status_code == 303
    assert after_revoke.headers["location"] == "/shop/select"
    assert fetch_auth_session(db_session, created).active_shop_id is None


def test_owner_demotion_is_forbidden_on_the_next_owner_request(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    current_owner = add_user(db_session)
    other_owner = add_user(db_session)
    first_target = add_user(db_session)
    second_target = add_user(db_session)
    shop = add_shop(db_session)
    current_staff = add_staff(
        db_session,
        shop=shop,
        user=current_owner,
        role=ShopRole.OWNER.value,
    )
    add_staff(db_session, shop=shop, user=other_owner, role=ShopRole.OWNER.value)
    db_session.commit()
    created = authenticate(
        db_session,
        user=current_owner,
        shop=shop,
        settings=settings,
    )
    set_session_cookie(client, settings, created)

    owner_request = post_add_staff(
        client,
        created=created,
        target=first_target,
    )
    assert owner_request.status_code == 303
    assert owner_request.headers["location"] == "/shop/staff?notice=staff_added"

    current_staff = fetch_staff(db_session, current_staff.id)
    current_staff.role = ShopRole.MANAGER.value
    db_session.commit()

    demoted_request = post_add_staff(
        client,
        created=created,
        target=second_target,
    )
    assert demoted_request.status_code == 403
    assert demoted_request.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    assert not has_active_membership(
        db_session,
        shop=shop,
        user=second_target,
    )


def test_manager_promotion_is_authorized_on_the_next_owner_request(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    owner = add_user(db_session)
    current_manager = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session)
    add_staff(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    manager_staff = add_staff(
        db_session,
        shop=shop,
        user=current_manager,
        role=ShopRole.MANAGER.value,
    )
    db_session.commit()
    created = authenticate(
        db_session,
        user=current_manager,
        shop=shop,
        settings=settings,
    )
    set_session_cookie(client, settings, created)

    manager_request = post_add_staff(client, created=created, target=target)
    assert manager_request.status_code == 403
    assert manager_request.headers["x-error-code"] == ErrorCode.FORBIDDEN.value

    manager_staff = fetch_staff(db_session, manager_staff.id)
    manager_staff.role = ShopRole.OWNER.value
    db_session.commit()

    promoted_request = post_add_staff(client, created=created, target=target)
    assert promoted_request.status_code == 303
    assert promoted_request.headers["location"] == "/shop/staff?notice=staff_added"
    assert has_active_membership(db_session, shop=shop, user=target)


def test_shop_suspension_is_read_only_on_the_next_requests(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    owner = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session)
    add_staff(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    db_session.commit()
    created = authenticate(
        db_session,
        user=owner,
        shop=shop,
        settings=settings,
    )
    set_session_cookie(client, settings, created)

    active_page = client.get("/shop", follow_redirects=False)
    assert active_page.status_code == 200
    assert "faqat ko'rish rejimi" not in unescape(active_page.text)

    db_session.expire_all()
    stored_shop = db_session.get(Shop, shop.id)
    assert stored_shop is not None
    stored_shop.status = ShopStatus.SUSPENDED.value
    db_session.commit()

    suspended_page = client.get("/shop", follow_redirects=False)
    assert suspended_page.status_code == 200
    assert "faqat ko'rish rejimi" in unescape(suspended_page.text)

    blocked_write = post_add_staff(client, created=created, target=target)
    assert blocked_write.status_code == 303
    assert blocked_write.headers["location"] == "/shop/staff?error=shop_suspended"
    assert not has_active_membership(db_session, shop=shop, user=target)


def test_shop_reactivation_allows_the_next_business_request(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    owner = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session, status=ShopStatus.SUSPENDED.value)
    add_staff(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    db_session.commit()
    created = authenticate(
        db_session,
        user=owner,
        shop=shop,
        settings=settings,
    )
    set_session_cookie(client, settings, created)

    suspended_request = post_add_staff(client, created=created, target=target)
    assert suspended_request.status_code == 303
    assert suspended_request.headers["location"] == ("/shop/staff?error=shop_suspended")

    db_session.expire_all()
    stored_shop = db_session.get(Shop, shop.id)
    assert stored_shop is not None
    stored_shop.status = ShopStatus.ACTIVE.value
    db_session.commit()

    reactivated_request = post_add_staff(client, created=created, target=target)
    assert reactivated_request.status_code == 303
    assert reactivated_request.headers["location"] == ("/shop/staff?notice=staff_added")
    assert has_active_membership(db_session, shop=shop, user=target)


@pytest.mark.parametrize("stale_context", ["foreign", "deleted_membership"])
def test_foreign_or_deleted_active_context_becomes_safely_unselected(
    m2_test_database: Engine,
    db_session: Session,
    stale_context: str,
) -> None:
    client, settings = make_client(m2_test_database)
    current_user = add_user(db_session)
    owner_a = add_user(db_session)
    owner_b = add_user(db_session)
    shop_a = add_shop(db_session, name="Current Desync Shop")
    shop_b = add_shop(db_session, name="Foreign Desync Shop")
    add_staff(db_session, shop=shop_a, user=owner_a, role=ShopRole.OWNER.value)
    current_staff = add_staff(
        db_session,
        shop=shop_a,
        user=current_user,
        role=ShopRole.CASHIER.value,
    )
    add_staff(db_session, shop=shop_b, user=owner_b, role=ShopRole.OWNER.value)
    db_session.commit()
    created = authenticate(
        db_session,
        user=current_user,
        shop=shop_a,
        settings=settings,
    )
    set_session_cookie(client, settings, created)

    current_page = client.get("/shop", follow_redirects=False)
    assert current_page.status_code == 200
    assert shop_a.name in current_page.text

    if stale_context == "foreign":
        auth_session = fetch_auth_session(db_session, created)
        auth_session.active_shop_id = shop_b.id
    else:
        current_staff = fetch_staff(db_session, current_staff.id)
        db_session.delete(current_staff)
    db_session.commit()

    stale_request = client.get("/shop", follow_redirects=False)
    assert stale_request.status_code == 303
    assert stale_request.headers["location"] == "/shop/select"
    assert shop_b.name not in stale_request.text
    assert fetch_auth_session(db_session, created).active_shop_id is None
