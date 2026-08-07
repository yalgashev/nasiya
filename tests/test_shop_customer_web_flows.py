from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from html import unescape
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.csrf import get_csrf_token
from app.auth.models import User
from app.auth.sessions import CreatedSession, create_authenticated_session
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.db import create_database_session_factory
from app.main import create_app
from app.security_headers import AUTH_NO_STORE_CACHE_CONTROL
from app.settings import Settings
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from app.telegram.models import TelegramLink

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-customer-web"


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    factory = create_database_session_factory(m2_test_database)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


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
    app = create_app(settings=settings)
    from app.auth.deps import get_current_time

    app.dependency_overrides[get_current_time] = lambda: NOW
    return TestClient(app, client=("127.0.0.1", 51000)), settings


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _user(db: Session, *, phone: str | None = None) -> User:
    user = User(phone=phone or _phone())
    db.add(user)
    db.flush()
    return user


def _session(
    db: Session, client: TestClient, settings: Settings, user: User, shop: Shop
) -> CreatedSession:
    created = create_authenticated_session(
        db, user.id, "pytest-shop-customer-web", NOW, settings=settings
    )
    created.session.active_shop_id = shop.id
    db.commit()
    client.cookies.set(
        settings.session_cookie_name,
        created.raw_token.as_cookie_value(),
        domain="testserver.local",
        path="/",
    )
    return created


def _seed(
    db: Session, *, role: ShopRole = ShopRole.OWNER
) -> tuple[User, Shop, User, Customer]:
    actor = _user(db)
    target = _user(db)
    customer = Customer(
        user_id=target.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        activated_at=NOW,
    )
    shop = Shop(name="Web tenant", phone=_phone())
    db.add_all(
        (
            customer,
            shop,
            TelegramLink(
                user_id=target.id,
                telegram_chat_id=target.id.int % 8_000_000_000 + 1,
                linked_at=NOW,
                phone_verified_at=NOW,
                updated_at=NOW,
            ),
        )
    )
    db.flush()
    db.add(ShopStaff(shop_id=shop.id, user_id=actor.id, role=role.value))
    db.flush()
    return actor, shop, target, customer


def _csrf(created: CreatedSession) -> str:
    return get_csrf_token(created.session).as_form_value()


def test_roster_and_link_post_are_masked_prg_and_no_store(
    m2_test_database: Engine, db_session: Session
) -> None:
    client, settings = _client(m2_test_database)
    actor, shop, target, _customer = _seed(db_session)
    created = _session(db_session, client, settings, actor, shop)

    before = client.get("/shop/customers")
    assert before.status_code == 200
    assert before.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert target.phone not in unescape(before.text)

    submitted = client.post(
        "/shop/customers/link",
        data={"csrf_token": _csrf(created), "phone": target.phone},
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    assert submitted.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert submitted.headers["location"] == "/shop/customers?notice=linked"
    assert target.phone not in submitted.headers["location"]

    roster = client.get(submitted.headers["location"])
    html = unescape(roster.text)
    assert target.phone not in html
    assert target.phone[-2:] in html
    assert roster.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert db_session.scalar(select(ShopCustomer)) is not None

    xss_attempt = client.post(
        "/shop/customers/link",
        data={"csrf_token": _csrf(created), "phone": "<script>alert(1)</script>"},
        follow_redirects=False,
    )
    assert xss_attempt.headers["location"] == "/shop/customers?error=VALIDATION_ERROR"
    xss_page = unescape(client.get(xss_attempt.headers["location"]).text)
    assert "<script>alert(1)</script>" not in xss_page


@pytest.mark.parametrize(
    ("role", "can_edit_defaults", "can_edit_policy"),
    (
        (ShopRole.OWNER, True, True),
        (ShopRole.MANAGER, False, True),
        (ShopRole.CASHIER, False, False),
    ),
)
def test_role_controls_are_server_derived(
    m2_test_database: Engine,
    db_session: Session,
    role: ShopRole,
    can_edit_defaults: bool,
    can_edit_policy: bool,
) -> None:
    client, settings = _client(m2_test_database)
    actor, shop, target, customer = _seed(db_session, role=role)
    row = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        created_by_user_id=actor.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=2,
        list_status="normal",
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(row)
    db_session.flush()
    _session(db_session, client, settings, actor, shop)

    roster = unescape(client.get("/shop/customers").text)
    assert (f"/shop/customers/{row.id}/policy" in roster) is can_edit_policy
    defaults = unescape(client.get("/shop/settings/credit").text)
    assert ('<button type="submit">' in defaults) is can_edit_defaults
    assert target.phone not in roster


def test_owner_default_and_manager_policy_posts_use_safe_prg(
    m2_test_database: Engine, db_session: Session
) -> None:
    client, settings = _client(m2_test_database)
    owner, shop, target, customer = _seed(db_session)
    manager = _user(db_session)
    db_session.add(ShopStaff(shop_id=shop.id, user_id=manager.id, role="manager"))
    row = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        created_by_user_id=owner.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=2,
        list_status="normal",
        revision=1,
        created_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
    )
    db_session.add(row)
    db_session.flush()
    owner_session = _session(db_session, client, settings, owner, shop)

    defaults = client.get("/shop/settings/credit")
    assert defaults.status_code == 200
    token = db_session.get(Shop, shop.id)
    assert token is not None
    updated = client.post(
        "/shop/settings/credit",
        data={
            "csrf_token": _csrf(owner_session),
            "expected_updated_at": token.updated_at.isoformat(),
            "credit_limit_uzs": "2000000",
            "max_open_debts": "3",
        },
        follow_redirects=False,
    )
    assert updated.headers["location"] == "/shop/settings/credit?notice=updated"

    client.cookies.clear()
    manager_session = _session(db_session, client, settings, manager, shop)
    policy = client.post(
        f"/shop/customers/{row.id}/policy",
        data={
            "csrf_token": _csrf(manager_session),
            "expected_revision": "1",
            "credit_limit_uzs": "2000000",
            "max_open_debts": "3",
            "list_status": "blacklisted",
        },
        follow_redirects=False,
    )
    assert policy.headers["location"] == "/shop/customers?notice=updated"
    assert str(row.id) not in policy.headers["location"]
    assert target.phone not in policy.headers["location"]


