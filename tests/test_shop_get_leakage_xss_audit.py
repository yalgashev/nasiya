import logging
import re
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-get-audit"
NOW = datetime(2026, 7, 27, 22, 0, tzinfo=UTC)
SHOP_GET_PATHS = ("/shop", "/shop/select", "/shop/staff")
SHOP_CONTEXT_GET_PATHS = ("/shop", "/shop/staff")
M12_SHOP_GET_PATHS = ("/shop/customers", "/shop/settings/credit")
M13_SHOP_GET_PATHS = (
    "/shop/customers/{shop_customer_id}/debts",
    "/shop/customers/{shop_customer_id}/debts/new",
    "/shop/debts/{debt_id}",
)
M14_SHOP_GET_PATHS = (
    "/shop/debts/{debt_id}/payments",
    "/shop/debts/{debt_id}/payments/new",
    "/shop/payments/{payment_id}",
)
SHOP_TEMPLATE_PATHS = tuple(
    sorted(
        (Path(__file__).resolve().parents[1] / "app" / "templates" / "shop").glob(
            "*.html"
        )
    )
)
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
SHOP_DOMAIN_MODELS = (Shop, ShopStaff, ShopStatusEvent, ShopStaffEvent)


@dataclass(frozen=True)
class AuditRows:
    current_user: User
    other_user: User
    shop: Shop
    current_staff: ShopStaff
    other_staff: ShopStaff


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


def add_user(db_session: Session, *, phone: str | None = None) -> User:
    user = User(phone=phone or unique_phone())
    db_session.add(user)
    db_session.flush()
    return user


def add_shop(
    db_session: Session,
    *,
    name: str = "GET Audit Shop",
    status: str = ShopStatus.ACTIVE.value,
) -> Shop:
    shop = Shop(
        name=name,
        phone=unique_phone(),
        status=status,
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
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user.id,
        role=role,
    )
    db_session.add(staff)
    db_session.flush()
    return staff


def add_status_event(
    db_session: Session,
    *,
    shop: Shop,
    actor: User,
    action: str,
    reason: str | None,
) -> ShopStatusEvent:
    event = ShopStatusEvent(
        shop_id=shop.id,
        action=action,
        actor_user_id=actor.id,
        reason=reason,
    )
    db_session.add(event)
    db_session.flush()
    return event


def add_staff_event(
    db_session: Session,
    *,
    shop: Shop,
    subject: User,
    actor: User,
    role: str,
) -> ShopStaffEvent:
    event = ShopStaffEvent(
        shop_id=shop.id,
        subject_user_id=subject.id,
        action=ShopStaffAction.ADDED.value,
        old_role=None,
        new_role=role,
        actor_user_id=actor.id,
    )
    db_session.add(event)
    db_session.flush()
    return event


def create_audit_rows(
    db_session: Session,
    *,
    name: str = "GET Audit Shop",
    status: str = ShopStatus.ACTIVE.value,
) -> AuditRows:
    current_user = add_user(db_session)
    other_user = add_user(db_session)
    shop = add_shop(db_session, name=name, status=status)
    current_staff = add_staff(
        db_session,
        shop=shop,
        user=current_user,
        role=ShopRole.OWNER.value,
    )
    other_staff = add_staff(
        db_session,
        shop=shop,
        user=other_user,
        role=ShopRole.CASHIER.value,
    )
    add_status_event(
        db_session,
        shop=shop,
        actor=current_user,
        action=ShopStatusAction.ACTIVATED.value,
        reason=None,
    )
    add_staff_event(
        db_session,
        shop=shop,
        subject=current_user,
        actor=current_user,
        role=ShopRole.OWNER.value,
    )
    add_staff_event(
        db_session,
        shop=shop,
        subject=other_user,
        actor=current_user,
        role=ShopRole.CASHIER.value,
    )
    db_session.commit()
    return AuditRows(
        current_user=current_user,
        other_user=other_user,
        shop=shop,
        current_staff=current_staff,
        other_staff=other_staff,
    )


