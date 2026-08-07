from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import AuditEventType
from app.audit.models import AuditLog
from app.auth.models import Session as AuthSession
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    DetachedShopCustomerAuthority,
    LinkShopCustomerCommand,
    ShopCustomerLinkOutcome,
    TransientCanonicalShopCustomerPhone,
)
from app.shop_customer.models import ShopCustomer
from app.shop_customer.service import coordinate_link_active_customer
from app.telegram.models import TelegramLink
from tests.test_shop_customer_repository_postgresql import (
    _add_active_customer,
    _add_shop,
    _add_user,
)

NOW = datetime(2026, 8, 7, 19, 0, tzinfo=UTC)


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session)


def _seed_eligible(
    engine: Engine,
    *,
    role: ShopRole = ShopRole.CASHIER,
    shop_status: ShopStatus = ShopStatus.ACTIVE,
    membership_active: bool = True,
    actor_platform_admin: bool = False,
    customer_active: bool = True,
) -> tuple[LinkShopCustomerCommand, dict[str, object]]:
    factory = _factory(engine)
    with factory.begin() as session:
        actor = _add_user(session)
        actor.is_platform_admin = actor_platform_admin
        target = _add_user(session)
        if customer_active:
            customer = _add_active_customer(session, user=target)
        else:
            customer = Customer(
                user_id=target.id,
                onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
                created_at=NOW,
                updated_at=NOW,
                activated_at=None,
            )
            session.add(customer)
        link = TelegramLink(
            user_id=target.id,
            telegram_chat_id=target.id.int % 8_000_000_000 + 1,
            linked_at=NOW,
            unlinked_at=None,
            phone_verified_at=NOW,
            updated_at=NOW,
        )
        session.add(link)
        shop = _add_shop(session, name="Link service tenant")
        shop.status = shop_status.value
        staff = ShopStaff(
            shop_id=shop.id,
            user_id=actor.id,
            role=role.value,
            is_active=membership_active,
            revoked_at=None if membership_active else NOW,
        )
        session.add(staff)
        session.flush()
        command = LinkShopCustomerCommand(
            authority=DetachedShopCustomerAuthority(
                actor_user_id=UserId(actor.id),
                current_shop_id=ShopId(shop.id),
            ),
            target_phone=TransientCanonicalShopCustomerPhone(target.phone),
        )
        ids = {
            "actor": actor.id,
            "target": target.id,
            "customer": customer.id,
            "link": link.id,
            "shop": shop.id,
            "staff": staff.id,
        }
    return command, ids


@pytest.mark.integration
@pytest.mark.parametrize("role", tuple(ShopRole))
def test_each_active_shop_role_can_link_one_verified_active_customer(
    m2_test_database: Engine,
    role: ShopRole,
) -> None:
    command, ids = _seed_eligible(m2_test_database, role=role)
    factory = _factory(m2_test_database)

    result = coordinate_link_active_customer(
        factory,
        command=command,
        now=NOW,
    )

    assert result.outcome is ShopCustomerLinkOutcome.CREATED
    with factory() as verification:
        row = verification.scalar(select(ShopCustomer))
        audit = verification.scalar(select(AuditLog))
        assert row is not None
        assert row.shop_id == ids["shop"]
        assert row.customer_id == ids["customer"]
        assert row.created_by_user_id == ids["actor"]
        assert row.credit_limit_uzs == Decimal("1000000")
        assert row.max_open_debts == 2
        assert row.list_status == "normal"
        assert row.revision == 1
        assert audit is not None
        assert audit.event_type == AuditEventType.SHOP_CUSTOMER_LINKED.value
        assert audit.object_id == row.id
        assert audit.payload == {
            "outcome": "created",
            "credit_limit_uzs": 1_000_000,
            "max_open_debts": 2,
            "list_status": "normal",
            "revision": 1,
        }