def test_cashier_policy_and_default_posts_are_forbidden_with_safe_prg(
    m2_test_database: Engine, db_session: Session
) -> None:
    client, settings = _client(m2_test_database)
    cashier, shop, _target, customer = _seed(db_session, role=ShopRole.CASHIER)
    row = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        created_by_user_id=cashier.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=2,
        list_status="normal",
        revision=1,
        created_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
    )
    db_session.add(row)
    db_session.flush()
    created = _session(db_session, client, settings, cashier, shop)
    fresh_shop = db_session.get(Shop, shop.id)
    assert fresh_shop is not None

    defaults = client.post(
        "/shop/settings/credit",
        data={
            "csrf_token": _csrf(created),
            "expected_updated_at": fresh_shop.updated_at.isoformat(),
            "credit_limit_uzs": "2000000",
            "max_open_debts": "3",
        },
        follow_redirects=False,
    )
    policy = client.post(
        f"/shop/customers/{row.id}/policy",
        data={
            "csrf_token": _csrf(created),
            "expected_revision": "1",
            "credit_limit_uzs": "2000000",
            "max_open_debts": "3",
            "list_status": "blacklisted",
        },
        follow_redirects=False,
    )
    assert defaults.headers["location"] == "/shop/settings/credit?error=FORBIDDEN"
    assert policy.headers["location"] == "/shop/customers?error=FORBIDDEN"
    assert str(row.id) not in policy.headers["location"]


def test_own_customer_shops_never_discloses_policy_or_other_customer(
    m2_test_database: Engine, db_session: Session
) -> None:
    client, settings = _client(m2_test_database)
    actor, shop, target, customer = _seed(db_session)
    db_session.add(
        ShopCustomer(
            shop_id=shop.id,
            customer_id=customer.id,
            created_by_user_id=actor.id,
            credit_limit_uzs=Decimal("1000000"),
            max_open_debts=2,
            list_status="blacklisted",
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db_session.flush()
    _session(db_session, client, settings, target, shop)

    response = client.get("/customer/shops")
    html = unescape(response.text)
    assert response.status_code == 200
    assert shop.name in html
    assert "blacklisted" not in html
    assert "1000000" not in html
    assert target.phone not in html
    assert response.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL

    other_user = _user(db_session)
    db_session.add(
        Customer(
            user_id=other_user.id,
            onboarding_status=CUSTOMER_ONBOARDING_STATUS_ACTIVE,
            created_at=NOW,
            updated_at=NOW,
            activated_at=NOW,
        )
    )
    db_session.flush()
    client.cookies.clear()
    _session(db_session, client, settings, other_user, shop)
    cross_user = client.get("/customer/shops")
    assert shop.name not in unescape(cross_user.text)


def test_localized_errors_and_csrf_failure_are_no_store_and_identifier_safe(
    m2_test_database: Engine, db_session: Session
) -> None:
    client, settings = _client(m2_test_database)
    actor, shop, target, _customer = _seed(db_session)
    _session(db_session, client, settings, actor, shop)

    csrf_failed = client.post(
        "/shop/customers/link",
        data={"csrf_token": "invalid", "phone": target.phone},
        follow_redirects=False,
    )
    assert csrf_failed.status_code == 403
    assert csrf_failed.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
    assert target.phone not in csrf_failed.text

    localized = client.get(
        "/shop/settings/credit?error=SHOP_CUSTOMER_CHANGED",
        headers={"accept-language": "ru-RU,uz;q=0.8"},
    )
    assert "Магазин" not in localized.text
    assert "измен" in localized.text.casefold()
    assert localized.headers["cache-control"] == AUTH_NO_STORE_CACHE_CONTROL
