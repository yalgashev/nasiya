from datetime import UTC, datetime, timedelta
from pathlib import Path
from re import findall, search
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import User
from app.auth.phone import mask_phone_for_display
from app.auth.sessions import create_authenticated_session
from app.customer.models import Customer
from app.debt.commands import M17_ADMIN_WRITE_OFF_ROUTES
from app.debt.models import Debt
from app.debt.write_off_service import write_off_overdue_debt
from app.idempotency.models import IdempotencyKey
from app.main import create_app
from app.payment.repository import SqlAlchemyLockedDebtPostedTotalReader
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.rating.models import RatingEvent
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from tests.test_m17_repository_postgresql import CREATED, _overdue, _parents
from tests.test_m17_write_off_service_postgresql import (
    WRITTEN_OFF,
    _command,
    _seed_source,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 7, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-m17-admin-routes"


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def _client(engine: Engine) -> tuple[TestClient, Settings]:
    settings = _settings(engine)
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    application.state.write_off_clock = lambda: WRITTEN_OFF
    return TestClient(application), settings


def _authenticate(
    client: TestClient,
    settings: Settings,
    session: Session,
    *,
    user: User,
) -> str:
    created = create_authenticated_session(
        session,
        user.id,
        "pytest-m17-admin-write-off",
        NOW,
        settings=settings,
    )
    session.commit()
    csrf = get_csrf_token(created.session).as_form_value()
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )
    return csrf


def _hidden(name: str, response_text: str) -> str:
    matched = search(rf'name="{name}" value="([^"]+)"', response_text)
    assert matched is not None
    return matched.group(1)


def test_exact_three_admin_write_off_routes_are_registered_and_no_store(
    m2_test_database: Engine,
) -> None:
    assert tuple(
        (route.method, route.path) for route in M17_ADMIN_WRITE_OFF_ROUTES
    ) == (
        ("GET", "/admin/debts/write-off-candidates"),
        ("GET", "/admin/debts/{debt_id}/write-off"),
        ("POST", "/admin/debts/{debt_id}/write-off"),
    )
    application = create_app(settings=_settings(m2_test_database))
    registered = {
        (method.upper(), path)
        for path, operations in application.openapi()["paths"].items()
        if path.startswith("/admin/debts")
        for method in operations
    }
    assert registered == {
        ("GET", "/admin/debts/write-off-candidates"),
        ("GET", "/admin/debts/{debt_id}/write-off"),
        ("POST", "/admin/debts/{debt_id}/write-off"),
    }


