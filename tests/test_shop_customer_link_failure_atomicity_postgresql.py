from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import app.shop_customer.service as link_service_module
from app.audit.contracts import AuditEvent
from app.audit.models import AuditLog
from app.auth.models import AuthRateLimit, User
from app.customer.models import Customer
from app.settings import Settings
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    DetachedShopCustomerAuthority,
    LinkShopCustomerCommand,
    ShopCustomerLinkResult,
    TransientCanonicalShopCustomerPhone,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.rate_limit import record_shop_customer_link_attempt
from app.shop_customer.service import (
    ShopCustomerLinkInternalError,
    coordinate_link_active_customer,
)
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.models import TelegramLink
from tests.test_shop_customer_link_service_postgresql import (
    NOW,
    _factory,
    _seed_eligible,
)
from tests.test_shop_customer_repository_postgresql import (
    _add_active_customer,
    _add_user,
)

RATE_KEY = "test-rate-limit-hmac-key-for-m12-link-failure-atomicity"


class InjectedAuditFailure(RuntimeError):
    pass


class FailingAuditWriter:
    def append(self, *, event: AuditEvent) -> None:
        _ = event
        raise InjectedAuditFailure("synthetic provider detail")


class FlushFaultSession(Session):
    def flush(self, objects=None) -> None:
        if any(isinstance(item, ShopCustomer) for item in self.new):
            raise RuntimeError("synthetic flush provider detail")
        super().flush(objects)


def _settings(engine: Engine) -> Settings:
    return Settings(
        _env_file=None,
        app_environment="testing",
        debug=False,
        database_url=engine.url.render_as_string(hide_password=False),
        session_cookie_secure=False,
        rate_limit_hmac_key=SecretStr(RATE_KEY),
    )


def _record_tx_b(
    engine: Engine,
    *,
    command: LinkShopCustomerCommand,
) -> None:
    result = record_shop_customer_link_attempt(
        _factory(engine),
        settings=_settings(engine),
        authority=command.authority,
        submitted_phone=command.target_phone.for_server_lookup(),
        client_ip=ResolvedClientIp("203.0.113.44"),
        now=NOW,
    )
    assert result.allowed is True


def _assert_only_tx_b_remains(engine: Engine) -> None:
    with _factory(engine)() as verification:
        assert verification.scalar(select(func.count()).select_from(AuthRateLimit)) == 4
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 0
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    ("missing", "disabled", "draft", "unverified", "collision_like"),
)
def test_ineligible_states_return_identical_generic_result_and_zero_tx_c_mutation(
    m2_test_database: Engine,
    state: str,
) -> None:
    if state == "missing":
        factory = _factory(m2_test_database)
        with factory.begin() as setup:
            actor = _add_user(setup)
            shop = Shop(name="Missing target tenant", phone="+998900005551")
            setup.add(shop)
            setup.flush()
            setup.add(
                ShopStaff(
                    shop_id=shop.id,
                    user_id=actor.id,
                    role="cashier",
                    is_active=True,
                )
            )
            command = LinkShopCustomerCommand(
                authority=DetachedShopCustomerAuthority(
                    actor_user_id=UserId(actor.id),
                    current_shop_id=ShopId(shop.id),
                ),
                target_phone=TransientCanonicalShopCustomerPhone("+998900005552"),
            )
    else:
        command, ids = _seed_eligible(
            m2_test_database,
            customer_active=state != "draft",
        )
        factory = _factory(m2_test_database)
        with factory.begin() as mutation:
            target = mutation.get(User, ids["target"])
            link = mutation.get(TelegramLink, ids["link"])
            assert target is not None and link is not None
            if state == "disabled":
                target.is_active = False
            elif state == "unverified":
                link.phone_verified_at = None
            elif state == "collision_like":
                target.phone = "+998900005553"

    _record_tx_b(m2_test_database, command=command)
    result = coordinate_link_active_customer(factory, command=command, now=NOW)

    assert result == ShopCustomerLinkResult.unavailable()
    assert repr(result) == repr(ShopCustomerLinkResult.unavailable())
    with factory() as verification:
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 0
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert verification.scalar(select(func.count()).select_from(AuthRateLimit)) == 4


