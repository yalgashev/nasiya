from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from html import unescape

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.debt.enums import DebtStatus
from app.debt.models import Debt
from app.debt.router import router as debt_router
from app.idempotency.models import IdempotencyKey
from app.offers.enums import OfferLanguage
from app.offers.models import OfferAcceptance, OfferText
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL
from app.shop_customer.models import ShopCustomer
from tests.test_debt_creation_gates_postgresql import _add_complete_offer
from tests.test_shop_customer_web_flows import (
    NOW,
    _client,
    _csrf,
    _seed,
    _session,
)

pytestmark = pytest.mark.integration

EXPECTED_ROUTES = {
    ("GET", "/shop/customers/{shop_customer_id}/debts"),
    ("GET", "/shop/customers/{shop_customer_id}/debts/new"),
    ("POST", "/shop/customers/{shop_customer_id}/debts"),
    ("GET", "/shop/debts/{debt_id}"),
    ("POST", "/shop/debts/{debt_id}/cancel"),
    ("GET", "/customer/debts"),
    ("GET", "/customer/debts/{debt_id}"),
    ("POST", "/customer/debts/{debt_id}/accept"),
    ("POST", "/customer/debts/{debt_id}/reject"),
}


def _linked_customer(db: Session, *, add_offer: bool = True):
    actor, shop, target, customer = _seed(db)
    linked = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        created_by_user_id=actor.id,
        credit_limit_uzs=1000,
        max_open_debts=3,
        list_status="normal",
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(linked)
    db.flush()
    if add_offer:
        _add_complete_offer(db, actor=actor)
    return actor, shop, target, linked


def _create_form(*, csrf: str, key: str, amount: str = "100") -> dict[str, str]:
    return {
        "csrf_token": csrf,
        "idempotency_key": key,
        "original_amount_uzs": amount,
        "discount_percent": "5",
        "due_date": date(2026, 8, 12).isoformat(),
    }


def test_exact_nine_debt_routes_are_registered_once(m2_test_database: Engine) -> None:
    observed = []
    for route in debt_router.routes:
        if not isinstance(route, APIRoute):
            continue
        observed.extend((method, route.path) for method in route.methods)
    assert set(observed) == EXPECTED_ROUTES
    assert len(observed) == len(EXPECTED_ROUTES)


def test_shop_create_list_detail_replay_conflict_and_no_store(
    m2_test_database: Engine,
) -> None:
    factory = __import__("app.db", fromlist=["create_database_session_factory"])
    session_factory = factory.create_database_session_factory(m2_test_database)
    client, settings = _client(m2_test_database)
    with session_factory() as db:
        actor, shop, _target, linked = _linked_customer(db)
        created = _session(db, client, settings, actor, shop)
        csrf = _csrf(created)
        linked_id = linked.id

    new_page = client.get(f"/shop/customers/{linked_id}/debts/new")
    assert new_page.status_code == 200
    assert new_page.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    key_match = re.search(
        r'name="idempotency_key" value="([0-9a-f-]{36})"', new_page.text
    )
    assert key_match is not None
    key = key_match.group(1)
    form = _create_form(csrf=csrf, key=key)

    first = client.post(
        f"/shop/customers/{linked_id}/debts",
        data=form,
        follow_redirects=False,
    )
    replay = client.post(
        f"/shop/customers/{linked_id}/debts",
        data=form,
        follow_redirects=False,
    )
    conflict = client.post(
        f"/shop/customers/{linked_id}/debts",
        data=_create_form(csrf=csrf, key=key, amount="101"),
        follow_redirects=False,
    )

    assert first.status_code == replay.status_code == conflict.status_code == 303
    assert first.headers["location"] == replay.headers["location"]
    assert first.headers["location"].startswith("/shop/debts/")
    assert conflict.headers["location"].endswith("?error=IDEMPOTENCY_CONFLICT")
    assert key not in first.headers["location"]
    assert key not in conflict.headers["location"]
    detail = client.get(first.headers["location"])
    listing = client.get(f"/shop/customers/{linked_id}/debts")
    assert detail.status_code == listing.status_code == 200
    assert detail.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert listing.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    with session_factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(Debt)) == 1
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == AuditEventType.DEBT_CREATED.value)
            )
            == 1
        )