def test_anonymous_admin_write_off_routes_redirect_without_cached_surface(
    m2_test_database: Engine,
) -> None:
    client, _settings_value = _client(m2_test_database)

    response = client.get("/admin/debts/write-off-candidates", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("role_kind", ("account", "shop_staff", "inactive"))
def test_non_admin_shop_staff_and_inactive_users_cannot_cross_admin_boundary(
    m2_test_database: Engine,
    role_kind: str,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        _admin, debt = _seed_source(session)
        relation = session.get_one(ShopCustomer, debt.shop_customer_id)
        user = User(
            phone=f"+998{uuid4().int % 1_000_000_000:09d}",
            is_active=True,
            is_platform_admin=False,
        )
        session.add(user)
        session.flush()
        if role_kind == "shop_staff":
            session.add(
                ShopStaff(
                    shop_id=relation.shop_id,
                    user_id=user.id,
                    role=ShopRole.OWNER.value,
                    is_active=True,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.flush()
        user_id = user.id
        phone = user.phone
        debt_id = debt.id
        csrf = _authenticate(client, settings, session, user=user)
    if role_kind == "inactive":
        with Session(m2_test_database) as session, session.begin():
            session.get_one(User, user_id).is_active = False

    responses = (
        client.get("/admin/debts/write-off-candidates", follow_redirects=False),
        client.get(f"/admin/debts/{debt_id}/write-off", follow_redirects=False),
        client.post(
            f"/admin/debts/{debt_id}/write-off",
            data={
                "csrf_token": csrf,
                "expected_revision": "3",
                "idempotency_key": str(uuid4()),
                "reason": "collection_exhausted",
                "confirmed": "yes",
            },
            follow_redirects=False,
        ),
    )
    expected_status = 303 if role_kind == "inactive" else 403
    assert all(response.status_code == expected_status for response in responses)
    assert all(
        response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
        for response in responses
    )
    assert all(str(user_id) not in response.text for response in responses)
    assert all(phone not in response.text for response in responses)
    with Session(m2_test_database) as session:
        assert session.get_one(Debt, debt_id).status == "overdue"
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyKey)
                .where(IdempotencyKey.endpoint == "admin.debts.write_off")
            )
            == 0
        )


def test_platform_admin_queue_detail_and_post_use_exact_service_composition(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        admin, debt = _seed_source(session)
        summary = session.execute(
            select(Shop.name, User.phone)
            .select_from(Debt)
            .join(ShopCustomer, ShopCustomer.id == Debt.shop_customer_id)
            .join(Shop, Shop.id == ShopCustomer.shop_id)
            .join(Customer, Customer.id == ShopCustomer.customer_id)
            .join(User, User.id == Customer.user_id)
            .where(Debt.id == debt.id)
        ).one()
        relation = session.get_one(ShopCustomer, debt.shop_customer_id)
        assert session.get_one(Shop, relation.shop_id).status == "suspended"
        debt_id = debt.id
        admin_id = admin.id
        admin_phone = admin.phone
        shop_name = summary.name
        full_phone = summary.phone
        masked_phone = mask_phone_for_display(full_phone)
        _authenticate(client, settings, session, user=admin)

    queue = client.get("/admin/debts/write-off-candidates")
    assert queue.status_code == 200
    assert queue.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert queue.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert f"/admin/debts/{debt_id}/write-off" in queue.text
    assert "/admin/offers" in queue.text
    assert shop_name in queue.text
    assert masked_phone in queue.text
    assert full_phone not in queue.text
    assert "100000 UZS" in queue.text
    assert "2026-08-04" in queue.text
    assert "Muddati o‘tgan" in queue.text

    detail = client.get(f"/admin/debts/{debt_id}/write-off")
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    raw_key = _hidden("idempotency_key", detail.text)
    UUID(raw_key)
    form = {
        "csrf_token": _hidden("csrf_token", detail.text),
        "expected_revision": _hidden("expected_revision", detail.text),
        "idempotency_key": raw_key,
        "reason": "collection_exhausted",
        "confirmed": "yes",
    }
    response = client.post(
        f"/admin/debts/{debt_id}/write-off",
        data=form,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/debts/{debt_id}/write-off"
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL

    replay = client.post(
        f"/admin/debts/{debt_id}/write-off",
        data=form,
        follow_redirects=False,
    )
    assert replay.status_code == 303
    assert replay.headers["location"] == f"/admin/debts/{debt_id}/write-off"

    completed = client.get(f"/admin/debts/{debt_id}/write-off")
    assert completed.status_code == 200
    assert "Undirish imkoniyatlari tugagan" in completed.text
    assert "Undirishdan chiqarilgan" in completed.text
    assert raw_key not in completed.text
    assert "collection_exhausted" not in completed.text
    assert str(admin_id) not in completed.text
    assert admin_phone not in completed.text
    assert full_phone not in completed.text
    assert 'name="idempotency_key"' not in completed.text
    with Session(m2_test_database) as session:
        assert session.get_one(Debt, debt_id).status == "written_off"
        assert _count_debt_rows(session, RatingEvent, debt_id) == 2
        assert _count_debt_rows(session, AuditLog, debt_id) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyKey)
                .where(IdempotencyKey.endpoint == "admin.debts.write_off")
            )
            == 1
        )
        audit_payloads = session.scalars(
            select(AuditLog.payload).where(AuditLog.object_id == debt_id)
        ).all()
        assert raw_key not in repr(audit_payloads)
        before_refresh = _domain_snapshot(session, debt_id)

    for _ in range(2):
        refreshed = client.get(f"/admin/debts/{debt_id}/write-off")
        assert refreshed.status_code == 200
        assert raw_key not in refreshed.text
    with Session(m2_test_database) as session:
        assert _domain_snapshot(session, debt_id) == before_refresh

    conflict_form = {**form, "reason": "fraud_or_abuse"}
    conflict = client.post(
        f"/admin/debts/{debt_id}/write-off",
        data=conflict_form,
        follow_redirects=False,
    )
    assert conflict.headers["location"] == (
        f"/admin/debts/{debt_id}/write-off?error=unavailable"
    )
    assert raw_key not in conflict.headers["location"]