@pytest.mark.integration
def test_repeat_is_idempotent_and_retains_original_policy_snapshot(
    m2_test_database: Engine,
) -> None:
    command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)
    first = coordinate_link_active_customer(factory, command=command, now=NOW)
    with factory.begin() as policy_change:
        shop = policy_change.get(Shop, ids["shop"])
        assert shop is not None
        shop.default_credit_limit_uzs = Decimal("9000000")
        shop.default_max_open_debts = 9
        shop.updated_at = NOW + timedelta(minutes=1)

    replay = coordinate_link_active_customer(
        factory,
        command=command,
        now=NOW + timedelta(minutes=2),
    )

    assert first.outcome is ShopCustomerLinkOutcome.CREATED
    assert replay.outcome is ShopCustomerLinkOutcome.ALREADY_LINKED
    assert replay.shop_customer_id == first.shop_customer_id
    with factory() as verification:
        row = verification.scalar(select(ShopCustomer))
        assert row is not None
        assert row.credit_limit_uzs == Decimal("1000000")
        assert row.max_open_debts == 2
        assert row.revision == 1
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 1
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    ("shop_status", "membership_active", "platform_admin"),
    (
        (ShopStatus.SUSPENDED, True, False),
        (ShopStatus.ACTIVE, False, False),
        (ShopStatus.ACTIVE, False, True),
    ),
)
def test_live_shop_membership_authority_denials_create_nothing(
    m2_test_database: Engine,
    shop_status: ShopStatus,
    membership_active: bool,
    platform_admin: bool,
) -> None:
    command, _ids = _seed_eligible(
        m2_test_database,
        shop_status=shop_status,
        membership_active=membership_active,
        actor_platform_admin=platform_admin,
    )
    factory = _factory(m2_test_database)

    result = coordinate_link_active_customer(factory, command=command, now=NOW)

    assert result.outcome is ShopCustomerLinkOutcome.CUSTOMER_LINK_UNAVAILABLE
    with factory() as verification:
        assert verification.scalar(select(func.count()).select_from(ShopCustomer)) == 0
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_link_mutates_only_relationship_and_central_audit(
    m2_test_database: Engine,
) -> None:
    command, ids = _seed_eligible(m2_test_database)
    factory = _factory(m2_test_database)
    with factory.begin() as setup:
        auth_session = AuthSession(
            user_id=ids["target"],
            active_shop_id=None,
            token_hash=uuid4().hex + uuid4().hex,
            csrf_secret=uuid4().hex,
            created_at=NOW,
            last_seen_at=NOW,
            expires_at=NOW + timedelta(days=1),
            revoked_at=None,
        )
        setup.add(auth_session)
        setup.flush()
        auth_session_id = auth_session.id
        target_before = setup.get(User, ids["target"])
        customer_before = setup.get(Customer, ids["customer"])
        link_before = setup.get(TelegramLink, ids["link"])
        assert target_before is not None
        assert customer_before is not None
        assert link_before is not None
        snapshots = (
            (target_before.phone, target_before.is_active, target_before.updated_at),
            (
                customer_before.onboarding_status,
                customer_before.activated_at,
                customer_before.updated_at,
            ),
            (
                link_before.telegram_chat_id,
                link_before.linked_at,
                link_before.phone_verified_at,
                link_before.updated_at,
            ),
            (
                auth_session.active_shop_id,
                auth_session.last_seen_at,
                auth_session.expires_at,
                auth_session.revoked_at,
            ),
        )

    result = coordinate_link_active_customer(factory, command=command, now=NOW)

    assert result.outcome is ShopCustomerLinkOutcome.CREATED
    with factory() as verification:
        target = verification.get(User, ids["target"])
        customer = verification.get(Customer, ids["customer"])
        link = verification.get(TelegramLink, ids["link"])
        auth_session = verification.get(AuthSession, auth_session_id)
        assert target is not None
        assert customer is not None
        assert link is not None
        assert auth_session is not None
        assert (
            target.phone,
            target.is_active,
            target.updated_at,
        ) == snapshots[0]
        assert (
            customer.onboarding_status,
            customer.activated_at,
            customer.updated_at,
        ) == snapshots[1]
        assert (
            link.telegram_chat_id,
            link.linked_at,
            link.phone_verified_at,
            link.updated_at,
        ) == snapshots[2]
        assert (
            auth_session.active_shop_id,
            auth_session.last_seen_at,
            auth_session.expires_at,
            auth_session.revoked_at,
        ) == snapshots[3]


def test_service_has_no_borrowed_session_or_out_scope_mutation() -> None:
    source = Path("app/shop_customer/service.py").read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "CustomerIdentity" not in source
    assert "CustomerDocument" not in source
    assert "Otp" not in source
    assert "set_session" not in source
