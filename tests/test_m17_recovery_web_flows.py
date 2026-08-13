from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
from re import search
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.deps import get_current_time
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.main import create_app
from app.payment import service as payment_service
from app.payment.models import Payment
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL
from app.settings import Settings
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from app.telegram.models import TelegramLink
from tests.test_m17_recovery_service_postgresql import _seed_written_off_source
from tests.test_payment_targeting_postgresql import _add_shop_actor

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)
RATE_KEY = "test-rate-limit-hmac-key-for-m17-recovery-web"


def _client(
    engine: Engine, *, actor_id: UUID, shop_id: UUID
) -> tuple[TestClient, Settings]:
    settings = Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=RATE_KEY,
    )
    application = create_app(settings=settings)
    application.dependency_overrides[get_current_time] = lambda: NOW
    client = TestClient(application, client=("127.0.0.1", 51734))
    with create_database_session_factory(engine).begin() as session:
        created = create_authenticated_session(
            session,
            actor_id,
            "pytest-m17-recovery-web",
            NOW,
            settings=settings,
        )
        created.session.active_shop_id = shop_id
        session.flush()
        cookie = created.raw_token.as_cookie_value()
    client.cookies.set(
        settings.session_cookie_name,
        cookie,
        domain="testserver.local",
        path="/",
    )
    return client, settings


def _hidden(html: str, name: str) -> str:
    matched = search(rf'name="{name}" value="([^"]+)"', html)
    assert matched is not None
    return matched.group(1)


def _activate_customer_debt_read_surface(engine: Engine, *, debt_id: UUID) -> UUID:
    with Session(engine) as session, session.begin():
        row = session.execute(
            select(Customer, User)
            .join(ShopCustomer, ShopCustomer.customer_id == Customer.id)
            .join(Debt, Debt.shop_customer_id == ShopCustomer.id)
            .join(User, User.id == Customer.user_id)
            .where(Debt.id == debt_id)
        ).one()
        customer, user = row
        user.is_active = True
        customer.onboarding_status = "active"
        customer.activated_at = NOW
        customer.updated_at = NOW
        session.add(
            TelegramLink(
                user_id=user.id,
                telegram_chat_id=user.id.int % 8_000_000_000 + 1,
                linked_at=NOW,
                phone_verified_at=NOW,
                updated_at=NOW,
            )
        )
        return user.id


def _form(html: str, *, amount: str) -> dict[str, str]:
    return {
        "csrf_token": _hidden(html, "csrf_token"),
        "idempotency_key": _hidden(html, "idempotency_key"),
        "expected_revision": _hidden(html, "expected_revision"),
        "expected_balance_basis": _hidden(html, "expected_balance_basis"),
        "amount_uzs": amount,
        "method": "cash",
    }


def _csrf_for_actor(engine: Engine, *, actor_id: UUID) -> str:
    with Session(engine) as session:
        row = session.scalar(
            select(AuthSession)
            .where(AuthSession.user_id == actor_id)
            .order_by(AuthSession.created_at.desc())
        )
        assert row is not None
        return get_csrf_token(row).as_form_value()


@pytest.mark.parametrize("role", tuple(ShopRole))
def test_all_live_shop_roles_receive_the_same_written_off_recovery_form(
    m2_test_database: Engine,
    role: ShopRole,
) -> None:
    actor_id, shop_id, debt_id = _seed_written_off_source(m2_test_database)
    with Session(m2_test_database) as session, session.begin():
        staff = session.scalar(
            select(ShopStaff).where(
                ShopStaff.shop_id == shop_id, ShopStaff.user_id == actor_id
            )
        )
        assert staff is not None
        staff.role = role.value
    client, _settings = _client(m2_test_database, actor_id=actor_id, shop_id=shop_id)
    page = client.get(f"/shop/debts/{debt_id}/payments/new")
    assert page.status_code == 200
    assert page.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert _hidden(unescape(page.text), "expected_balance_basis") == "original"