def test_guessed_and_moved_admin_debt_locators_are_generic_unavailable(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        original_admin, debt = _seed_source(session)
        debt_id = debt.id
        csrf = _authenticate(client, settings, session, user=original_admin)

    guessed_id = uuid4()
    guessed_get = client.get(
        f"/admin/debts/{guessed_id}/write-off", follow_redirects=False
    )
    assert guessed_get.status_code == 303
    assert guessed_get.headers["location"] == "/admin/debts/write-off-candidates"
    assert guessed_get.headers["x-error-code"] == "DEBT_UNAVAILABLE"
    assert str(guessed_id) not in guessed_get.headers["location"]
    guessed_post = client.post(
        f"/admin/debts/{guessed_id}/write-off",
        data={
            "csrf_token": csrf,
            "expected_revision": "3",
            "idempotency_key": str(uuid4()),
            "reason": "collection_exhausted",
            "confirmed": "yes",
        },
        follow_redirects=False,
    )
    assert guessed_post.status_code == 303
    assert guessed_post.headers["location"] == (
        f"/admin/debts/{guessed_id}/write-off?error=unavailable"
    )

    with Session(m2_test_database) as session, session.begin():
        other_admin = User(
            phone=f"+998{uuid4().int % 1_000_000_000:09d}",
            is_active=True,
            is_platform_admin=True,
        )
        session.add(other_admin)
        session.flush()
        debt = session.get_one(Debt, debt_id)
        write_off_overdue_debt(
            session,
            command=_command(other_admin, debt),
            rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
            clock=lambda: WRITTEN_OFF,
        )

    moved = client.get(f"/admin/debts/{debt_id}/write-off", follow_redirects=False)
    assert moved.status_code == 303
    assert moved.headers["location"] == "/admin/debts/write-off-candidates"
    assert moved.headers["x-error-code"] == "DEBT_UNAVAILABLE"
    assert str(debt_id) not in moved.headers["location"]


def test_confirmation_and_invalid_input_use_one_generic_localized_prg_error(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        admin, debt = _seed_source(session)
        debt_id = debt.id
        _authenticate(client, settings, session, user=admin)

    detail = client.get(
        f"/admin/debts/{debt_id}/write-off",
        headers={"accept-language": "ru"},
    )
    raw_key = _hidden("idempotency_key", detail.text)
    assert '<html lang="ru">' in detail.text
    assert "Возможности взыскания исчерпаны" in detail.text
    form = {
        "csrf_token": _hidden("csrf_token", detail.text),
        "expected_revision": _hidden("expected_revision", detail.text),
        "idempotency_key": raw_key,
        "reason": "not-a-reason",
        "confirmed": "yes",
    }
    invalid = client.post(
        f"/admin/debts/{debt_id}/write-off",
        data=form,
        follow_redirects=False,
    )
    expected_location = f"/admin/debts/{debt_id}/write-off?error=unavailable"
    assert invalid.status_code == 303
    assert invalid.headers["location"] == expected_location
    assert raw_key not in invalid.headers["location"]
    assert form["reason"] not in invalid.headers["location"]

    localized = client.get(
        invalid.headers["location"], headers={"accept-language": "ru"}
    )
    assert "Не удалось выполнить действие" in localized.text

    fresh = client.get(f"/admin/debts/{debt_id}/write-off")
    stale = client.post(
        f"/admin/debts/{debt_id}/write-off",
        data={
            "csrf_token": _hidden("csrf_token", fresh.text),
            "expected_revision": "2",
            "idempotency_key": _hidden("idempotency_key", fresh.text),
            "reason": "collection_exhausted",
            "confirmed": "yes",
        },
        follow_redirects=False,
    )
    assert stale.headers["location"] == expected_location

    fresh = client.get(f"/admin/debts/{debt_id}/write-off")
    without_confirmation = client.post(
        f"/admin/debts/{debt_id}/write-off",
        data={
            "csrf_token": _hidden("csrf_token", fresh.text),
            "expected_revision": _hidden("expected_revision", fresh.text),
            "idempotency_key": _hidden("idempotency_key", fresh.text),
            "reason": "collection_exhausted",
        },
        follow_redirects=False,
    )
    assert without_confirmation.headers["location"] == expected_location
    with Session(m2_test_database) as session:
        assert session.get_one(Debt, debt_id).status == "overdue"
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyKey)
                .where(IdempotencyKey.endpoint == "admin.debts.write_off")
            )
            == 0
        )


