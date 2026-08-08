from collections.abc import Generator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from html import unescape
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time, validate_csrf
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import (
    CreatedSession,
    create_anonymous_session,
    create_authenticated_session,
)
from app.db import create_database_session_factory
from app.debt.dependencies import get_detached_current_shop_debt_actor_authority
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop_customer.dependencies import get_detached_shop_customer_authority

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-route-matrix"


class RouteClass(StrEnum):
    CONTEXT = "context"
    TENANT_READ = "tenant_read"
    OWNER_BUSINESS_MUTATION = "owner_business_mutation"
    TENANT_BUSINESS_MUTATION = "tenant_business_mutation"
    TENANT_LOCATOR_READ = "tenant_locator_read"
    TENANT_ACTIVE_FORM = "tenant_active_form"


@dataclass(frozen=True)
class RoutePolicy:
    method: str
    path_format: str
    classification: RouteClass

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path_format)


ROUTE_POLICIES = (
    RoutePolicy("GET", "/shop/select", RouteClass.CONTEXT),
    RoutePolicy("POST", "/shop/select", RouteClass.CONTEXT),
    RoutePolicy("GET", "/shop", RouteClass.TENANT_READ),
    RoutePolicy("GET", "/shop/staff", RouteClass.TENANT_READ),
    RoutePolicy("GET", "/shop/customers", RouteClass.TENANT_READ),
    RoutePolicy("GET", "/shop/settings/credit", RouteClass.TENANT_READ),
    RoutePolicy(
        "GET",
        "/shop/customers/{shop_customer_id}/debts",
        RouteClass.TENANT_LOCATOR_READ,
    ),
    RoutePolicy(
        "GET",
        "/shop/customers/{shop_customer_id}/debts/new",
        RouteClass.TENANT_ACTIVE_FORM,
    ),
    RoutePolicy(
        "POST",
        "/shop/customers/{shop_customer_id}/debts",
        RouteClass.TENANT_BUSINESS_MUTATION,
    ),
    RoutePolicy("GET", "/shop/debts/{debt_id}", RouteClass.TENANT_LOCATOR_READ),
    RoutePolicy(
        "POST",
        "/shop/debts/{debt_id}/cancel",
        RouteClass.TENANT_BUSINESS_MUTATION,
    ),
    RoutePolicy(
        "POST",
        "/shop/customers/link",
        RouteClass.TENANT_BUSINESS_MUTATION,
    ),
    RoutePolicy(
        "POST",
        "/shop/customers/{shop_customer_id}/policy",
        RouteClass.TENANT_BUSINESS_MUTATION,
    ),
    RoutePolicy(
        "POST",
        "/shop/settings/credit",
        RouteClass.TENANT_BUSINESS_MUTATION,
    ),
    RoutePolicy("POST", "/shop/staff/add", RouteClass.OWNER_BUSINESS_MUTATION),
    RoutePolicy(
        "POST",
        "/shop/staff/{staff_id}/role",
        RouteClass.OWNER_BUSINESS_MUTATION,
    ),
    RoutePolicy(
        "POST",
        "/shop/staff/{staff_id}/revoke",
        RouteClass.OWNER_BUSINESS_MUTATION,
    ),
)
POST_ROUTE_POLICIES = tuple(
    policy for policy in ROUTE_POLICIES if policy.method == "POST"
)
TENANT_READ_POLICIES = tuple(
    policy
    for policy in ROUTE_POLICIES
    if policy.classification is RouteClass.TENANT_READ
)
OWNER_MUTATION_POLICIES = tuple(
    policy
    for policy in ROUTE_POLICIES
    if policy.classification is RouteClass.OWNER_BUSINESS_MUTATION
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


def add_shop(
    db_session: Session,
    *,
    status: str = ShopStatus.ACTIVE.value,
) -> Shop:
    shop = Shop(
        name=f"Matrix Shop {uuid4().hex[:8]}", phone=unique_phone(), status=status
    )
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
    *,
    active_shop: Shop | None = None,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-shop-route-matrix",
        now,
        settings=settings,
    )
    if active_shop is not None:
        created.session.active_shop_id = active_shop.id
    db_session.commit()
    return created


def commit_anonymous_session(
    db_session: Session,
    now: datetime,
    settings: Settings,
) -> CreatedSession:
    created = create_anonymous_session(
        db_session,
        "pytest-shop-route-matrix",
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
        domain="testserver.local",
        path="/",
    )


