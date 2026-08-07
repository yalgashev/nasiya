from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.csrf import get_csrf_token
from app.auth.models import Session as AuthSession
from app.auth.sessions import create_authenticated_session
from app.db import create_database_session_factory
from app.settings import Settings
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.contracts import DetachedShopCustomerAuthority
from app.shop_customer.dependencies import get_detached_shop_customer_authority
from tests.test_shop_customer_repository_postgresql import _add_shop, _add_user

NOW = datetime(2026, 8, 7, 16, 0, tzinfo=UTC)
RATE_KEY = "test-rate-limit-hmac-key-for-m12-detached-authority"


class TrackingSession(Session):
    created: list["TrackingSession"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.close_calls = 0
        self.__class__.created.append(self)

    def close(self) -> None:
        self.close_calls += 1
        super().close()


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
        rate_limit_hmac_key=RATE_KEY,
    )


def _add_staff(session: Session, *, shop: Shop, user_id) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=user_id,
        role=ShopRole.CASHIER.value,
        is_active=True,
    )
    session.add(staff)
    session.flush()
    return staff


def _authenticated_browser(
    engine: Engine,
    *,
    active_shop_id=None,
) -> tuple[TestClient, UUID, str, UUID]:
    settings = _settings(engine)
    seed_factory = create_database_session_factory(engine)
    with seed_factory.begin() as seed:
        user = _add_user(seed)
        created = create_authenticated_session(
            seed,
            user.id,
            "m12-test-browser",
            NOW,
            settings=settings,
        )
        created.session.active_shop_id = active_shop_id
        seed.flush()
        raw_cookie = created.raw_token.as_cookie_value()
        csrf = get_csrf_token(created.session).as_form_value()
        session_id = created.session.id
        user_id = user.id

    TrackingSession.created = []
    runtime_factory = sessionmaker(bind=engine, class_=TrackingSession)
    application = FastAPI()
    application.state.settings = settings
    application.state.database_session_factory = runtime_factory
    application.state.authority = None

    @application.post("/probe")
    async def probe(
        authority: Annotated[
            DetachedShopCustomerAuthority,
            Depends(get_detached_shop_customer_authority),
        ],
    ) -> dict[str, bool]:
        application.state.authority = authority
        return {"ready": True}

    client = TestClient(application)
    client.cookies.set(settings.session_cookie_name, raw_cookie)
    return client, session_id, csrf, user_id


@pytest.mark.integration
def test_authority_is_server_derived_and_tx_a_is_closed_before_route(
    m2_test_database: Engine,
) -> None:
    seed_factory = create_database_session_factory(m2_test_database)
    with seed_factory.begin() as seed:
        shop = _add_shop(seed, name="Detached tenant")
        shop_id = shop.id
    client, session_id, csrf, user_id = _authenticated_browser(
        m2_test_database,
        active_shop_id=shop_id,
    )
    with seed_factory.begin() as seed:
        shop = seed.get(Shop, shop_id)
        assert shop is not None
        _add_staff(seed, shop=shop, user_id=user_id)

    response = client.post(
        "/probe?shop_id=00000000-0000-0000-0000-000000000001",
        data={"csrf_token": csrf, "shop_id": str(uuid4())},
    )

    assert response.status_code == 200
    authority = client.app.state.authority
    assert authority.actor_user_id == user_id
    assert authority.current_shop_id == shop_id
    assert str(user_id) not in repr(authority)
    assert str(shop_id) not in repr(authority)
    assert TrackingSession.created
    assert all(item.close_calls == 1 for item in TrackingSession.created)
    assert all(not item.in_transaction() for item in TrackingSession.created)
    assert session_id is not None


@pytest.mark.integration
def test_one_live_membership_is_selected_inside_closed_tx_a(
    m2_test_database: Engine,
) -> None:
    seed_factory = create_database_session_factory(m2_test_database)
    client, session_id, csrf, user_id = _authenticated_browser(m2_test_database)
    with seed_factory.begin() as seed:
        shop = _add_shop(seed, name="Auto-selected tenant")
        _add_staff(seed, shop=shop, user_id=user_id)
        shop_id = shop.id

    response = client.post("/probe", data={"csrf_token": csrf})

    assert response.status_code == 200
    with seed_factory() as verification:
        stored = verification.get(AuthSession, session_id)
        assert stored is not None
        assert stored.active_shop_id == shop_id
    assert all(item.close_calls == 1 for item in TrackingSession.created)


@pytest.mark.integration
def test_stale_membership_is_cleared_then_safely_requires_selection(
    m2_test_database: Engine,
) -> None:
    seed_factory = create_database_session_factory(m2_test_database)
    with seed_factory.begin() as seed:
        stale_shop = _add_shop(seed, name="Stale tenant")
        stale_shop_id = stale_shop.id
    client, session_id, csrf, _user_id = _authenticated_browser(
        m2_test_database,
        active_shop_id=stale_shop_id,
    )

    response = client.post(
        "/probe",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/select"
    with seed_factory() as verification:
        stored = verification.get(AuthSession, session_id)
        assert stored is not None
        assert stored.active_shop_id is None
    assert all(item.close_calls == 1 for item in TrackingSession.created)


def test_detached_dependency_returns_no_orm_or_client_authority() -> None:
    annotations = DetachedShopCustomerAuthority.__annotations__
    assert set(annotations) == {"actor_user_id", "current_shop_id"}
    assert "Shop" not in annotations.values()
    assert "ShopStaff" not in annotations.values()
    assert "role" not in annotations
    assert "status" not in annotations