def authenticate(
    db_session: Session,
    *,
    user: User,
    settings: Settings,
    active_shop: Shop | None,
) -> CreatedSession:
    created = create_authenticated_session(
        db_session,
        user.id,
        "pytest-shop-get-audit",
        NOW,
        settings=settings,
    )
    if active_shop is not None:
        created.session.active_shop_id = active_shop.id
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


def snapshot_model(
    db_session: Session,
    model: type,
) -> tuple[int, tuple[tuple[object, ...], ...]]:
    columns = tuple(model.__table__.columns)
    count = db_session.scalar(select(func.count()).select_from(model)) or 0
    rows = db_session.scalars(select(model).order_by(model.id)).all()
    row_state = tuple(
        tuple(getattr(row, column.key) for column in columns) for row in rows
    )
    return count, row_state


def snapshot_shop_domain(
    db_session: Session,
) -> dict[str, tuple[int, tuple[tuple[object, ...], ...]]]:
    db_session.expire_all()
    return {
        model.__tablename__: snapshot_model(db_session, model)
        for model in SHOP_DOMAIN_MODELS
    }


def snapshot_auth_session(
    db_session: Session,
    created: CreatedSession,
) -> dict[str, object]:
    db_session.expire_all()
    stored = db_session.get(AuthSession, created.session.id)
    assert stored is not None
    return {
        column.key: getattr(stored, column.key)
        for column in AuthSession.__table__.columns
    }


def assert_only_active_shop_changed(
    before: dict[str, object],
    after: dict[str, object],
    *,
    expected_active_shop_id: UUID | None,
) -> None:
    assert after["active_shop_id"] == expected_active_shop_id
    assert {key: value for key, value in before.items() if key != "active_shop_id"} == {
        key: value for key, value in after.items() if key != "active_shop_id"
    }


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


def test_all_shop_get_routes_are_in_the_side_effect_audit() -> None:
    application = create_app()
    actual = {
        route.path
        for route in iter_api_routes(application.routes)
        if route.path.startswith("/shop") and "GET" in (route.methods or set())
    }

    assert actual == {
        *SHOP_GET_PATHS,
        *M12_SHOP_GET_PATHS,
        *M13_SHOP_GET_PATHS,
        *M14_SHOP_GET_PATHS,
    }


@pytest.mark.parametrize(
    ("path", "start_with_selected_shop"),
    [
        ("/shop", True),
        ("/shop/select", False),
        ("/shop/staff", True),
    ],
)
def test_shop_gets_do_not_mutate_domain_rows_or_unrelated_session_state(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
    start_with_selected_shop: bool,
) -> None:
    client, settings = make_client(m2_test_database)
    rows = create_audit_rows(db_session)
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=rows.shop if start_with_selected_shop else None,
    )
    set_session_cookie(client, settings, created)
    before_domain = snapshot_shop_domain(db_session)
    before_session = snapshot_auth_session(db_session, created)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 200
    assert snapshot_shop_domain(db_session) == before_domain
    assert snapshot_auth_session(db_session, created) == before_session


@pytest.mark.parametrize("path", SHOP_CONTEXT_GET_PATHS)
def test_context_get_auto_selects_once_without_mutating_shop_domain(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    client, settings = make_client(m2_test_database)
    rows = create_audit_rows(db_session)
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=None,
    )
    set_session_cookie(client, settings, created)
    before_domain = snapshot_shop_domain(db_session)
    before_session = snapshot_auth_session(db_session, created)
    assert before_session["active_shop_id"] is None

    first_response = client.get(path, follow_redirects=False)

    assert first_response.status_code == 200
    after_auto_select = snapshot_auth_session(db_session, created)
    assert_only_active_shop_changed(
        before_session,
        after_auto_select,
        expected_active_shop_id=rows.shop.id,
    )
    assert snapshot_shop_domain(db_session) == before_domain

    second_response = client.get(path, follow_redirects=False)

    assert second_response.status_code == 200
    assert snapshot_auth_session(db_session, created) == after_auto_select
    assert snapshot_shop_domain(db_session) == before_domain


