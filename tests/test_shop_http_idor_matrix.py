from collections.abc import Generator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from html import unescape
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
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
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-http-idor"


class IdorVector(StrEnum):
    FOREIGN_ACTIVE_SHOP_SESSION = "foreign_active_shop_session"
    FOREIGN_SHOP_UUID = "foreign_shop_uuid"
    FOREIGN_STAFF_UUID = "foreign_staff_uuid"
    FOREIGN_SHOP_CUSTOMER_UUID = "foreign_shop_customer_uuid"
    FOREIGN_DEBT_UUID = "foreign_debt_uuid"
    FOREIGN_PAYMENT_UUID = "foreign_payment_uuid"
    FOREIGN_DISCLOSURE_VIEW_UUID = "foreign_disclosure_view_uuid"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class RouteIdorCase:
    method: str
    path_format: str
    vector: IdorVector
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path_format)


ROUTE_IDOR_CASES = (
    RouteIdorCase(
        "GET",
        "/shop",
        IdorVector.FOREIGN_ACTIVE_SHOP_SESSION,
        "uses active_shop_id from the current session",
    ),
    RouteIdorCase(
        "GET",
        "/shop/select",
        IdorVector.NOT_APPLICABLE,
        "no route resource ID; lists only the authenticated user's memberships",
    ),
    RouteIdorCase(
        "POST",
        "/shop/select",
        IdorVector.FOREIGN_SHOP_UUID,
        "posted shop_id must belong to the authenticated user's active memberships",
    ),
    RouteIdorCase(
        "GET",
        "/shop/staff",
        IdorVector.FOREIGN_ACTIVE_SHOP_SESSION,
        "uses active_shop_id from the current session",
    ),
    RouteIdorCase(
        "POST",
        "/shop/staff/add",
        IdorVector.NOT_APPLICABLE,
        "classic resource-ID IDOR does not apply; add is by global phone",
    ),
    RouteIdorCase(
        "POST",
        "/shop/staff/{staff_id}/role",
        IdorVector.FOREIGN_STAFF_UUID,
        "path staff_id must be scoped to the active shop",
    ),
    RouteIdorCase(
        "POST",
        "/shop/staff/{staff_id}/revoke",
        IdorVector.FOREIGN_STAFF_UUID,
        "path staff_id must be scoped to the active shop",
    ),
    RouteIdorCase(
        "GET",
        "/shop/customers",
        IdorVector.FOREIGN_ACTIVE_SHOP_SESSION,
        "uses the current session shop and a live membership",
    ),
    RouteIdorCase(
        "POST",
        "/shop/customers/link",
        IdorVector.NOT_APPLICABLE,
        "target is resolved server-side from exact phone, not a resource ID",
    ),
    RouteIdorCase(
        "GET",
        "/shop/settings/credit",
        IdorVector.FOREIGN_ACTIVE_SHOP_SESSION,
        "uses the current session shop and a live membership",
    ),
    RouteIdorCase(
        "POST",
        "/shop/settings/credit",
        IdorVector.FOREIGN_ACTIVE_SHOP_SESSION,
        "uses detached authority derived from the current session shop",
    ),
    RouteIdorCase(
        "POST",
        "/shop/customers/{shop_customer_id}/policy",
        IdorVector.FOREIGN_SHOP_CUSTOMER_UUID,
        "path locator is resolved only within detached current-shop authority",
    ),
    RouteIdorCase(
        "GET",
        "/shop/customers/{shop_customer_id}/debts",
        IdorVector.FOREIGN_SHOP_CUSTOMER_UUID,
        "shop_customer_id is resolved only inside the current shop",
    ),
    RouteIdorCase(
        "GET",
        "/shop/customers/{shop_customer_id}/debts/new",
        IdorVector.FOREIGN_SHOP_CUSTOMER_UUID,
        "create form resolves shop_customer_id only inside the current shop",
    ),
    RouteIdorCase(
        "POST",
        "/shop/customers/{shop_customer_id}/debts",
        IdorVector.FOREIGN_SHOP_CUSTOMER_UUID,
        "creation rechecks shop_customer_id under detached current-shop authority",
    ),
    RouteIdorCase(
        "GET",
        "/shop/debts/{debt_id}",
        IdorVector.FOREIGN_DEBT_UUID,
        "debt_id is joined through a ShopCustomer in the current shop",
    ),
    RouteIdorCase(
        "POST",
        "/shop/debts/{debt_id}/cancel",
        IdorVector.FOREIGN_DEBT_UUID,
        "cancel locks and rechecks debt_id through the current shop chain",
    ),
    RouteIdorCase(
        "GET",
        "/shop/debts/{debt_id}/payments",
        IdorVector.FOREIGN_DEBT_UUID,
        "payment history joins debt_id through the current shop chain",
    ),
    RouteIdorCase(
        "GET",
        "/shop/debts/{debt_id}/payments/new",
        IdorVector.FOREIGN_DEBT_UUID,
        "payment form resolves debt_id only within current-shop authority",
    ),
    RouteIdorCase(
        "POST",
        "/shop/debts/{debt_id}/payments",
        IdorVector.FOREIGN_DEBT_UUID,
        "payment mutation locks and rechecks the full current-shop chain",
    ),
    RouteIdorCase(
        "GET",
        "/shop/payments/{payment_id}",
        IdorVector.FOREIGN_PAYMENT_UUID,
        "receipt joins payment_id through Debt and ShopCustomer authority",
    ),
    RouteIdorCase(
        "POST",
        "/shop/customers/{shop_customer_id}/risk-band-disclosures",
        IdorVector.FOREIGN_SHOP_CUSTOMER_UUID,
        "snapshot creation rechecks the complete current-Shop target chain",
    ),
    RouteIdorCase(
        "GET",
        "/shop/risk-band-disclosures/{disclosure_view_id}",
        IdorVector.FOREIGN_DISCLOSURE_VIEW_UUID,
        "opaque locator is scoped to the current actor, Shop, and stored chain",
    ),
)