def test_candidate_page_preserves_exact_order_and_page_50(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        actor, _shop, _customer, relation = _parents(session)
        debts = [
            _overdue(
                relation=relation,
                actor=actor,
                overdue_at=CREATED + timedelta(days=5, microseconds=index // 2),
            )
            for index in range(51)
        ]
        session.add_all(reversed(debts))
        session.flush()
        expected_ids = [
            row.id
            for row in sorted(debts, key=lambda row: (row.overdue_at, row.id))[:50]
        ]
        _authenticate(client, settings, session, user=actor)

    response = client.get("/admin/debts/write-off-candidates")
    paths = findall(r'href="(/admin/debts/[0-9a-f-]+/write-off)"', response.text)
    assert paths == [f"/admin/debts/{debt_id}/write-off" for debt_id in expected_ids]
    assert response.text.count("Asl summa bo‘yicha qoldiq") == 50


def test_admin_get_and_refresh_are_domain_retrieval_only(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    with Session(m2_test_database) as session:
        admin, debt = _seed_source(session)
        debt_id = debt.id
        _authenticate(client, settings, session, user=admin)
        before = _domain_snapshot(session, debt_id)

    for _ in range(2):
        assert client.get("/admin/debts/write-off-candidates").status_code == 200
        assert client.get(f"/admin/debts/{debt_id}/write-off").status_code == 200

    with Session(m2_test_database) as session:
        assert _domain_snapshot(session, debt_id) == before


def test_admin_write_off_pages_autoescape_untrusted_target_copy(
    m2_test_database: Engine,
) -> None:
    client, settings = _client(m2_test_database)
    untrusted_name = '<img src=x onerror="alert(1)">'
    with Session(m2_test_database) as session:
        admin, debt = _seed_source(session)
        relation = session.get_one(ShopCustomer, debt.shop_customer_id)
        session.get_one(Shop, relation.shop_id).name = untrusted_name
        debt_id = debt.id
        _authenticate(client, settings, session, user=admin)

    for path in (
        "/admin/debts/write-off-candidates",
        f"/admin/debts/{debt_id}/write-off",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert untrusted_name not in response.text
        assert "&lt;img src=x onerror=&#34;alert(1)&#34;&gt;" in response.text
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL


def test_admin_write_off_routes_are_server_rendered_only() -> None:
    source = (
        "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                Path("app/debt/admin_write_off_router.py"),
                Path("app/templates/debt/admin_write_off_candidates.html"),
                Path("app/templates/debt/admin_write_off_detail.html"),
            )
        )
    ).casefold()
    for forbidden in (
        "jsonresponse",
        "api/",
        "htmx",
        "<script",
        "<style",
        "date.now",
        "localstorage",
        "sessionstorage",
    ):
        assert forbidden not in source


def _count_debt_rows(session: Session, model, debt_id) -> int:
    debt_column = AuditLog.object_id if model is AuditLog else model.debt_id
    return int(
        session.scalar(
            select(func.count()).select_from(model).where(debt_column == debt_id)
        )
        or 0
    )


def _domain_snapshot(session: Session, debt_id) -> tuple[object, ...]:
    debt = session.get_one(Debt, debt_id)
    return (
        debt.status,
        debt.revision,
        debt.written_off_at,
        _count_debt_rows(session, RatingEvent, debt_id),
        _count_debt_rows(session, AuditLog, debt_id),
        int(session.scalar(select(func.count()).select_from(IdempotencyKey)) or 0),
    )