def test_customer_legal_accept_prg_replay_and_foreign_locator_are_safe(
    m2_test_database: Engine,
) -> None:
    from app.db import create_database_session_factory

    session_factory = create_database_session_factory(m2_test_database)
    client, settings = _client(m2_test_database)
    with session_factory() as db:
        actor, shop, target, linked = _linked_customer(db)
        staff_session = _session(db, client, settings, actor, shop)
        staff_csrf = _csrf(staff_session)
        linked_id = linked.id
        target_id = target.id
        shop_id = shop.id
    create_page = client.get(f"/shop/customers/{linked_id}/debts/new")
    key = re.search(r'name="idempotency_key" value="([0-9a-f-]{36})"', create_page.text)
    assert key is not None
    created = client.post(
        f"/shop/customers/{linked_id}/debts",
        data=_create_form(csrf=staff_csrf, key=key.group(1)),
        follow_redirects=False,
    )
    debt_id = created.headers["location"].split("/", 3)[-1].split("?", 1)[0]

    client.cookies.clear()
    with session_factory() as db:
        fresh_target = db.get(type(target), target_id)
        fresh_shop = db.get(type(shop), shop_id)
        assert fresh_target is not None and fresh_shop is not None
        customer_session = _session(db, client, settings, fresh_target, fresh_shop)
        customer_csrf = _csrf(customer_session)
        offer_text_id = db.scalar(
            select(OfferText.id).where(
                OfferText.language == OfferLanguage.UZ_LATN.value
            )
        )
        assert offer_text_id is not None

    listing = client.get("/customer/debts")
    detail = client.get(f"/customer/debts/{debt_id}")
    assert listing.status_code == detail.status_code == 200
    html = unescape(detail.text)
    assert "Terms UZ_LATN" in html
    assert "<script" not in detail.text
    form = {
        "csrf_token": customer_csrf,
        "expected_revision": "1",
        "language": OfferLanguage.UZ_LATN.value,
        "displayed_offer_text_id": str(offer_text_id),
    }
    accepted = client.post(
        f"/customer/debts/{debt_id}/accept",
        data=form,
        follow_redirects=False,
    )
    replay = client.post(
        f"/customer/debts/{debt_id}/accept",
        data=form,
        follow_redirects=False,
    )
    assert accepted.headers["location"].endswith("?notice=accepted")
    assert replay.headers["location"].endswith("?notice=accepted")
    with session_factory.begin() as db:
        debt = db.get(Debt, debt_id)
        assert debt is not None and debt.status == DebtStatus.ACTIVE.value
        assert db.scalar(select(func.count()).select_from(OfferAcceptance)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == AuditEventType.DEBT_ACCEPTED.value)
            )
            == 1
        )

    with session_factory() as db:
        _other_actor, other_shop, other_target, _other_linked = _linked_customer(
            db, add_offer=False
        )
        other_target_id = other_target.id
        other_shop_id = other_shop.id
        db.commit()
        fresh_other_target = db.get(type(other_target), other_target_id)
        fresh_other_shop = db.get(type(other_shop), other_shop_id)
        assert fresh_other_target is not None and fresh_other_shop is not None
        _session(db, client, settings, fresh_other_target, fresh_other_shop)
    foreign = client.get(f"/customer/debts/{debt_id}", follow_redirects=False)
    assert foreign.status_code == 303
    assert foreign.headers["location"] == "/customer/debts?error=DEBT_UNAVAILABLE"
    assert debt_id not in foreign.headers["location"]


def test_debt_posts_require_csrf_and_never_echo_private_reason(
    m2_test_database: Engine,
) -> None:
    from app.db import create_database_session_factory

    session_factory = create_database_session_factory(m2_test_database)
    client, settings = _client(m2_test_database)
    with session_factory() as db:
        actor, shop, _target, linked = _linked_customer(db)
        _session(db, client, settings, actor, shop)
        linked_id = linked.id
    missing_csrf = client.post(
        f"/shop/customers/{linked_id}/debts",
        data=_create_form(csrf="", key="00000000-0000-4000-8000-000000000001"),
        follow_redirects=False,
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert "00000000-0000-4000-8000-000000000001" not in missing_csrf.text


def test_reject_and_cancel_prg_keep_reasons_private_and_autoescaped(
    m2_test_database: Engine,
) -> None:
    from app.db import create_database_session_factory

    session_factory = create_database_session_factory(m2_test_database)
    client, settings = _client(m2_test_database)
    with session_factory() as db:
        actor, shop, target, linked = _linked_customer(db)
        debts = []
        for _index in range(2):
            debt = Debt(
                shop_customer_id=linked.id,
                created_by_user_id=actor.id,
                original_amount_uzs=Decimal("100"),
                discount_basis_points=0,
                discounted_amount_uzs=Decimal("100"),
                due_date=date(2026, 8, 12),
                pending_expires_at=NOW + timedelta(hours=72),
                status=DebtStatus.PENDING.value,
                revision=1,
                created_at=NOW,
                updated_at=NOW,
            )
            db.add(debt)
            db.flush()
            debts.append(debt.id)
        actor_id = actor.id
        target_id = target.id
        shop_id = shop.id
        db.commit()

    with session_factory() as db:
        fresh_target = db.get(type(target), target_id)
        fresh_shop = db.get(type(shop), shop_id)
        assert fresh_target is not None and fresh_shop is not None
        customer_session = _session(db, client, settings, fresh_target, fresh_shop)
        customer_csrf = _csrf(customer_session)
    private_customer_reason = "<script>alert('private')</script>"
    rejected = client.post(
        f"/customer/debts/{debts[0]}/reject",
        data={
            "csrf_token": customer_csrf,
            "expected_revision": "1",
            "reason": private_customer_reason,
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert private_customer_reason not in rejected.headers["location"]
    rejected_detail = client.get(f"/customer/debts/{debts[0]}")
    assert private_customer_reason not in rejected_detail.text
    assert "&lt;script&gt;" in rejected_detail.text

    client.cookies.clear()
    with session_factory() as db:
        fresh_actor = db.get(type(actor), actor_id)
        fresh_shop = db.get(type(shop), shop_id)
        assert fresh_actor is not None and fresh_shop is not None
        staff_session = _session(db, client, settings, fresh_actor, fresh_shop)
        staff_csrf = _csrf(staff_session)
    private_staff_reason = "private staff correction"
    cancelled = client.post(
        f"/shop/debts/{debts[1]}/cancel",
        data={
            "csrf_token": staff_csrf,
            "expected_revision": "1",
            "reason": private_staff_reason,
        },
        follow_redirects=False,
    )
    assert cancelled.status_code == 303
    assert private_staff_reason not in cancelled.headers["location"]
    with session_factory.begin() as db:
        statuses = {
            str(row.id): row.status
            for row in db.scalars(select(Debt).where(Debt.id.in_(debts)))
        }
        assert statuses == {
            str(debts[0]): DebtStatus.REJECTED.value,
            str(debts[1]): DebtStatus.CANCELLED.value,
        }
        assert db.scalar(select(func.count()).select_from(OfferAcceptance)) == 0