def test_active_shop_can_recover_written_off_debt_and_all_safe_pages_localize(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(payment_service, "_utc_now", lambda: NOW)
    actor_id, shop_id, debt_id = _seed_written_off_source(m2_test_database)
    customer_user_id = _activate_customer_debt_read_surface(
        m2_test_database, debt_id=debt_id
    )
    staff_client, _settings = _client(
        m2_test_database, actor_id=actor_id, shop_id=shop_id
    )
    customer_client, _settings = _client(
        m2_test_database, actor_id=customer_user_id, shop_id=shop_id
    )
    history_path = f"/shop/debts/{debt_id}/payments"

    for path in (f"/shop/debts/{debt_id}", history_path):
        page = staff_client.get(path)
        html = unescape(page.text)
        assert page.status_code == 200
        assert page.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
        assert "Undirishdan chiqarilgan" in html
        assert f"{history_path}/new" in html
        assert "Asl summa bo'yicha hisob" in html
        assert "collection_exhausted" not in html
        assert str(actor_id) not in html
        assert "rating" not in html.casefold()

    form_page = staff_client.get(f"{history_path}/new")
    form_html = unescape(form_page.text)
    assert form_page.status_code == 200
    assert form_page.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert _hidden(form_html, "expected_balance_basis") == "original"
    assert 'max="1000"' in form_html
    assert "Undirishdan chiqarilgan qarz qoldig‘i" in form_html

    created = staff_client.post(
        history_path,
        data=_form(form_html, amount="400"),
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"].startswith("/shop/payments/")
    receipt_path = created.headers["location"]
    receipt = staff_client.get(receipt_path)
    assert receipt.status_code == 200
    assert "Undirishdan chiqarilgan" in unescape(receipt.text)
    assert "600" in receipt.text

    customer_debt_paths = (
        "/customer/debts",
        f"/customer/debts/{debt_id}",
        f"/customer/debts/{debt_id}/payments",
        receipt_path.replace("/shop/", "/customer/"),
    )
    for path in customer_debt_paths:
        page = customer_client.get(path, headers={"Accept-Language": "ru"})
        assert page.status_code == 200
        assert page.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
        assert "Списан для взыскания" in page.text
        assert "collection_exhausted" not in page.text
        assert str(actor_id) not in page.text
        assert "rating" not in page.text.casefold()
        assert "<form" not in page.text.casefold()
    customer_history = customer_client.get(f"/customer/debts/{debt_id}/payments")
    assert "Mijoz to‘lov kiritolmaydi" in unescape(customer_history.text)

    second_page = staff_client.get(f"{history_path}/new")
    settled = staff_client.post(
        history_path,
        data=_form(unescape(second_page.text), amount="600"),
        follow_redirects=False,
    )
    assert settled.status_code == 303
    settled_receipt = staff_client.get(settled.headers["location"])
    assert "Undirish qarzi yopilgan" in unescape(settled_receipt.text)
    with Session(m2_test_database) as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "written_off_settled"
        assert debt.paid_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.debt_id == debt_id)
            )
            == 2
        )

    settled_history = staff_client.get(history_path)
    assert f"{history_path}/new" not in settled_history.text
    direct_settled = staff_client.get(f"{history_path}/new", follow_redirects=False)
    assert direct_settled.status_code == 303
    assert direct_settled.headers["location"] == (
        f"/shop/debts/{debt_id}?error=DEBT_NOT_PAYABLE"
    )


def test_suspended_historical_reads_remain_safe_but_new_recovery_and_foreign_reads_deny(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(payment_service, "_utc_now", lambda: NOW)
    actor_id, shop_id, debt_id = _seed_written_off_source(m2_test_database)
    staff_client, _settings = _client(
        m2_test_database, actor_id=actor_id, shop_id=shop_id
    )
    history_path = f"/shop/debts/{debt_id}/payments"
    form_page = staff_client.get(f"{history_path}/new")
    initial_form = _form(unescape(form_page.text), amount="400")
    partial = staff_client.post(
        history_path,
        data=initial_form,
        follow_redirects=False,
    )
    receipt_path = partial.headers["location"]

    with Session(m2_test_database) as session, session.begin():
        shop = session.get_one(Shop, shop_id)
        shop.status = "suspended"
    history = staff_client.get(history_path)
    receipt = staff_client.get(receipt_path)
    assert history.status_code == receipt.status_code == 200
    assert history.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert receipt.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert f"{history_path}/new" not in history.text
    denied_new = staff_client.get(f"{history_path}/new", follow_redirects=False)
    assert denied_new.headers["location"] == (
        f"/shop/debts/{debt_id}?error=SHOP_SUSPENDED"
    )
    suspended_post = staff_client.post(
        history_path,
        data={
            **initial_form,
            "idempotency_key": str(uuid4()),
            "expected_revision": "5",
            "amount_uzs": "1",
        },
        follow_redirects=False,
    )
    assert suspended_post.headers["location"].endswith("?error=SHOP_SUSPENDED")
    with Session(m2_test_database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.debt_id == debt_id)
            )
            == 1
        )

    with Session(m2_test_database) as session, session.begin():
        foreign_actor, foreign_shop, _foreign_staff = _add_shop_actor(
            session, role=ShopRole.CASHIER
        )
        foreign_actor_id = foreign_actor.id
        foreign_shop_id = foreign_shop.id
        staff = session.scalar(
            select(ShopStaff).where(
                ShopStaff.shop_id == shop_id, ShopStaff.user_id == actor_id
            )
        )
        assert staff is not None
        staff.is_active = False
        staff.revoked_at = NOW
        session.get_one(Shop, shop_id).status = "active"

    foreign_client, _settings = _client(
        m2_test_database, actor_id=foreign_actor_id, shop_id=foreign_shop_id
    )
    foreign = foreign_client.get(history_path, follow_redirects=False)
    assert foreign.status_code == 303
    assert foreign.headers["location"] == "/shop/customers?error=DEBT_UNAVAILABLE"
    foreign_post = foreign_client.post(
        history_path,
        data={
            **initial_form,
            "csrf_token": _csrf_for_actor(m2_test_database, actor_id=foreign_actor_id),
            "idempotency_key": str(uuid4()),
            "expected_revision": "5",
            "amount_uzs": "1",
        },
        follow_redirects=False,
    )
    assert foreign_post.status_code == 303
    assert foreign_post.headers["location"].endswith("?error=DEBT_UNAVAILABLE")

    revoked = staff_client.get(history_path, follow_redirects=False)
    assert revoked.status_code == 303
    assert "DEBT_UNAVAILABLE" not in revoked.headers["location"]
    assert str(debt_id) not in revoked.headers["location"]
    revoked_post = staff_client.post(
        history_path,
        data={
            **initial_form,
            "idempotency_key": str(uuid4()),
            "expected_revision": "5",
            "amount_uzs": "1",
        },
        follow_redirects=False,
    )
    assert revoked_post.status_code == 303
    assert revoked_post.headers["location"] == "/shop/select"
    with Session(m2_test_database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.debt_id == debt_id)
            )
            == 1
        )