@pytest.mark.parametrize("path", SHOP_CONTEXT_GET_PATHS)
def test_context_get_clears_foreign_selection_without_mutating_shop_domain(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    client, settings = make_client(m2_test_database)
    rows = create_audit_rows(db_session)
    foreign_owner = add_user(db_session)
    foreign_shop = add_shop(db_session, name="Foreign GET Audit Shop")
    add_staff(
        db_session,
        shop=foreign_shop,
        user=foreign_owner,
        role=ShopRole.OWNER.value,
    )
    db_session.commit()
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=foreign_shop,
    )
    set_session_cookie(client, settings, created)
    before_domain = snapshot_shop_domain(db_session)
    before_session = snapshot_auth_session(db_session, created)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/select"
    assert_only_active_shop_changed(
        before_session,
        snapshot_auth_session(db_session, created),
        expected_active_shop_id=None,
    )
    assert snapshot_shop_domain(db_session) == before_domain


def test_workspace_does_not_render_shop_or_user_identifiers(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    rows = create_audit_rows(db_session)
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=rows.shop,
    )
    set_session_cookie(client, settings, created)

    response = client.get("/shop")

    assert response.status_code == 200
    assert rows.shop.name in response.text
    for hidden_value in (
        str(rows.shop.id),
        rows.shop.phone,
        str(rows.current_user.id),
        rows.current_user.phone,
        str(rows.other_user.id),
        rows.other_user.phone,
    ):
        assert hidden_value not in response.text


def test_staff_page_masks_phones_and_uses_only_staff_uuids_in_forms(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    rows = create_audit_rows(db_session)
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=rows.shop,
    )
    set_session_cookie(client, settings, created)

    response = client.get("/shop/staff")

    assert response.status_code == 200
    assert mask_phone_for_display(rows.current_user.phone) in response.text
    assert mask_phone_for_display(rows.other_user.phone) in response.text
    for hidden_value in (
        str(rows.shop.id),
        rows.shop.phone,
        str(rows.current_user.id),
        rows.current_user.phone,
        str(rows.other_user.id),
        rows.other_user.phone,
    ):
        assert hidden_value not in response.text

    rendered_uuids = {match.casefold() for match in UUID_PATTERN.findall(response.text)}
    expected_staff_uuids = {
        str(rows.current_staff.id),
        str(rows.other_staff.id),
    }
    assert rendered_uuids == expected_staff_uuids
    for staff_id in expected_staff_uuids:
        assert f'action="/shop/staff/{staff_id}/role"' in response.text
        assert f'action="/shop/staff/{staff_id}/revoke"' in response.text


