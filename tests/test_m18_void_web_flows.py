from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.auth.models import User
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.debt.presentation import DebtWebLanguage
from app.idempotency.models import IdempotencyKey
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.models import Payment, PaymentVoid
from app.payment.repository import (
    get_tenant_payment,
    latest_non_voided_payment_for_tenant_debt,
)
from app.payment.router import _can_navigate_to_payment_void
from app.payment.values import PaymentId
from app.payment.void_targeting import discover_tenant_payment_void_target
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop.repository import get_shop_staff_access
from app.shop.values import ShopId, UserId
from app.shop_customer.values import ShopCustomerId
from tests.test_payment_read_postgresql import _seed_read_graph
from tests.test_payment_web_flows import _client, _form, _hidden

pytestmark = pytest.mark.integration

VOIDED_AT = datetime(2026, 8, 14, 9, tzinfo=UTC)


def _void_form(html: str) -> dict[str, str]:
    return {
        "csrf_token": _hidden(html, "csrf_token"),
        "idempotency_key": _hidden(html, "idempotency_key"),
        "expected_revision": _hidden(html, "expected_revision"),
        "reason": "wrong_debt",
        "confirmation": "yes",
    }


def test_shop_void_prg_replay_and_customer_projection_are_privacy_separated(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _seed_read_graph(m2_test_database, discounted="1000")
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        staff = session.get_one(ShopStaff, seed.staff_id)
        staff.role = ShopRole.OWNER.value

    shop_client, _settings = _client(
        m2_test_database,
        actor_id=seed.actor_id,
        shop_id=seed.shop_id,
    )
    create_path = f"/shop/debts/{seed.debt_id}/payments"
    create_page = unescape(shop_client.get(f"{create_path}/new").text)
    created = shop_client.post(
        create_path,
        data=_form(create_page, amount="400"),
        follow_redirects=False,
    )
    assert created.status_code == 303
    receipt_path = created.headers["location"]
    payment_id = UUID(receipt_path.rsplit("/", 1)[1])

    with factory() as session:
        actor = DetachedPaymentActorContext(
            actor_user_id=seed.actor_id,
            current_shop_id=seed.shop_id,
            role_hint=ShopRole.OWNER,
            language=DebtWebLanguage.UZ_LATN,
        )
        payment = PaymentId(payment_id)
        access = get_shop_staff_access(
            session, shop_id=ShopId(seed.shop_id), user_id=UserId(seed.actor_id)
        )
        candidate = discover_tenant_payment_void_target(
            session, actor=actor, payment_id=payment
        )
        row = get_tenant_payment(
            session, shop_id=ShopId(seed.shop_id), payment_id=payment
        )
        assert access is not None and access.role is ShopRole.OWNER and access.is_live
        assert candidate is not None and row is not None
        latest = latest_non_voided_payment_for_tenant_debt(
            session,
            shop_id=ShopId(seed.shop_id),
            shop_customer_id=ShopCustomerId(row.debt.shop_customer_id),
            debt_id=candidate.debt_id,
        )
        assert latest is not None and latest.id == payment
        assert _can_navigate_to_payment_void(session, actor=actor, payment_id=payment)

    receipt_before = unescape(shop_client.get(receipt_path).text)
    assert f'/shop/payments/{payment_id}/void"' in receipt_before
    history_before = shop_client.get(f"/shop/debts/{seed.debt_id}/payments").text
    assert f'/shop/payments/{payment_id}/void"' in history_before
    void_path = f"/shop/payments/{payment_id}/void"
    void_page = shop_client.get(void_path)
    void_html = unescape(void_page.text)
    assert void_page.status_code == 200
    assert void_page.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert _hidden(void_html, "expected_revision") == "3"
    assert re.findall(r'<option value="([a-z_]+)">', void_html) == [
        "duplicate_payment",
        "incorrect_amount",
        "incorrect_method",
        "payment_not_received",
        "wrong_debt",
    ]
    assert str(seed.actor_id) not in void_html

    shop_client.app.state.payment_void_clock = lambda: VOIDED_AT
    form = _void_form(void_html)
    first = shop_client.post(void_path, data=form, follow_redirects=False)
    replay = shop_client.post(void_path, data=form, follow_redirects=False)
    assert first.status_code == replay.status_code == 303
    assert first.headers["location"] == replay.headers["location"] == receipt_path

    shop_receipt = unescape(shop_client.get(receipt_path).text)
    assert "Noto'g'ri qarz" in shop_receipt
    assert "Ushbu to'lovdan keyingi qoldiq</dt><dd>600" in shop_receipt
    assert "Hozirgi qoldiq</dt><dd>1000" in shop_receipt
    assert "wrong_debt" not in shop_receipt
    assert str(seed.actor_id) not in shop_receipt
    shop_history = unescape(shop_client.get(create_path).text)
    assert "Bekor qilingan: Noto'g'ri qarz" in shop_history
    assert "To'langan jami</dt><dd>0" in shop_history
    assert "Hozirgi qoldiq" in shop_history

    customer_client, _settings = _client(
        m2_test_database,
        actor_id=seed.customer_user_id,
        shop_id=seed.shop_id,
    )
    customer_receipt = unescape(
        customer_client.get(f"/customer/payments/{payment_id}").text
    )
    assert "Bekor qilingan" in customer_receipt
    assert "Noto'g'ri qarz" not in customer_receipt
    assert "wrong_debt" not in customer_receipt
    assert str(seed.actor_id) not in customer_receipt
    customer_history = unescape(
        customer_client.get(f"/customer/debts/{seed.debt_id}/payments").text
    )
    assert "Bekor qilingan" in customer_history
    assert "Noto'g'ri qarz" not in customer_history
    assert "wrong_debt" not in customer_history
    assert "To'langan jami</dt><dd>0" in customer_history

    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        assert debt.status == "active" and debt.revision == 4
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(PaymentVoid)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 2
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2


def test_void_navigation_is_live_role_and_latest_payment_scoped(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(m2_test_database, discounted="1000")
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        staff = session.get_one(ShopStaff, seed.staff_id)
        staff.role = ShopRole.OWNER.value
    client, _settings = _client(
        m2_test_database, actor_id=seed.actor_id, shop_id=seed.shop_id
    )
    create_path = f"/shop/debts/{seed.debt_id}/payments"
    first_page = unescape(client.get(f"{create_path}/new").text)
    first = client.post(
        create_path, data=_form(first_page, amount="400"), follow_redirects=False
    )
    first_payment_id = UUID(first.headers["location"].rsplit("/", 1)[1])
    second_page = unescape(client.get(f"{create_path}/new").text)
    second = client.post(
        create_path, data=_form(second_page, amount="100"), follow_redirects=False
    )
    second_payment_id = UUID(second.headers["location"].rsplit("/", 1)[1])

    first_receipt = client.get(first.headers["location"]).text
    second_receipt = client.get(second.headers["location"]).text
    history = client.get(f"/shop/debts/{seed.debt_id}/payments").text
    assert f"/shop/payments/{first_payment_id}/void" not in first_receipt
    assert f"/shop/payments/{first_payment_id}/void" not in history
    assert f"/shop/payments/{second_payment_id}/void" in second_receipt
    assert f"/shop/payments/{second_payment_id}/void" in history

    with factory.begin() as session:
        staff = session.get_one(ShopStaff, seed.staff_id)
        staff.role = ShopRole.CASHIER.value

    denied_receipt = client.get(second.headers["location"])
    denied_form = client.get(
        f"/shop/payments/{second_payment_id}/void", follow_redirects=False
    )
    assert f"/shop/payments/{second_payment_id}/void" not in denied_receipt.text
    assert denied_form.status_code == 303
    assert denied_form.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL

    with factory.begin() as session:
        session.get_one(User, seed.actor_id).is_platform_admin = True
    admin_without_role = client.get(
        f"/shop/payments/{second_payment_id}/void", follow_redirects=False
    )
    assert admin_without_role.status_code == 303

    with factory.begin() as session:
        session.get_one(ShopStaff, seed.staff_id).role = ShopRole.OWNER.value
    dual_role = client.get(f"/shop/payments/{second_payment_id}/void")
    assert dual_role.status_code == 200

    with factory.begin() as session:
        session.get_one(Shop, seed.shop_id).status = ShopStatus.SUSPENDED.value
    historical_receipt = client.get(second.headers["location"])
    suspended_form = client.get(
        f"/shop/payments/{second_payment_id}/void", follow_redirects=False
    )
    assert historical_receipt.status_code == 200
    assert "100" in historical_receipt.text
    assert f"/shop/payments/{second_payment_id}/void" not in historical_receipt.text
    assert suspended_form.status_code == 303


def test_void_get_refresh_and_invalid_posts_are_mutation_free_and_redacted(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(m2_test_database, discounted="1000")
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        session.get_one(ShopStaff, seed.staff_id).role = ShopRole.MANAGER.value
    client, _settings = _client(
        m2_test_database, actor_id=seed.actor_id, shop_id=seed.shop_id
    )
    create_path = f"/shop/debts/{seed.debt_id}/payments"
    create_page = unescape(client.get(f"{create_path}/new").text)
    created = client.post(
        create_path,
        data=_form(create_page, amount="400"),
        follow_redirects=False,
    )
    payment_id = UUID(created.headers["location"].rsplit("/", 1)[1])
    void_path = f"/shop/payments/{payment_id}/void"

    first_get = unescape(client.get(void_path).text)
    second_get = unescape(client.get(void_path).text)
    first_key = _hidden(first_get, "idempotency_key")
    second_key = _hidden(second_get, "idempotency_key")
    assert first_key != second_key
    assert str(UUID(first_key)) == first_key
    assert str(UUID(second_key)) == second_key
    assert first_key not in void_path
    assert "payment-void-warning" in first_get
    assert 'class="button-danger" type="submit"' in first_get
    assert 'id="payment-void-confirmation"' in first_get

    invalid = _void_form(first_get)
    invalid["confirmation"] = ""
    rejected = client.post(void_path, data=invalid, follow_redirects=False)
    assert rejected.status_code == 303
    assert rejected.headers["location"].endswith("error=VALIDATION_ERROR")
    assert first_key not in rejected.headers["location"]

    csrf_failed = _void_form(first_get)
    csrf_failed["csrf_token"] = "invalid"
    denied = client.post(void_path, data=csrf_failed, follow_redirects=False)
    assert denied.status_code == 403
    assert first_key not in denied.text

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentVoid)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1


def test_voiding_latest_exposes_preceding_stack_item_on_a_fresh_read(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(m2_test_database, discounted="1000")
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        session.get_one(ShopStaff, seed.staff_id).role = ShopRole.OWNER.value
    client, _settings = _client(
        m2_test_database, actor_id=seed.actor_id, shop_id=seed.shop_id
    )
    create_path = f"/shop/debts/{seed.debt_id}/payments"
    first_page = unescape(client.get(f"{create_path}/new").text)
    first = client.post(
        create_path, data=_form(first_page, amount="400"), follow_redirects=False
    )
    first_id = UUID(first.headers["location"].rsplit("/", 1)[1])
    second_page = unescape(client.get(f"{create_path}/new").text)
    second = client.post(
        create_path, data=_form(second_page, amount="100"), follow_redirects=False
    )
    second_id = UUID(second.headers["location"].rsplit("/", 1)[1])
    void_path = f"/shop/payments/{second_id}/void"
    void_html = unescape(client.get(void_path).text)
    client.app.state.payment_void_clock = lambda: VOIDED_AT
    response = client.post(
        void_path, data=_void_form(void_html), follow_redirects=False
    )
    assert response.status_code == 303

    history = unescape(client.get(create_path).text)
    first_receipt = unescape(client.get(first.headers["location"]).text)
    assert f'/shop/payments/{first_id}/void"' in history
    assert f'/shop/payments/{first_id}/void"' in first_receipt
    assert f'/shop/payments/{second_id}/void"' not in history


@pytest.mark.parametrize("role", (ShopRole.OWNER, ShopRole.MANAGER))
def test_owner_and_manager_receive_localized_five_reason_form(
    m2_test_database: Engine,
    role: ShopRole,
) -> None:
    seed = _seed_read_graph(m2_test_database, discounted="1000")
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        session.get_one(ShopStaff, seed.staff_id).role = role.value
    client, _settings = _client(
        m2_test_database, actor_id=seed.actor_id, shop_id=seed.shop_id
    )
    create_path = f"/shop/debts/{seed.debt_id}/payments"
    created = client.post(
        create_path,
        data=_form(unescape(client.get(f"{create_path}/new").text), amount="400"),
        follow_redirects=False,
    )
    payment_id = UUID(created.headers["location"].rsplit("/", 1)[1])
    uz = unescape(client.get(f"/shop/payments/{payment_id}/void").text)
    ru = unescape(
        client.get(
            f"/shop/payments/{payment_id}/void",
            headers={"Accept-Language": "ru"},
        ).text
    )
    assert all(
        label in uz
        for label in (
            "Takroriy to'lov",
            "Noto'g'ri summa",
            "Noto'g'ri usul",
            "To'lov olinmagan",
            "Noto'g'ri qarz",
        )
    )
    assert all(
        label in ru
        for label in (
            "Повторный платёж",
            "Неверная сумма",
            "Неверный способ",
            "Платёж не получен",
            "Неверный долг",
        )
    )
