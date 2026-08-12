from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from inspect import getsource
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.auth.csrf import get_csrf_token
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.auth.sessions import create_authenticated_session
from app.db import create_database_session_factory
from app.main import create_app
from app.rating.dependencies import (
    get_detached_current_shop_disclosure_actor_context,
    get_detached_current_shop_disclosure_read_actor_context,
    get_risk_band_disclosure_clock,
)
from app.rating.presentation import RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS
from app.rating.router import router
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL, CONTENT_SECURITY_POLICY
from app.settings import Settings
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from tests.test_shop_customer_web_flows import _seed

NOW = datetime(2026, 8, 12, 8, tzinfo=UTC)
_RATE_LIMIT_KEY = "m16-disclosure-route-test-rate-limit-key"


def _application(engine: Engine) -> FastAPI:
    app = create_app(
        settings=Settings(
            _env_file=None,
            app_environment="testing",
            debug=False,
            database_url=engine.url.render_as_string(hide_password=False),
            session_cookie_secure=False,
            rate_limit_hmac_key=_RATE_LIMIT_KEY,
        )
    )
    from app.auth.deps import get_current_time

    app.dependency_overrides[get_current_time] = lambda: NOW
    app.state.risk_band_disclosure_clock = lambda: NOW
    return app


def _client_with_target(engine: Engine) -> tuple[TestClient, ShopCustomer, str]:
    app = _application(engine)
    client = TestClient(app, client=("127.0.0.1", 51001))
    factory = create_database_session_factory(engine)
    with factory.begin() as session:
        actor, shop, _target, customer = _seed(session, role=ShopRole.CASHIER)
        relation = ShopCustomer(
            shop_id=shop.id,
            customer_id=customer.id,
            created_by_user_id=actor.id,
            credit_limit_uzs=1_000_000,
            max_open_debts=2,
            list_status="normal",
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(relation)
        session.flush()
        created = create_authenticated_session(
            session,
            actor.id,
            "m16-disclosure-router",
            NOW,
            settings=app.state.settings,
        )
        created.session.active_shop_id = shop.id
        csrf_token = get_csrf_token(created.session).as_form_value()
        relation_id = relation.id
    client.cookies.set(
        app.state.settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )
    with factory() as session:
        persisted = session.get_one(ShopCustomer, relation_id)
        return client, persisted, csrf_token


def test_exact_two_disclosure_routes_have_mode_specific_dependencies(
    m2_test_database: Engine,
) -> None:
    observed = {
        (route.name, method, route.path_format)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    expected = {
        (contract.name, contract.method, contract.path)
        for contract in RISK_BAND_DISCLOSURE_ROUTE_CONTRACTS
    }
    assert observed == expected

    application = _application(m2_test_database)
    included = [
        item
        for item in application.routes
        if getattr(item, "original_router", None) is router
    ]
    assert len(included) == 1
    routes = {
        route.name: route for route in router.routes if isinstance(route, APIRoute)
    }
    post_calls = {
        item.call
        for item in routes["shop_risk_band_disclosure_create"].dependant.dependencies
    }
    get_calls = {
        item.call
        for item in routes["shop_risk_band_disclosure_view"].dependant.dependencies
    }
    assert get_detached_current_shop_disclosure_actor_context in post_calls
    assert get_risk_band_disclosure_clock in post_calls
    assert get_detached_current_shop_disclosure_read_actor_context in get_calls
    assert get_risk_band_disclosure_clock not in get_calls


@pytest.mark.integration
def test_roster_and_debt_new_offer_only_server_rendered_post_capabilities(
    m2_test_database: Engine,
) -> None:
    client, relation, _csrf = _client_with_target(m2_test_database)
    roster = unescape(client.get("/shop/customers").text)
    action = f"/shop/customers/{relation.id}/risk-band-disclosures"
    assert f'<form action="{action}" method="post"' in roster
    assert 'name="purpose"' in roster
    assert 'name="idempotency_key"' in roster
    assert f'<a href="{action}"' not in roster

    debt_new = unescape(client.get(f"/shop/customers/{relation.id}/debts/new").text)
    assert f'<form action="{action}" method="post"' in debt_new
    assert f'<a href="{action}"' not in debt_new


@pytest.mark.integration
def test_post_prg_and_get_are_band_only_and_no_store(
    m2_test_database: Engine,
) -> None:
    client, relation, csrf_token = _client_with_target(m2_test_database)
    raw_key = str(uuid4())
    created = client.post(
        f"/shop/customers/{relation.id}/risk-band-disclosures",
        data={
            "csrf_token": csrf_token,
            "purpose": "debt_proposal_review",
            "idempotency_key": raw_key,
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert created.headers["location"].startswith("/shop/risk-band-disclosures/")
    assert raw_key not in created.headers["location"]

    snapshot = client.get(created.headers["location"])
    html = unescape(snapshot.text)
    assert snapshot.status_code == 200
    assert snapshot.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert "Yangi" in html
    action = f"/shop/customers/{relation.id}/risk-band-disclosures"
    assert html.count(action) == 1
    presentation_without_action = html.replace(action, "")
    for forbidden in (str(relation.id), raw_key, "score", "history", "delta"):
        assert forbidden not in presentation_without_action.casefold()

    hidden_key = re.search(r'name="idempotency_key" value="([^"]+)"', html)
    assert hidden_key is not None
    assert str(UUID(hidden_key.group(1))) == hidden_key.group(1)
    assert hidden_key.group(1) != raw_key


@pytest.mark.integration
def test_same_key_replay_has_same_location_and_conflict_is_generic_localized(
    m2_test_database: Engine,
) -> None:
    client, relation, csrf_token = _client_with_target(m2_test_database)
    raw_key = str(uuid4())
    action = f"/shop/customers/{relation.id}/risk-band-disclosures"
    payload = {
        "csrf_token": csrf_token,
        "purpose": "credit_limit_review",
        "idempotency_key": raw_key,
    }
    first = client.post(action, data=payload, follow_redirects=False)
    replay = client.post(action, data=payload, follow_redirects=False)
    assert first.status_code == replay.status_code == 303
    assert first.headers["location"] == replay.headers["location"]

    conflict = client.post(
        action,
        data={**payload, "purpose": "existing_debt_review"},
        follow_redirects=False,
    )
    invalid = client.post(
        action,
        data={**payload, "idempotency_key": "not-a-key"},
        follow_redirects=False,
    )
    assert conflict.headers["location"] == invalid.headers["location"]
    assert conflict.headers["location"] == "/shop/customers?risk_error=unavailable"
    message = client.get(
        conflict.headers["location"], headers={"Accept-Language": "ru"}
    )
    assert "недоступен" in message.text.casefold()
    alert = re.search(r'<p class="form-error" role="alert">(.*?)</p>', message.text)
    assert alert is not None
    for forbidden in (raw_key, str(relation.id), "conflict", "validation"):
        assert forbidden not in alert.group(1).casefold()


@pytest.mark.integration
def test_snapshot_refresh_uses_a_fresh_key_and_selected_purpose(
    m2_test_database: Engine,
) -> None:
    client, relation, csrf_token = _client_with_target(m2_test_database)
    created = client.post(
        f"/shop/customers/{relation.id}/risk-band-disclosures",
        data={
            "csrf_token": csrf_token,
            "purpose": "existing_debt_review",
            "idempotency_key": str(uuid4()),
        },
        follow_redirects=False,
    )
    first = unescape(client.get(created.headers["location"]).text)
    second = unescape(client.get(created.headers["location"]).text)
    key_pattern = r'name="idempotency_key" value="([^"]+)"'
    first_key = re.search(key_pattern, first)
    second_key = re.search(key_pattern, second)
    assert first_key is not None and second_key is not None
    assert first_key.group(1) != second_key.group(1)
    assert 'value="existing_debt_review" selected' in first
    assert 'method="post"' in first


@pytest.mark.integration
def test_suspended_shop_denies_fresh_but_reads_existing_snapshot(
    m2_test_database: Engine,
) -> None:
    client, relation, csrf_token = _client_with_target(m2_test_database)
    action = f"/shop/customers/{relation.id}/risk-band-disclosures"
    created = client.post(
        action,
        data={
            "csrf_token": csrf_token,
            "purpose": "debt_proposal_review",
            "idempotency_key": str(uuid4()),
        },
        follow_redirects=False,
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        relation_row = session.get_one(ShopCustomer, relation.id)
        shop = session.get_one(Shop, relation_row.shop_id)
        shop.status = "suspended"

    old_snapshot = client.get(created.headers["location"])
    assert old_snapshot.status_code == 200
    assert action not in old_snapshot.text
    roster = unescape(client.get("/shop/customers").text)
    assert action not in roster
    debt_new = client.get(
        f"/shop/customers/{relation.id}/debts/new", follow_redirects=False
    )
    assert debt_new.status_code == 303
    denied = client.post(
        action,
        data={
            "csrf_token": csrf_token,
            "purpose": "debt_proposal_review",
            "idempotency_key": str(uuid4()),
        },
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert denied.headers["location"] == "/shop/customers?risk_error=unavailable"


@pytest.mark.integration
def test_revoked_or_wrong_tenant_snapshot_is_generic_without_oracle(
    m2_test_database: Engine,
) -> None:
    owner_client, relation, csrf_token = _client_with_target(m2_test_database)
    action = f"/shop/customers/{relation.id}/risk-band-disclosures"
    created = owner_client.post(
        action,
        data={
            "csrf_token": csrf_token,
            "purpose": "debt_proposal_review",
            "idempotency_key": str(uuid4()),
        },
        follow_redirects=False,
    )
    foreign_client, _foreign_relation, _foreign_csrf = _client_with_target(
        m2_test_database
    )
    foreign = foreign_client.get(created.headers["location"], follow_redirects=False)
    guessed = foreign_client.get(
        f"/shop/risk-band-disclosures/{uuid4()}", follow_redirects=False
    )
    assert foreign.status_code == guessed.status_code == 303
    assert foreign.headers["location"] == guessed.headers["location"]
    assert foreign.headers["location"] == "/shop/customers?risk_error=unavailable"

    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        relation_row = session.get_one(ShopCustomer, relation.id)
        staff = session.scalar(
            select(ShopStaff).where(ShopStaff.shop_id == relation_row.shop_id)
        )
        assert staff is not None
        actor = session.get_one(User, staff.user_id)
        actor.is_platform_admin = True
        staff.is_active = False
        staff.revoked_at = NOW
    revoked = owner_client.get(created.headers["location"], follow_redirects=False)
    assert revoked.status_code == 303
    assert revoked.headers["location"] == "/shop/customers?risk_error=unavailable"
    for response in (foreign, guessed, revoked):
        assert str(relation.id) not in response.text


@pytest.mark.integration
def test_same_user_other_shop_or_unselected_mode_cannot_read_snapshot(
    m2_test_database: Engine,
) -> None:
    client, relation, csrf_token = _client_with_target(m2_test_database)
    created = client.post(
        f"/shop/customers/{relation.id}/risk-band-disclosures",
        data={
            "csrf_token": csrf_token,
            "purpose": "debt_proposal_review",
            "idempotency_key": str(uuid4()),
        },
        follow_redirects=False,
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        relation_row = session.get_one(ShopCustomer, relation.id)
        staff = session.scalar(
            select(ShopStaff).where(ShopStaff.shop_id == relation_row.shop_id)
        )
        assert staff is not None
        other_shop = Shop(
            name="Other disclosure tenant",
            phone=f"+998{uuid4().int % 10**9:09d}",
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(other_shop)
        session.flush()
        session.add(
            ShopStaff(
                shop_id=other_shop.id,
                user_id=staff.user_id,
                role=ShopRole.CASHIER.value,
                is_active=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        auth_session = session.scalar(
            select(AuthSession).where(AuthSession.user_id == staff.user_id)
        )
        assert auth_session is not None
        auth_session.active_shop_id = other_shop.id

    other_mode = client.get(created.headers["location"], follow_redirects=False)
    assert other_mode.status_code == 303
    assert other_mode.headers["location"] == "/shop/customers?risk_error=unavailable"

    with factory.begin() as session:
        auth_session = session.scalar(select(AuthSession))
        assert auth_session is not None
        auth_session.active_shop_id = None
    unselected = client.get(created.headers["location"], follow_redirects=False)
    assert unselected.status_code == 303
    assert unselected.headers["location"] == "/shop/customers?risk_error=unavailable"


def test_router_has_no_extra_delivery_surface_or_transaction_ownership() -> None:
    source = getsource(__import__("app.rating.router", fromlist=["router"]))
    assert source.count("@router.post(") == 1
    assert source.count("@router.get(") == 1
    assert '"/customer' not in source
    assert '"/admin' not in source
    assert '"/api' not in source
    assert "JSONResponse" not in source
    assert "fragment" not in source.casefold()
    assert "session.commit(" not in source
    assert "session.rollback(" not in source
    assert "session.close(" not in source


@pytest.mark.integration
def test_uz_ru_snapshot_copy_and_invalid_input_are_autoescaped_and_generic(
    m2_test_database: Engine,
) -> None:
    client, relation, csrf_token = _client_with_target(m2_test_database)
    action = f"/shop/customers/{relation.id}/risk-band-disclosures"
    created = client.post(
        action,
        data={
            "csrf_token": csrf_token,
            "purpose": "credit_limit_review",
            "idempotency_key": str(uuid4()),
        },
        follow_redirects=False,
    )
    uz = client.get(created.headers["location"], headers={"Accept-Language": "uz"})
    ru = client.get(created.headers["location"], headers={"Accept-Language": "ru"})
    assert "Risk bandi ko‘rinishi" in unescape(uz.text)
    assert "Kredit limitini ko‘rib chiqish" in unescape(uz.text)
    assert "Просмотр группы риска" in ru.text
    assert "Проверка кредитного лимита" in ru.text
    assert '<time datetime="2026-08-12T08:00:00+00:00">' in uz.text

    attack = '<script src="https://example.invalid/x.js"></script>'
    invalid = client.post(
        action,
        data={
            "csrf_token": csrf_token,
            "purpose": attack,
            "idempotency_key": str(uuid4()),
        },
        follow_redirects=False,
    )
    assert invalid.headers["location"] == "/shop/customers?risk_error=unavailable"
    body = client.get(invalid.headers["location"]).text
    assert attack not in body
    assert "example.invalid" not in body


def test_disclosure_templates_are_mobile_accessible_and_browser_authority_free() -> (
    None
):
    project = Path(__file__).resolve().parents[1]
    templates = "\n".join(
        (project / path).read_text(encoding="utf-8")
        for path in (
            "app/templates/rating/disclosure_view.html",
            "app/templates/shop_customer/roster.html",
            "app/templates/debt/shop_new.html",
        )
    )
    css = (project / "app/static/css/app.css").read_text(encoding="utf-8")
    lowered = templates.casefold()
    assert 'method="post"' in lowered
    assert 'name="csrf_token"' in lowered
    assert 'name="idempotency_key"' in lowered
    assert "<label" in lowered and "<select" in lowered
    assert "required" in lowered
    assert "<dl" in lowered and "<time" in lowered
    assert "<script" not in lowered
    for forbidden in (
        "localstorage",
        "sessionstorage",
        "cachestorage",
        "caches.open",
        "onclick=",
        "onchange=",
        "score",
        "delta",
    ):
        assert forbidden not in lowered
    assert "@media (max-width: 430px)" in css
    assert "min-height: 44px" in css
    assert ":focus-visible" in css
    assert "overflow-wrap: anywhere" in css
    assert "script-src 'self'" in CONTENT_SECURITY_POLICY
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