@dataclass(frozen=True)
class CrossShopRows:
    user_a: User
    user_b: User
    shop_a: Shop
    shop_b: Shop
    staff_a: ShopStaff
    staff_b: ShopStaff

    @property
    def hidden_b_values(self) -> tuple[str, ...]:
        return (
            self.shop_b.name,
            self.shop_b.phone,
            str(self.shop_b.id),
            str(self.staff_b.id),
            str(self.user_b.id),
            self.user_b.phone,
        )


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


def add_shop(db_session: Session, *, name: str) -> Shop:
    shop = Shop(name=name, phone=unique_phone(), status=ShopStatus.ACTIVE.value)
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


def create_cross_shop_rows(db_session: Session) -> CrossShopRows:
    user_a = add_user(db_session)
    user_b = add_user(db_session)
    shop_a = add_shop(db_session, name="A IDOR Shop")
    shop_b = add_shop(db_session, name="B Hidden Shop")
    staff_a = add_staff(
        db_session,
        shop=shop_a,
        user=user_a,
        role=ShopRole.OWNER.value,
    )
    staff_b = add_staff(
        db_session,
        shop=shop_b,
        user=user_b,
        role=ShopRole.OWNER.value,
    )
    db_session.commit()
    return CrossShopRows(
        user_a=user_a,
        user_b=user_b,
        shop_a=shop_a,
        shop_b=shop_b,
        staff_a=staff_a,
        staff_b=staff_b,
    )


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
        "pytest-shop-http-idor",
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


def iter_application_api_routes(application: FastAPI) -> Iterator[APIRoute]:
    yield from iter_api_routes(application.routes)


def collect_shop_route_keys(application: FastAPI) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for route in iter_application_api_routes(application):
        if route.path_format.startswith("/shop"):
            for method in route.methods or set():
                if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    keys.add((method, route.path_format))
    return keys


def assert_shop_security_headers(response) -> None:
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def assert_no_hidden_b_values(response_text: str, rows: CrossShopRows) -> None:
    for hidden_value in rows.hidden_b_values:
        assert hidden_value not in response_text


def fetch_active_shop_id(db_session: Session, created: CreatedSession) -> UUID | None:
    stored_session = db_session.get(AuthSession, created.session.id)
    assert stored_session is not None
    db_session.refresh(stored_session)
    return stored_session.active_shop_id


