from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.auth.deps import get_current_time
from app.auth.error_codes import ErrorCode
from app.auth.sessions import create_authenticated_session
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.idempotency.models import IdempotencyKey
from app.main import create_app
from app.payment.models import Payment
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL
from app.settings import Settings
from app.shop.models import Shop
from tests.test_payment_read_postgresql import _record, _seed_read_graph
from tests.test_payment_targeting_postgresql import _seed_one

pytestmark = pytest.mark.integration

PAYMENT_TIME = datetime(2026, 8, 10, 12, tzinfo=UTC)
RATE_KEY = "test-rate-limit-hmac-key-for-m14-payment-web"


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
    application.dependency_overrides[get_current_time] = lambda: PAYMENT_TIME
    client = TestClient(application, client=("127.0.0.1", 51014))
    factory = create_database_session_factory(engine)
    with factory.begin() as db:
        created = create_authenticated_session(
            db,
            actor_id,
            "pytest-m14-payment-web",
            PAYMENT_TIME,
            settings=settings,
        )
        created.session.active_shop_id = shop_id
        db.flush()
        cookie = created.raw_token.as_cookie_value()
    client.cookies.set(
        settings.session_cookie_name,
        cookie,
        domain="testserver.local",
        path="/",
    )
    return client, settings


def _hidden(html: str, name: str) -> str:
    matched = re.search(rf'name="{re.escape(name)}" value="([^"]+)"', html)
    assert matched is not None
    return matched.group(1)


def _form(html: str, *, amount: str) -> dict[str, str]:
    return {
        "csrf_token": _hidden(html, "csrf_token"),
        "idempotency_key": _hidden(html, "idempotency_key"),
        "expected_revision": _hidden(html, "expected_revision"),
        "amount_uzs": amount,
        "method": "cash",
    }


def _counts(engine: Engine) -> tuple[int, int, int]:
    factory = create_database_session_factory(engine)
    with factory() as db:
        return (
            int(db.scalar(select(func.count()).select_from(Payment))),
            int(db.scalar(select(func.count()).select_from(IdempotencyKey))),
            int(db.scalar(select(func.count()).select_from(AuditLog))),
        )