def test_forbidden_error_body_does_not_leak_tenant_or_database_details(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    owner_a = add_user(db_session)
    manager_a = add_user(db_session)
    owner_b = add_user(db_session)
    shop_a = add_shop(db_session, name="Confidential Current Shop")
    shop_b = add_shop(db_session, name="Confidential Foreign Shop")
    add_staff(db_session, shop=shop_a, user=owner_a, role=ShopRole.OWNER.value)
    add_staff(db_session, shop=shop_a, user=manager_a, role=ShopRole.MANAGER.value)
    foreign_staff = add_staff(
        db_session,
        shop=shop_b,
        user=owner_b,
        role=ShopRole.OWNER.value,
    )
    db_session.commit()
    created = authenticate(
        db_session,
        user=manager_a,
        settings=settings,
        active_shop=shop_a,
    )
    set_session_cookie(client, settings, created)

    response = client.post(
        f"/shop/staff/{foreign_staff.id}/role",
        data={
            "csrf_token": csrf_value(created),
            "new_role": ShopRole.CASHIER.value,
        },
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.headers["x-error-code"] == ErrorCode.FORBIDDEN.value
    for hidden_value in (
        shop_a.name,
        shop_a.phone,
        shop_b.name,
        shop_b.phone,
        str(shop_b.id),
        str(foreign_staff.id),
        manager_a.phone,
        owner_b.phone,
        created.raw_token.as_cookie_value(),
        "IntegrityError",
        "SELECT ",
        "shop_staff",
        "uq_shop_staff_shop_id_user_id",
        "ck_shop_staff_role_allowed",
    ):
        assert hidden_value not in response.text


def test_shop_get_logs_do_not_contain_phone_session_token_or_status_reason(
    m2_test_database: Engine,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, settings = make_client(m2_test_database)
    rows = create_audit_rows(db_session, status=ShopStatus.SUSPENDED.value)
    status_reason = "internal-status-reason-7f6d3c2b"
    add_status_event(
        db_session,
        shop=rows.shop,
        actor=rows.current_user,
        action=ShopStatusAction.SUSPENDED.value,
        reason=status_reason,
    )
    db_session.commit()
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=rows.shop,
    )
    set_session_cookie(client, settings, created)
    raw_session_token = created.raw_token.as_cookie_value()
    caplog.clear()

    with caplog.at_level(logging.DEBUG):
        responses = [
            client.get(path, follow_redirects=False) for path in SHOP_GET_PATHS
        ]

    assert all(response.status_code == 200 for response in responses)
    for hidden_value in (
        rows.shop.phone,
        rows.current_user.phone,
        rows.other_user.phone,
        raw_session_token,
        status_reason,
    ):
        assert hidden_value not in caplog.text


@pytest.mark.parametrize("path", SHOP_GET_PATHS)
def test_rendered_shop_name_is_jinja_escaped(
    m2_test_database: Engine,
    db_session: Session,
    path: str,
) -> None:
    client, settings = make_client(m2_test_database)
    xss_name = 'XSS <script>alert("shop-xss")</script><img src=x onerror=alert(1)>'
    rows = create_audit_rows(db_session, name=xss_name)
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=rows.shop,
    )
    set_session_cookie(client, settings, created)

    response = client.get(path)

    assert response.status_code == 200
    assert xss_name not in response.text
    assert "<script" not in response.text.casefold()
    assert "<img" not in response.text.casefold()
    assert "&lt;script&gt;" in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text


def test_shop_templates_do_not_disable_autoescape_with_safe_filter() -> None:
    assert {path.name for path in SHOP_TEMPLATE_PATHS} == {
        "select.html",
        "staff.html",
        "workspace.html",
    }
    safe_filter = re.compile(r"\|\s*safe\b")

    for template_path in SHOP_TEMPLATE_PATHS:
        template_source = template_path.read_text(encoding="utf-8")
        assert safe_filter.search(template_source) is None, template_path


def test_status_reason_has_no_m5_web_render_surface(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    client, settings = make_client(m2_test_database)
    rows = create_audit_rows(db_session, status=ShopStatus.SUSPENDED.value)
    status_reason = "private suspension review reference 4c2e91"
    event = add_status_event(
        db_session,
        shop=rows.shop,
        actor=rows.current_user,
        action=ShopStatusAction.SUSPENDED.value,
        reason=status_reason,
    )
    db_session.commit()
    created = authenticate(
        db_session,
        user=rows.current_user,
        settings=settings,
        active_shop=rows.shop,
    )
    set_session_cookie(client, settings, created)
    db_session.refresh(event)
    assert event.reason == status_reason

    responses = [client.get(path, follow_redirects=False) for path in SHOP_GET_PATHS]

    assert all(response.status_code == 200 for response in responses)
    for response in responses:
        assert status_reason not in response.text
