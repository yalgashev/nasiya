from collections.abc import Generator
from datetime import UTC, datetime
from html import unescape
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-staff"


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
    name: str = "Staff Shop",
    status: str = ShopStatus.ACTIVE.value,
) -> Shop:
    shop = Shop(name=name, phone=unique_phone(), status=status)
    db_session.add(shop)
    db_session.flush()
    return shop


def add_staff_row(
    db_session: Session,
    *,
    shop: Shop,
    user: User,
    role: str,
    is_active: bool = True,
    created_at: datetime | None = None,
) -> ShopStaff:
    values = {
        "shop_id": shop.id,
        "user_id": user.id,
        "role": role,
        "is_active": is_active,
        "revoked_at": None if is_active else datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
    }
    if created_at is not None:
        values["created_at"] = created_at
        values["updated_at"] = created_at

    staff = ShopStaff(**values)
    db_session.add(staff)
    db_session.flush()
    return staff


def commit_authenticated_session(
    db_session: Session,
    user: User,
    now: datetime,
    settings: Settings,
    *,
    active_shop: Shop,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-shop-staff",
        now,
        settings=settings,
    )
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


def assert_shop_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def fetch_staff_for_user(db_session: Session, *, shop: Shop, user: User) -> ShopStaff:
    db_session.expire_all()
    staff = db_session.scalar(
        select(ShopStaff).where(
            ShopStaff.shop_id == shop.id,
            ShopStaff.user_id == user.id,
        )
    )
    assert staff is not None
    return staff


def assert_forbidden_response_is_safe(response, hidden_values: tuple[str, ...]) -> None:
    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    assert_shop_security_headers(response)
    assert "Bu amal uchun ruxsat yo'q." in response.text
    for hidden_value in hidden_values:
        assert hidden_value not in response.text


def assert_safe_staff_html(response, hidden_values: tuple[str, ...]) -> str:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert_shop_security_headers(response)
    html = unescape(response.text)
    for hidden_value in hidden_values:
        assert hidden_value not in html
    assert "Traceback" not in html
    assert "IntegrityError" not in html
    return html