@pytest.mark.integration
def test_audit_failure_rolls_back_tx_c_but_preserves_closed_tx_b(
    m2_test_database: Engine,
) -> None:
    command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)
    _record_tx_b(m2_test_database, command=command)
    with factory() as before:
        customer = before.get(Customer, ids["customer"])
        link = before.get(TelegramLink, ids["link"])
        assert customer is not None and link is not None
        snapshot = (
            customer.onboarding_status,
            customer.updated_at,
            link.linked_at,
            link.phone_verified_at,
            link.updated_at,
        )

    with pytest.raises(ShopCustomerLinkInternalError) as caught:
        coordinate_link_active_customer(
            factory,
            command=command,
            now=NOW,
            audit_writer_factory=lambda _session: FailingAuditWriter(),
        )

    _assert_only_tx_b_remains(m2_test_database)
    rendered = str(caught.value) + repr(caught.value)
    assert "provider detail" not in rendered
    assert command.target_phone.for_server_lookup() not in rendered
    assert str(ids["customer"]) not in rendered
    with factory() as after:
        customer = after.get(Customer, ids["customer"])
        link = after.get(TelegramLink, ids["link"])
        assert customer is not None and link is not None
        assert (
            customer.onboarding_status,
            customer.updated_at,
            link.linked_at,
            link.phone_verified_at,
            link.updated_at,
        ) == snapshot


@pytest.mark.integration
def test_unexpected_integrity_error_is_reraised_only_as_safe_error(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)
    collision_id = uuid4()
    with factory.begin() as setup:
        actor = setup.get(User, ids["actor"])
        shop = setup.get(Shop, ids["shop"])
        assert actor is not None and shop is not None
        other_target = _add_user(setup)
        other_customer = _add_active_customer(setup, user=other_target)
        setup.add(
            ShopCustomer(
                id=collision_id,
                shop_id=shop.id,
                customer_id=other_customer.id,
                credit_limit_uzs=Decimal("1000000"),
                max_open_debts=2,
                list_status="normal",
                revision=1,
                created_by_user_id=actor.id,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    monkeypatch.setattr(link_service_module, "uuid4", lambda: collision_id)

    with pytest.raises(ShopCustomerLinkInternalError) as caught:
        coordinate_link_active_customer(factory, command=command, now=NOW)

    rendered = str(caught.value) + repr(caught.value)
    assert "IntegrityError" not in rendered
    assert "SQL" not in rendered
    assert str(collision_id) not in rendered
    assert command.target_phone.for_server_lookup() not in rendered
    with factory() as verification:
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 1
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_insert_flush_fault_rolls_back_domain_and_preserves_rate_attempt(
    m2_test_database: Engine,
) -> None:
    command, _ids = _seed_eligible(m2_test_database)
    _record_tx_b(m2_test_database, command=command)
    fault_factory = sessionmaker(bind=m2_test_database, class_=FlushFaultSession)

    with pytest.raises(ShopCustomerLinkInternalError) as caught:
        coordinate_link_active_customer(
            fault_factory,
            command=command,
            now=NOW,
        )

    _assert_only_tx_b_remains(m2_test_database)
    rendered = str(caught.value) + repr(caught.value)
    assert "flush provider detail" not in rendered
    assert command.target_phone.for_server_lookup() not in rendered


def test_invalid_phone_and_safe_exceptions_never_render_raw_input() -> None:
    raw_phone = "raw-invalid-phone-value"
    with pytest.raises(ValueError) as invalid:
        TransientCanonicalShopCustomerPhone(raw_phone)
    assert raw_phone not in str(invalid.value)
    assert raw_phone not in repr(invalid.value)
    safe = ShopCustomerLinkInternalError()
    assert raw_phone not in str(safe)
    assert raw_phone not in repr(safe)
    assert "SQL" not in str(safe)