def test_shop_payment_form_records_once_and_replay_uses_same_receipt_prg(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    client, _settings = _client(m2_test_database, actor_id=actor_id, shop_id=shop_id)
    new_path = f"/shop/debts/{debt_id}/payments/new"

    page = client.get(new_path)
    html = unescape(page.text)
    assert page.status_code == 200
    assert page.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert _hidden(html, "expected_revision") == "2"
    assert UUID(_hidden(html, "idempotency_key")).version == 4
    assert 'max="1000"' in html
    assert '<html lang="uz">' in html
    assert '<label for="payment-amount">' in html
    assert '<label for="payment-method">' in html
    assert 'id="payment-form-error" role="alert" aria-live="assertive"' in html
    assert 'aria-describedby="payment-balance payment-form-error"' in html
    assert 'aria-invalid="false"' in html
    assert re.findall(r'<option value="([a-z]+)">', html) == [
        "cash",
        "card",
        "transfer",
        "other",
    ]
    form = _form(html, amount="400")

    russian_page = client.get(new_path, headers={"Accept-Language": "ru"})
    assert '<html lang="ru">' in russian_page.text
    assert "Сумма платежа" in russian_page.text
    assert "Остаток долга" in russian_page.text

    created = client.post(
        f"/shop/debts/{debt_id}/payments",
        data=form,
        headers={"Idempotency-Key": form["idempotency_key"]},
        follow_redirects=False,
    )
    replay = client.post(
        f"/shop/debts/{debt_id}/payments",
        data=form,
        headers={"Idempotency-Key": form["idempotency_key"]},
        follow_redirects=False,
    )

    assert created.status_code == replay.status_code == 303
    assert created.headers["location"] == replay.headers["location"]
    assert created.headers["location"].startswith("/shop/payments/")
    assert created.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert _counts(m2_test_database) == (1, 1, 1)

    receipt = client.get(created.headers["location"])
    refreshed = client.get(created.headers["location"])
    assert receipt.status_code == refreshed.status_code == 200
    assert receipt.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert _counts(m2_test_database) == (1, 1, 1)
    factory = create_database_session_factory(m2_test_database)
    with factory() as db:
        debt = db.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == "active" and debt.revision == 3


def test_validation_stale_overpay_and_csrf_fail_through_safe_results(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    client, _settings = _client(m2_test_database, actor_id=actor_id, shop_id=shop_id)
    post_path = f"/shop/debts/{debt_id}/payments"
    first_page = unescape(client.get(f"{post_path}/new").text)
    first_form = _form(first_page, amount="400")

    mismatch = client.post(
        post_path,
        data=first_form,
        headers={"Idempotency-Key": str(uuid4())},
        follow_redirects=False,
    )
    assert mismatch.headers["location"].endswith("?error=VALIDATION_ERROR")
    assert _counts(m2_test_database) == (0, 0, 0)

    created = client.post(post_path, data=first_form, follow_redirects=False)
    assert created.headers["location"].startswith("/shop/payments/")
    assert _counts(m2_test_database) == (1, 1, 1)

    stale_form = {**first_form, "idempotency_key": str(uuid4()), "amount_uzs": "1"}
    stale = client.post(post_path, data=stale_form, follow_redirects=False)
    assert stale.headers["location"].endswith("?error=DEBT_CHANGED")
    stale_page = client.get(
        stale.headers["location"], headers={"Accept-Language": "ru"}
    )
    assert "Долг изменился" in stale_page.text
    assert stale_form["idempotency_key"] not in stale.headers["location"]
    assert _counts(m2_test_database) == (1, 1, 1)

    current_page = unescape(client.get(f"{post_path}/new").text)
    over_form = _form(current_page, amount="601")
    over = client.post(post_path, data=over_form, follow_redirects=False)
    assert over.headers["location"].endswith("?error=PAYMENT_AMOUNT_EXCEEDS_BALANCE")
    over_page = client.get(over.headers["location"])
    assert "qolgan qarzdan oshadi" in over_page.text
    assert "601" not in over.headers["location"]
    assert _counts(m2_test_database) == (1, 1, 1)

    forged = client.post(
        post_path,
        data={**over_form, "csrf_token": "forged"},
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert forged.status_code == 403
    assert forged.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value
    assert forged.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert _counts(m2_test_database) == (1, 1, 1)


@pytest.mark.parametrize(
    ("state", "expected_error"),
    (
        ("suspended", ErrorCode.SHOP_SUSPENDED),
        ("past_due", ErrorCode.DEBT_NOT_PAYABLE),
        ("paid", ErrorCode.DEBT_NOT_PAYABLE),
    ),
)
def test_nonpayable_controls_are_hidden_and_direct_form_get_is_denied(
    m2_test_database: Engine,
    state: str,
    expected_error: ErrorCode,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as db:
        shop = db.get(Shop, shop_id)
        debt = db.get(Debt, debt_id)
        assert shop is not None and debt is not None
        if state == "suspended":
            shop.status = "suspended"
        elif state == "past_due":
            debt.due_date = date(2026, 8, 9)
            assert debt.accepted_at is not None
            debt.updated_at = debt.accepted_at + timedelta(hours=1)
        else:
            debt.status = "paid"
            debt.paid_at = PAYMENT_TIME
            debt.updated_at = PAYMENT_TIME

    client, _settings = _client(m2_test_database, actor_id=actor_id, shop_id=shop_id)
    history_path = f"/shop/debts/{debt_id}/payments"
    history = client.get(history_path)
    assert history.status_code == 200
    assert f'{history_path}/new"' not in history.text

    debt_detail = client.get(f"/shop/debts/{debt_id}")
    assert debt_detail.status_code == 200
    assert f'{history_path}/new"' not in debt_detail.text

    direct = client.get(f"{history_path}/new", follow_redirects=False)
    assert direct.status_code == 303
    assert direct.headers["location"] == (
        f"/shop/debts/{debt_id}?error={expected_error.value}"
    )
    assert direct.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL


def test_shop_history_and_receipt_render_authoritative_balances_without_leaks(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(m2_test_database, discounted="900")
    first = _record(
        m2_test_database, seed, amount="300", revision=2, key=uuid4(), method="card"
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as db:
        shop = db.get(Shop, seed.shop_id)
        assert shop is not None
        shop.name = "<script>alert(1)</script> Safe Shop"

    client, _settings = _client(
        m2_test_database, actor_id=seed.actor_id, shop_id=seed.shop_id
    )
    history = client.get(
        f"/shop/debts/{seed.debt_id}/payments", headers={"Accept-Language": "ru"}
    )
    html = unescape(history.text)
    assert history.status_code == 200
    assert history.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert "default-src 'self'" in history.headers["content-security-policy"]
    assert "Сумма долга со скидкой" in html and "900" in html
    assert "Всего оплачено" in html and "300" in html
    assert "Остаток долга" in html and "600" in html
    assert "Активен" in html and "Карта" in html
    assert f"/shop/payments/{first.payment_id.as_uuid()}" in html
    assert f"/shop/debts/{seed.debt_id}/payments/new" in html
    assert str(seed.actor_id) not in html
    assert "recorded_by" not in html and "idempotency" not in html

    receipt = client.get(
        f"/shop/payments/{first.payment_id.as_uuid()}",
        headers={"Accept-Language": "ru"},
    )
    rendered = receipt.text
    assert receipt.status_code == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt; Safe Shop" in rendered
    assert "<script>alert(1)</script>" not in rendered
    assert "Карта" in rendered and "600" in rendered
    assert f"/shop/debts/{seed.debt_id}/payments" in rendered
    for forbidden in ("recorded_by", "idempotency", "hash", "print", "pdf"):
        assert forbidden not in rendered.casefold()

    _record(m2_test_database, seed, amount="600", revision=3, key=uuid4())
    later = client.get(f"/shop/payments/{first.payment_id.as_uuid()}")
    later_html = unescape(later.text)
    assert "600" in later_html and "0" in later_html and "To'langan" in later_html


def test_shop_foreign_receipt_and_customer_history_receipt_are_private_and_read_only(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(m2_test_database, discounted="900")
    first = _record(
        m2_test_database, seed, amount="300", revision=2, key=uuid4(), method="cash"
    )
    other = _seed_read_graph(m2_test_database)
    other_shop_client, _settings = _client(
        m2_test_database, actor_id=other.actor_id, shop_id=other.shop_id
    )
    foreign = other_shop_client.get(
        f"/shop/payments/{first.payment_id.as_uuid()}", follow_redirects=False
    )
    assert foreign.status_code == 303
    assert foreign.headers["location"].endswith("?error=PAYMENT_UNAVAILABLE")
    assert str(first.payment_id.as_uuid()) not in foreign.headers["location"]

    customer_client, _settings = _client(
        m2_test_database,
        actor_id=seed.customer_user_id,
        shop_id=seed.shop_id,
    )
    history = customer_client.get(
        f"/customer/debts/{seed.debt_id}/payments",
        headers={"Accept-Language": "ru"},
    )
    html = history.text
    assert history.status_code == 200
    assert history.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert (
        '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    )
    assert "Сумма долга со скидкой" in html and "900" in html
    assert "Всего оплачено" in html and "300" in html
    assert "Наличные" in html
    assert "<form" not in html.casefold()
    assert "void" not in html.casefold()
    assert "recorded_by" not in html and "idempotency" not in html

    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as db:
        shop = db.get(Shop, seed.shop_id)
        assert shop is not None
        shop.status = "suspended"
    suspended_history = customer_client.get(f"/customer/debts/{seed.debt_id}/payments")
    assert suspended_history.status_code == 200

    receipt = customer_client.get(f"/customer/payments/{first.payment_id.as_uuid()}")
    assert receipt.status_code == 200
    assert f"/customer/debts/{seed.debt_id}/payments" in receipt.text
    for forbidden in ("recorded_by", "idempotency", "hash", "bank", "terminal"):
        assert forbidden not in receipt.text.casefold()

    foreign_customer_client, _settings = _client(
        m2_test_database,
        actor_id=other.customer_user_id,
        shop_id=other.shop_id,
    )
    foreign_customer = foreign_customer_client.get(
        f"/customer/payments/{first.payment_id.as_uuid()}", follow_redirects=False
    )
    assert foreign_customer.status_code == 303
    assert foreign_customer.headers["location"].endswith("?error=PAYMENT_UNAVAILABLE")