@pytest.mark.parametrize(
    ("current_role", "can_manage"),
    [
        (ShopRole.OWNER.value, True),
        (ShopRole.MANAGER.value, False),
        (ShopRole.CASHIER.value, False),
    ],
)
def test_get_staff_page_renders_active_staff_for_all_roles_with_masked_phones(
    m2_test_database: Engine,
    db_session: Session,
    current_role: str,
    can_manage: bool,
) -> None:
    now = datetime(2026, 7, 27, 18, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    current_user = add_user(db_session)
    owner_user = (
        current_user if current_role == ShopRole.OWNER.value else add_user(db_session)
    )
    manager_user = (
        current_user if current_role == ShopRole.MANAGER.value else add_user(db_session)
    )
    cashier_user = (
        current_user if current_role == ShopRole.CASHIER.value else add_user(db_session)
    )
    shop = add_shop(db_session)
    add_staff_row(
        db_session,
        shop=shop,
        user=owner_user,
        role=ShopRole.OWNER.value,
        created_at=now,
    )
    add_staff_row(
        db_session,
        shop=shop,
        user=manager_user,
        role=ShopRole.MANAGER.value,
        created_at=now,
    )
    add_staff_row(
        db_session,
        shop=shop,
        user=cashier_user,
        role=ShopRole.CASHIER.value,
        created_at=now,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        current_user,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop/staff")

    html = assert_safe_staff_html(
        response,
        hidden_values=(
            owner_user.phone,
            manager_user.phone,
            cashier_user.phone,
            str(owner_user.id),
            str(manager_user.id),
            str(cashier_user.id),
            shop.phone,
        ),
    )
    assert shop.name in html
    assert mask_phone_for_display(owner_user.phone) in html
    assert mask_phone_for_display(manager_user.phone) in html
    assert mask_phone_for_display(cashier_user.phone) in html
    assert "egasi" in html
    assert "menejer" in html
    assert "kassir" in html
    assert "Qo'shilgan sana" in html
    assert "2026-07-27 18:00 UTC" in html
    if can_manage:
        assert 'action="/shop/staff/add"' in html
        assert "Qo'shish" in html
        assert "Rolni o'zgartirish" in html
        assert "Bo'shatish" in html
    else:
        assert 'action="/shop/staff/add"' not in html
        assert "Qo'shish" not in html
        assert "Rolni o'zgartirish" not in html
        assert "Bo'shatish" not in html


def test_owner_can_add_staff_with_prg_and_safe_notice(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 18, 5, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session)
    add_staff_row(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/staff/add",
        data={
            "csrf_token": csrf_value(created),
            "phone": target.phone,
            "role": ShopRole.CASHIER.value,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?notice=staff_added"
    assert_shop_security_headers(response)
    staff = fetch_staff_for_user(db_session, shop=shop, user=target)
    assert staff.is_active is True
    assert staff.role == ShopRole.CASHIER.value

    page = client.get(response.headers["location"])
    html = assert_safe_staff_html(page, hidden_values=(target.phone, str(target.id)))
    assert "Xodim saqlandi." in html
    assert mask_phone_for_display(target.phone) in html


def test_owner_add_unknown_phone_is_enumeration_safe(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 18, 10, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    shop = add_shop(db_session)
    add_staff_row(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)
    unknown_phone = "+998901112233"

    response = client.post(
        "/shop/staff/add",
        data={
            "csrf_token": csrf_value(created),
            "phone": unknown_phone,
            "role": ShopRole.CASHIER.value,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?error=add_failed"
    page = client.get(response.headers["location"])
    html = assert_safe_staff_html(page, hidden_values=(unknown_phone,))
    assert "Xodimni qo'shib bo'lmadi." in html


def test_owner_can_change_staff_role_with_prg(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 18, 15, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    cashier = add_user(db_session)
    shop = add_shop(db_session)
    add_staff_row(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    cashier_staff = add_staff_row(
        db_session,
        shop=shop,
        user=cashier,
        role=ShopRole.CASHIER.value,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        f"/shop/staff/{cashier_staff.id}/role",
        data={
            "csrf_token": csrf_value(created),
            "new_role": ShopRole.MANAGER.value,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?notice=role_updated"
    staff = fetch_staff_for_user(db_session, shop=shop, user=cashier)
    assert staff.role == ShopRole.MANAGER.value


def test_owner_can_revoke_staff_with_prg(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 18, 20, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    cashier = add_user(db_session)
    shop = add_shop(db_session)
    add_staff_row(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    cashier_staff = add_staff_row(
        db_session,
        shop=shop,
        user=cashier,
        role=ShopRole.CASHIER.value,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        f"/shop/staff/{cashier_staff.id}/revoke",
        data={"csrf_token": csrf_value(created)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?notice=staff_revoked"
    staff = fetch_staff_for_user(db_session, shop=shop, user=cashier)
    assert staff.is_active is False
    assert staff.revoked_at is not None

    page = client.get(response.headers["location"])
    html = assert_safe_staff_html(page, hidden_values=(cashier.phone, str(cashier.id)))
    assert "Xodim huquqi yopildi." in html
    assert mask_phone_for_display(cashier.phone) not in html


@pytest.mark.parametrize(
    "current_role", [ShopRole.MANAGER.value, ShopRole.CASHIER.value]
)
@pytest.mark.parametrize("action", ["add", "role", "revoke"])
def test_manager_and_cashier_post_staff_mutations_are_forbidden(
    m2_test_database: Engine,
    db_session: Session,
    current_role: str,
    action: str,
) -> None:
    now = datetime(2026, 7, 27, 18, 25, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    current_user = add_user(db_session)
    owner = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session)
    add_staff_row(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    current_staff = add_staff_row(
        db_session,
        shop=shop,
        user=current_user,
        role=current_role,
    )
    target_staff = add_staff_row(
        db_session,
        shop=shop,
        user=target,
        role=ShopRole.CASHIER.value,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        current_user,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    if action == "add":
        response = client.post(
            "/shop/staff/add",
            data={
                "csrf_token": csrf_value(created),
                "phone": target.phone,
                "role": ShopRole.MANAGER.value,
            },
            follow_redirects=False,
        )
    elif action == "role":
        response = client.post(
            f"/shop/staff/{target_staff.id}/role",
            data={
                "csrf_token": csrf_value(created),
                "new_role": ShopRole.MANAGER.value,
            },
            follow_redirects=False,
        )
    else:
        response = client.post(
            f"/shop/staff/{target_staff.id}/revoke",
            data={"csrf_token": csrf_value(created)},
            follow_redirects=False,
        )

    assert_forbidden_response_is_safe(
        response,
        hidden_values=(
            current_user.phone,
            target.phone,
            str(current_user.id),
            str(current_staff.id),
            str(target_staff.id),
        ),
    )


@pytest.mark.parametrize("action", ["add", "role", "revoke"])
def test_suspended_shop_blocks_staff_business_mutations_with_safe_message(
    m2_test_database: Engine,
    db_session: Session,
    action: str,
) -> None:
    now = datetime(2026, 7, 27, 18, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session, status=ShopStatus.SUSPENDED.value)
    add_staff_row(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    target_staff = add_staff_row(
        db_session,
        shop=shop,
        user=target,
        role=ShopRole.CASHIER.value,
    )
    new_user = add_user(db_session)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    if action == "add":
        response = client.post(
            "/shop/staff/add",
            data={
                "csrf_token": csrf_value(created),
                "phone": new_user.phone,
                "role": ShopRole.MANAGER.value,
            },
            follow_redirects=False,
        )
    elif action == "role":
        response = client.post(
            f"/shop/staff/{target_staff.id}/role",
            data={
                "csrf_token": csrf_value(created),
                "new_role": ShopRole.MANAGER.value,
            },
            follow_redirects=False,
        )
    else:
        response = client.post(
            f"/shop/staff/{target_staff.id}/revoke",
            data={"csrf_token": csrf_value(created)},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?error=shop_suspended"
    page = client.get(response.headers["location"])
    html = assert_safe_staff_html(
        page,
        hidden_values=(new_user.phone, target.phone, str(new_user.id), str(target.id)),
    )
    assert "Do'kon to'xtatilgan. O'zgartirish kiritib bo'lmaydi." in html
    assert "faqat ko'rish rejimi" in html
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(ShopStaff).where(
                ShopStaff.shop_id == shop.id,
                ShopStaff.user_id == new_user.id,
            )
        )
        is None
    )
    refreshed_target = db_session.get(ShopStaff, target_staff.id)
    assert refreshed_target is not None
    assert refreshed_target.role == ShopRole.CASHIER.value
    assert refreshed_target.is_active is True


@pytest.mark.parametrize("action", ["role", "revoke"])
def test_last_owner_is_blocked_with_safe_message(
    m2_test_database: Engine,
    db_session: Session,
    action: str,
) -> None:
    now = datetime(2026, 7, 27, 18, 35, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    shop = add_shop(db_session)
    owner_staff = add_staff_row(
        db_session,
        shop=shop,
        user=owner,
        role=ShopRole.OWNER.value,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    if action == "role":
        response = client.post(
            f"/shop/staff/{owner_staff.id}/role",
            data={
                "csrf_token": csrf_value(created),
                "new_role": ShopRole.MANAGER.value,
            },
            follow_redirects=False,
        )
    else:
        response = client.post(
            f"/shop/staff/{owner_staff.id}/revoke",
            data={"csrf_token": csrf_value(created)},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?error=last_owner"
    page = client.get(response.headers["location"])
    html = assert_safe_staff_html(page, hidden_values=(owner.phone, str(owner.id)))
    assert "Oxirgi egani olib tashlab yoki rolini pasaytirib bo'lmaydi." in html
    staff = fetch_staff_for_user(db_session, shop=shop, user=owner)
    assert staff.role == ShopRole.OWNER.value
    assert staff.is_active is True


@pytest.mark.parametrize("action", ["role", "revoke"])
def test_foreign_staff_uuid_is_safe_and_does_not_mutate(
    m2_test_database: Engine,
    db_session: Session,
    action: str,
) -> None:
    now = datetime(2026, 7, 27, 18, 40, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner_a = add_user(db_session)
    owner_b = add_user(db_session)
    shop_a = add_shop(db_session, name="Shop A")
    shop_b = add_shop(db_session, name="Shop B")
    add_staff_row(db_session, shop=shop_a, user=owner_a, role=ShopRole.OWNER.value)
    foreign_staff = add_staff_row(
        db_session,
        shop=shop_b,
        user=owner_b,
        role=ShopRole.OWNER.value,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner_a,
        now,
        settings,
        active_shop=shop_a,
    )
    set_client_session_cookie(client, settings, created)

    if action == "role":
        response = client.post(
            f"/shop/staff/{foreign_staff.id}/role",
            data={
                "csrf_token": csrf_value(created),
                "new_role": ShopRole.MANAGER.value,
            },
            follow_redirects=False,
        )
    else:
        response = client.post(
            f"/shop/staff/{foreign_staff.id}/revoke",
            data={"csrf_token": csrf_value(created)},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?error=staff_action_failed"
    page = client.get(response.headers["location"])
    html = assert_safe_staff_html(
        page,
        hidden_values=(
            str(foreign_staff.id),
            str(shop_b.id),
            shop_b.name,
            shop_b.phone,
            str(owner_b.id),
            owner_b.phone,
        ),
    )
    assert "Xodim bo'yicha amal bajarilmadi." in html
    db_session.expire_all()
    refreshed_foreign = db_session.get(ShopStaff, foreign_staff.id)
    assert refreshed_foreign is not None
    assert refreshed_foreign.role == ShopRole.OWNER.value
    assert refreshed_foreign.is_active is True


def test_add_staff_without_csrf_is_rejected_without_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 18, 45, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session)
    add_staff_row(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        owner,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/staff/add",
        data={"phone": target.phone, "role": ShopRole.CASHIER.value},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_shop_security_headers(response)
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(ShopStaff).where(
                ShopStaff.shop_id == shop.id,
                ShopStaff.user_id == target.id,
            )
        )
        is None
    )
