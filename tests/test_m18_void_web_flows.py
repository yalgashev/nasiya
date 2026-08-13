from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

import app.payment.void_service as void_service_module
from app.audit.models import AuditLog
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.idempotency.models import IdempotencyKey
from app.payment.models import Payment, PaymentVoid
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL
from app.shop.enums import ShopRole
from app.shop.models import ShopStaff
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

    receipt_before = unescape(shop_client.get(receipt_path).text)
    assert f'/shop/payments/{payment_id}/void"' in receipt_before
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

    monkeypatch.setattr(void_service_module, "_utc_now", lambda: VOIDED_AT)
    form = _void_form(void_html)
    first = shop_client.post(void_path, data=form, follow_redirects=False)
    replay = shop_client.post(void_path, data=form, follow_redirects=False)
    assert first.status_code == replay.status_code == 303
    assert first.headers["location"] == replay.headers["location"] == receipt_path

    shop_receipt = unescape(shop_client.get(receipt_path).text)
    assert "Noto'g'ri qarz" in shop_receipt
    assert "wrong_debt" not in shop_receipt
    assert str(seed.actor_id) not in shop_receipt

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

    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        assert debt.status == "active" and debt.revision == 4
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(PaymentVoid)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 2
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2