def csrf_value(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def actual_path(policy: RoutePolicy, staff_id: UUID | None = None) -> str:
    path = policy.path_format
    if "{staff_id}" in path:
        assert staff_id is not None
        path = path.replace("{staff_id}", str(staff_id))
    return path.replace("{shop_customer_id}", str(uuid4())).replace(
        "{debt_id}", str(uuid4())
    )


def post_data_for(
    policy: RoutePolicy,
    *,
    csrf_token: str | None = None,
    target_phone: str = "+998901234560",
) -> dict[str, str]:
    data: dict[str, str] = {}
    if csrf_token is not None:
        data["csrf_token"] = csrf_token
    if policy.path_format == "/shop/select":
        data["shop_id"] = target_phone
    elif policy.path_format == "/shop/staff/add":
        data["phone"] = target_phone
        data["role"] = ShopRole.CASHIER.value
    elif policy.path_format.endswith("/role"):
        data["new_role"] = ShopRole.MANAGER.value
    elif policy.path_format == "/shop/customers/link":
        data["phone"] = target_phone
    elif policy.path_format.endswith("/policy"):
        data.update(
            expected_revision="1",
            credit_limit_uzs="1000000",
            max_open_debts="2",
            list_status="normal",
        )
    elif policy.path_format == "/shop/settings/credit":
        data.update(
            expected_updated_at="2026-07-27T19:00:00+00:00",
            credit_limit_uzs="1000000",
            max_open_debts="2",
        )
    return data


def assert_shop_security_headers(response) -> None:
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


def iter_application_api_routes(application: FastAPI) -> Iterator[APIRoute]:
    yield from iter_api_routes(application.routes)


def iter_dependency_calls(dependant: Dependant) -> Iterator[object]:
    for dependency in dependant.dependencies:
        if dependency.call is not None:
            yield dependency.call
        yield from iter_dependency_calls(dependency)


def route_has_csrf_dependency(route: APIRoute) -> bool:
    return any(
        dependency_call
        in {
            validate_csrf,
            get_detached_shop_customer_authority,
            get_detached_current_shop_debt_actor_authority,
        }
        for dependency_call in iter_dependency_calls(route.dependant)
    )


def collect_shop_route_keys(application: FastAPI) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for route in iter_application_api_routes(application):
        if route.path_format.startswith("/shop"):
            for method in route.methods or set():
                if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    keys.add((method, route.path_format))
    return keys


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


def test_all_shop_routes_have_explicit_security_classification() -> None:
    application = create_app()

    actual = collect_shop_route_keys(application)
    expected = {policy.key for policy in ROUTE_POLICIES}

    assert actual == expected


def test_all_shop_post_routes_have_csrf_dependency() -> None:
    application = create_app()
    csrf_route_keys = {
        (method, route.path_format)
        for route in iter_application_api_routes(application)
        if route.path_format.startswith("/shop")
        if route_has_csrf_dependency(route)
        for method in route.methods or set()
        if method == "POST"
    }

    assert csrf_route_keys == {policy.key for policy in POST_ROUTE_POLICIES}


@pytest.mark.parametrize("policy", ROUTE_POLICIES)
def test_shop_routes_have_anonymous_behavior(
    m2_test_database: Engine,
    db_session: Session,
    policy: RoutePolicy,
) -> None:
    now = datetime(2026, 7, 27, 19, 0, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)

    if policy.method == "GET":
        response = client.get(actual_path(policy), follow_redirects=False)
    else:
        created = commit_anonymous_session(db_session, now, settings)
        set_client_session_cookie(client, settings, created)
        response = client.post(
            actual_path(policy, staff_id=uuid4()),
            data=post_data_for(
                policy,
                csrf_token=csrf_value(created),
                target_phone=str(uuid4()),
            ),
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value
    assert_shop_security_headers(response)


@pytest.mark.parametrize("policy", ROUTE_POLICIES)
def test_shop_routes_have_no_membership_behavior_with_select_exception(
    m2_test_database: Engine,
    db_session: Session,
    policy: RoutePolicy,
) -> None:
    now = datetime(2026, 7, 27, 19, 5, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    if policy.method == "GET":
        response = client.get(actual_path(policy), follow_redirects=False)
    else:
        response = client.post(
            actual_path(policy, staff_id=uuid4()),
            data=post_data_for(
                policy,
                csrf_token=csrf_value(created),
                target_phone=str(uuid4()),
            ),
            follow_redirects=False,
        )

    if policy.path_format == "/shop/select" and policy.method == "GET":
        assert response.status_code == 200
        assert "hech bir do'konga bog'lanmagansiz" in response.text
    elif policy.path_format == "/shop/select" and policy.method == "POST":
        assert response.status_code == 403
        assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    else:
        assert response.status_code == 303
        assert response.headers["location"] == "/shop/select"
    assert_shop_security_headers(response)


@pytest.mark.parametrize("policy", TENANT_READ_POLICIES)
def test_tenant_read_routes_allow_suspended_shop_membership(
    m2_test_database: Engine,
    db_session: Session,
    policy: RoutePolicy,
) -> None:
    now = datetime(2026, 7, 27, 19, 10, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    shop = add_shop(db_session, status=ShopStatus.SUSPENDED.value)
    add_staff(db_session, shop=shop, user=user, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.get(policy.path_format, follow_redirects=False)

    assert response.status_code == 200
    assert "faqat ko'rish rejimi" in unescape(response.text)
    assert_shop_security_headers(response)


@pytest.mark.parametrize("policy", POST_ROUTE_POLICIES)
def test_all_shop_post_routes_without_csrf_are_csrf_failed(
    m2_test_database: Engine,
    db_session: Session,
    policy: RoutePolicy,
) -> None:
    now = datetime(2026, 7, 27, 19, 15, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    shop = add_shop(db_session)
    staff = add_staff(db_session, shop=shop, user=user, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        user,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        actual_path(policy, staff_id=staff.id),
        data=post_data_for(policy, target_phone=str(shop.id)),
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert_shop_security_headers(response)


@pytest.mark.parametrize("actor_role", [ShopRole.MANAGER, ShopRole.CASHIER])
@pytest.mark.parametrize("policy", OWNER_MUTATION_POLICIES)
def test_manager_and_cashier_are_forbidden_on_owner_business_mutations(
    m2_test_database: Engine,
    db_session: Session,
    policy: RoutePolicy,
    actor_role: ShopRole,
) -> None:
    now = datetime(2026, 7, 27, 19, 20, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    actor = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session)
    add_staff(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    add_staff(db_session, shop=shop, user=actor, role=actor_role.value)
    target_staff = add_staff(
        db_session,
        shop=shop,
        user=target,
        role=ShopRole.CASHIER.value,
    )
    db_session.commit()
    created = commit_authenticated_session(
        db_session,
        actor,
        now,
        settings,
        active_shop=shop,
    )
    set_client_session_cookie(client, settings, created)

    response = client.post(
        actual_path(policy, staff_id=target_staff.id),
        data=post_data_for(
            policy,
            csrf_token=csrf_value(created),
            target_phone=target.phone,
        ),
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    assert_shop_security_headers(response)


def test_context_post_prg_success_and_suspended_shop_is_not_blocked(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    now = datetime(2026, 7, 27, 19, 25, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    user = add_user(db_session)
    suspended_shop = add_shop(db_session, status=ShopStatus.SUSPENDED.value)
    add_staff(db_session, shop=suspended_shop, user=user, role=ShopRole.OWNER.value)
    db_session.commit()
    created = commit_authenticated_session(db_session, user, now, settings)
    set_client_session_cookie(client, settings, created)

    response = client.post(
        "/shop/select",
        data={"csrf_token": csrf_value(created), "shop_id": str(suspended_shop.id)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop"
    assert "x-error-code" not in response.headers
    assert_shop_security_headers(response)
    assert fetch_active_shop_id(db_session, created) == suspended_shop.id


@pytest.mark.parametrize("policy", OWNER_MUTATION_POLICIES)
def test_owner_business_mutations_have_prg_success(
    m2_test_database: Engine,
    db_session: Session,
    policy: RoutePolicy,
) -> None:
    now = datetime(2026, 7, 27, 19, 30, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    target = add_user(db_session)
    shop = add_shop(db_session)
    owner_staff = add_staff(
        db_session, shop=shop, user=owner, role=ShopRole.OWNER.value
    )
    target_staff = add_staff(
        db_session,
        shop=shop,
        user=target,
        role=ShopRole.CASHIER.value,
    )
    if policy.path_format == "/shop/staff/add":
        db_session.delete(target_staff)
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
        actual_path(policy, staff_id=target_staff.id),
        data=post_data_for(
            policy,
            csrf_token=csrf_value(created),
            target_phone=target.phone,
        ),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert_shop_security_headers(response)
    if policy.path_format == "/shop/staff/add":
        assert response.headers["location"] == "/shop/staff?notice=staff_added"
        added_staff = db_session.scalar(
            select(ShopStaff).where(
                ShopStaff.shop_id == shop.id,
                ShopStaff.user_id == target.id,
            )
        )
        assert added_staff is not None
        assert added_staff.is_active is True
    elif policy.path_format.endswith("/role"):
        assert response.headers["location"] == "/shop/staff?notice=role_updated"
        assert fetch_staff(db_session, target_staff.id).role == ShopRole.MANAGER.value
    else:
        assert response.headers["location"] == "/shop/staff?notice=staff_revoked"
        assert fetch_staff(db_session, target_staff.id).is_active is False

    assert owner_staff.id is not None


@pytest.mark.parametrize("policy", OWNER_MUTATION_POLICIES)
def test_owner_business_mutations_are_blocked_when_shop_is_suspended(
    m2_test_database: Engine,
    db_session: Session,
    policy: RoutePolicy,
) -> None:
    now = datetime(2026, 7, 27, 19, 35, tzinfo=UTC)
    client, settings = make_client(m2_test_database, now)
    owner = add_user(db_session)
    target = add_user(db_session)
    new_user = add_user(db_session)
    shop = add_shop(db_session, status=ShopStatus.SUSPENDED.value)
    add_staff(db_session, shop=shop, user=owner, role=ShopRole.OWNER.value)
    target_staff = add_staff(
        db_session,
        shop=shop,
        user=target,
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
        actual_path(policy, staff_id=target_staff.id),
        data=post_data_for(
            policy,
            csrf_token=csrf_value(created),
            target_phone=new_user.phone,
        ),
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/staff?error=shop_suspended"
    assert_shop_security_headers(response)
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
