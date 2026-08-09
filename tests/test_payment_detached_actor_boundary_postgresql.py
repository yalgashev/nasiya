from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.auth.csrf import get_csrf_token
from app.auth.error_codes import ErrorCode
from app.auth.models import Session as AuthSession
from app.auth.sessions import create_anonymous_session, create_authenticated_session
from app.db import create_database_session_factory
from app.debt.presentation import DebtWebLanguage
from app.payment.dependencies import (
    DetachedPaymentActorContext,
    get_detached_current_shop_payment_actor_context,
)
from app.settings import Settings
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from tests.test_shop_customer_repository_postgresql import _add_shop, _add_user

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
RATE_KEY = "test-rate-limit-hmac-key-for-m14-payment-actor"


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
    session = create_database_session_factory(m2_test_database)()
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


def _add_cashier(session: Session, *, shop: Shop, user_id: UUID) -> None:
    session.add(
        ShopStaff(
            shop_id=shop.id,
            user_id=user_id,
            role=ShopRole.CASHIER.value,
            is_active=True,
        )
    )
    session.flush()


def _payment_probe(
    engine: Engine,
    *,
    active_shop_id: UUID | None,
    authenticated: bool = True,
) -> tuple[TestClient, UUID, str, UUID | None]:
    settings = _settings(engine)
    seed_factory = create_database_session_factory(engine)
    with seed_factory.begin() as seed:
        user_id: UUID | None = None
        if authenticated:
            user_id = _add_user(seed).id
            created = create_authenticated_session(
                seed,
                user_id,
                "m14-payment-actor",
                NOW,
                settings=settings,
            )
        else:
            created = create_anonymous_session(
                seed,
                "m14-payment-actor",
                NOW,
                settings=settings,
            )
        created.session.active_shop_id = active_shop_id
        seed.flush()
        raw_cookie = created.raw_token.as_cookie_value()
        csrf = get_csrf_token(created.session).as_form_value()
        session_id = created.session.id

    TrackingSession.created = []
    application = FastAPI()
    application.state.settings = settings
    application.state.database_session_factory = sessionmaker(
        bind=engine, class_=TrackingSession
    )
    application.state.authority = None
    application.state.tx_b_opened = False

    @application.post("/payment-probe")
    async def payment_probe(
        request: Request,
        authority: Annotated[
            DetachedPaymentActorContext,
            Depends(get_detached_current_shop_payment_actor_context),
        ],
    ) -> dict[str, bool]:
        tx_a_sessions = tuple(TrackingSession.created)
        assert tx_a_sessions
        assert all(item.close_calls == 1 for item in tx_a_sessions)
        assert all(not item.in_transaction() for item in tx_a_sessions)
        with request.app.state.database_session_factory.begin() as tx_b:
            assert all(item.close_calls == 1 for item in tx_a_sessions)
            assert tx_b not in tx_a_sessions
            request.app.state.tx_b_opened = True
        request.app.state.authority = authority
        return {"ready": True}

    client = TestClient(application)
    client.cookies.set(settings.session_cookie_name, raw_cookie)
    return client, session_id, csrf, user_id


@pytest.mark.integration
def test_payment_actor_context_is_server_derived_and_tx_a_closes_before_tx_b(
    m2_test_database: Engine,
) -> None:
    seed_factory = create_database_session_factory(m2_test_database)
    with seed_factory.begin() as seed:
        shop = _add_shop(seed, name="M14 detached payment tenant")
        shop_id = shop.id
    client, session_id, csrf, user_id = _payment_probe(
        m2_test_database, active_shop_id=shop_id
    )
    assert user_id is not None
    with seed_factory.begin() as seed:
        shop = seed.get(Shop, shop_id)
        assert shop is not None
        _add_cashier(seed, shop=shop, user_id=user_id)

    response = client.post(
        f"/payment-probe?shop_id={uuid4()}",
        data={
            "csrf_token": csrf,
            "shop_id": str(uuid4()),
            "role_hint": ShopRole.OWNER.value,
        },
        headers={"Accept-Language": "ru-RU,uz;q=0.8"},
    )

    assert response.status_code == 200
    authority = client.app.state.authority
    assert authority.actor_user_id == user_id
    assert authority.current_shop_id == shop_id
    assert authority.role_hint is ShopRole.CASHIER
    assert authority.language is DebtWebLanguage.RU
    assert str(user_id) not in repr(authority)
    assert str(shop_id) not in repr(authority)
    assert client.app.state.tx_b_opened
    assert len(TrackingSession.created) == 2
    assert all(item.close_calls == 1 for item in TrackingSession.created)
    with seed_factory() as verification:
        auth_session = verification.get(AuthSession, session_id)
        assert auth_session is not None
        assert auth_session.last_seen_at == NOW


@pytest.mark.integration
def test_payment_actor_boundary_keeps_csrf_session_and_mode_failures_stable(
    m2_test_database: Engine,
) -> None:
    no_session = TestClient(FastAPI())
    no_session.app.state.settings = _settings(m2_test_database)
    no_session.app.state.database_session_factory = sessionmaker(bind=m2_test_database)

    @no_session.app.post("/payment-probe")
    async def no_session_probe(
        authority: Annotated[
            DetachedPaymentActorContext,
            Depends(get_detached_current_shop_payment_actor_context),
        ],
    ) -> dict[str, str]:
        return {"role": authority.role_hint.value}

    csrf_failed = no_session.post("/payment-probe", data={"csrf_token": "forged"})
    assert csrf_failed.status_code == 403
    assert csrf_failed.headers["x-error-code"] == ErrorCode.CSRF_FAILED.value

    client, _session_id, csrf, _user_id = _payment_probe(
        m2_test_database, active_shop_id=None, authenticated=False
    )
    anonymous = client.post(
        "/payment-probe",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert anonymous.status_code == 303
    assert anonymous.headers["location"] == "/auth/login"
    assert anonymous.headers["x-error-code"] == ErrorCode.UNAUTHORIZED.value


@pytest.mark.integration
def test_payment_actor_boundary_requires_a_server_selected_current_shop(
    m2_test_database: Engine,
) -> None:
    client, _session_id, csrf, _user_id = _payment_probe(
        m2_test_database, active_shop_id=None
    )

    response = client.post(
        f"/payment-probe?shop_id={uuid4()}",
        data={"csrf_token": csrf, "shop_id": str(uuid4())},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/shop/select"