def fetch_staff(db_session: Session, staff_id: UUID) -> ShopStaff:
    db_session.expire_all()
    staff = db_session.get(ShopStaff, staff_id)
    assert staff is not None
    return staff


def count_staff_for_user(db_session: Session, *, shop: Shop, user: User) -> int:
    db_session.expire_all()
    return (
        db_session.query(ShopStaff)
        .filter(ShopStaff.shop_id == shop.id, ShopStaff.user_id == user.id)
        .count()
    )


def test_all_shop_routes_have_explicit_http_idor_case_or_na_reason() -> None:
    application = create_app()

    actual = collect_shop_route_keys(application)
    expected = {case.key for case in ROUTE_IDOR_CASES}

    assert actual == expected
    assert all(case.reason.strip() for case in ROUTE_IDOR_CASES)
    assert {
        case.key
        for case in ROUTE_IDOR_CASES
        if case.vector is IdorVector.NOT_APPLICABLE
    } == {
        ("GET", "/shop/select"),
        ("POST", "/shop/staff/add"),
        ("POST", "/shop/customers/link"),
    }


@pytest.mark.parametrize("path", ["/shop", "/shop/staff"])
def test_tenant_read_foreign_active_shop_id_does_not_leak_or_succeed(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    now = datetime(2026, 7, 27, 20, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    rows = create_cross_shop_rows(db_session)
    created = commit_authenticated_session(
        db_session,
        rows.user_a,
        now,
        settings,
        active_shop=rows.shop_b,
    )
    set_client_session_cookie(client, settings, created)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/select"
    assert_shop_security_headers(response)
    assert_no_hidden_b_values(response.text, rows)

    staff_a = fetch_staff(db_session, rows.staff_a.id)
    staff_b = fetch_staff(db_session, rows.staff_b.id)
    assert staff_a.is_active is True
    assert staff_a.role == ShopRole.OWNER.value
    assert staff_b.is_active is True
    assert staff_b.role == ShopRole.OWNER.value


def test_session_poisoning_clears_foreign_active_shop_id(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 20, 5, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    rows = create_cross_shop_rows(db_session)
    created = commit_authenticated_session(
        db_session,
        rows.user_a,
        now,
        settings,
        active_shop=rows.shop_b,
    )
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/select"
    assert fetch_active_shop_id(db_session, created) is None


def test_get_shop_select_is_na_but_lists_only_authenticated_user_memberships(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 20, 10, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    rows = create_cross_shop_rows(db_session)
    created = commit_authenticated_session(
        db_session,
        rows.user_a,
        now,
        settings,
        active_shop=rows.shop_b,
    )
    set_client_session_cookie(client, settings, created)

    response = client.get("/shop/select", follow_redirects=False)

    assert response.status_code == 200
    assert rows.shop_a.name in response.text
    assert str(rows.shop_a.id) in response.text
    assert_no_hidden_b_values(response.text, rows)
    assert fetch_active_shop_id(db_session, created) == rows.shop_b.id


def test_post_shop_select_foreign_shop_uuid_does_not_leak_or_mutate_session(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 20, 15, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    rows = create_cross_shop_rows(db_session)
    created = commit_authenticated_session(
        db_session,
        rows.user_a,
        now,
        settings,
        active_shop=rows.shop_a,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/select",
        data={"csrf_token": csrf_value(created), "shop_id": str(rows.shop_b.id)},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    assert_shop_security_headers(response)
    assert_no_hidden_b_values(response.text, rows)
    assert fetch_active_shop_id(db_session, created) == rows.shop_a.id

    staff_a = fetch_staff(db_session, rows.staff_a.id)
    staff_b = fetch_staff(db_session, rows.staff_b.id)
    assert staff_a.is_active is True
    assert staff_b.is_active is True


@pytest.mark.parametrize(
    ("path_template", "form_data"),
    [
        ("/shop/staff/{staff_id}/role", {"new_role": ShopRole.MANAGER.value}),
        ("/shop/staff/{staff_id}/revoke", {}),
    ],
)
def test_staff_mutation_foreign_staff_uuid_does_not_leak_or_mutate(
    m2_test_database: Engine,
    db_session: Session,
    path_template: str,
    form_data: dict[str, str],
) -> None:
    now = datetime(2026, 7, 27, 20, 20, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    rows = create_cross_shop_rows(db_session)
    created = commit_authenticated_session(
        db_session,
        rows.user_a,
        now,
        settings,
        active_shop=rows.shop_a,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        path_template.replace("{staff_id}", str(rows.staff_b.id)),
        data={"csrf_token": csrf_value(created), **form_data},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?error=staff_action_failed"
    assert_shop_security_headers(response)
    assert_no_hidden_b_values(response.text, rows)

    page = client.get(response.headers["location"], follow_redirects=False)
    assert page.status_code == 200
    page_text = unescape(page.text)
    assert "Xodim bo'yicha amal bajarilmadi." in page_text
    assert rows.shop_a.name in page.text
    assert_no_hidden_b_values(page.text, rows)

    staff_a = fetch_staff(db_session, rows.staff_a.id)
    staff_b = fetch_staff(db_session, rows.staff_b.id)
    assert staff_a.is_active is True
    assert staff_a.role == ShopRole.OWNER.value
    assert staff_b.is_active is True
    assert staff_b.role == ShopRole.OWNER.value


def test_staff_add_unknown_phone_is_not_distinguished_from_other_add_failure(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 20, 25, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    rows = create_cross_shop_rows(db_session)
    existing_user = add_user(db_session)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        rows.user_a,
        now,
        settings,
        active_shop=rows.shop_a,
    )
    set_client_session_cookie(client, settings, created)
    unknown_phone = "+998901119999"

    unknown_response = client.post(
        "/shop/staff/add",
        data={
            "csrf_token": csrf_value(created),
            "phone": unknown_phone,
            "role": ShopRole.CASHIER.value,
        },
        follow_redirects=False,
    )
    invalid_role_response = client.post(
        "/shop/staff/add",
        data={
            "csrf_token": csrf_value(created),
            "phone": existing_user.phone,
            "role": "seller",
        },
        follow_redirects=False,
    )

    assert unknown_response.status_code == 303
    assert invalid_role_response.status_code == 303
    assert unknown_response.headers["location"] == "/shop/staff?error=add_failed"
    assert invalid_role_response.headers["location"] == "/shop/staff?error=add_failed"
    assert_no_hidden_b_values(unknown_response.text, rows)
    assert_no_hidden_b_values(invalid_role_response.text, rows)

    page = client.get(unknown_response.headers["location"], follow_redirects=False)
    assert page.status_code == 200
    page_text = unescape(page.text)
    assert "Xodimni qo'shib bo'lmadi." in page_text
    assert "mavjud emas" not in page_text.casefold()
    assert unknown_phone not in page_text
    assert existing_user.phone not in page_text
    assert_no_hidden_b_values(page.text, rows)
    assert count_staff_for_user(db_session, shop=rows.shop_a, user=existing_user) == 0
    assert count_staff_for_user(db_session, shop=rows.shop_b, user=existing_user) == 0


def test_staff_add_existing_global_user_creates_membership_only_in_current_shop(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 20, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    rows = create_cross_shop_rows(db_session)
    target_user = add_user(db_session)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        rows.user_a,
        now,
        settings,
        active_shop=rows.shop_a,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/staff/add",
        data={
            "csrf_token": csrf_value(created),
            "phone": target_user.phone,
            "role": ShopRole.CASHIER.value,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?notice=staff_added"
    assert_shop_security_headers(response)
    assert_no_hidden_b_values(response.text, rows)
    assert count_staff_for_user(db_session, shop=rows.shop_a, user=target_user) == 1
    assert count_staff_for_user(db_session, shop=rows.shop_b, user=target_user) == 0

    added_staff = db_session.scalar(
        select(ShopStaff).where(
            ShopStaff.shop_id == rows.shop_a.id,
            ShopStaff.user_id == target_user.id,
        )
    )
    assert added_staff is not None
    assert added_staff.role == ShopRole.CASHIER.value
    assert added_staff.is_active is True

    staff_b = fetch_staff(db_session, rows.staff_b.id)
    assert staff_b.role == ShopRole.OWNER.value
    assert staff_b.is_active is True
