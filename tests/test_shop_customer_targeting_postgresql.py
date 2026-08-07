from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.models import User
from app.customer.models import (
    CUSTOMER_ONBOARDING_STATUS_ACTIVE,
    CUSTOMER_ONBOARDING_STATUS_DRAFT,
    Customer,
)
from app.db import create_database_session_factory
from app.shop.models import Shop, ShopStaff
from app.shop.repository import (
    lock_actor_shop_staff_for_update,
    lock_shop_for_update,
)
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import TransientCanonicalShopCustomerPhone
from app.shop_customer.models import ShopCustomer
from app.shop_customer.targeting import (
    discover_target_user_id,
    resolve_locked_eligible_target,
)
from app.telegram.models import TelegramLink
from tests.test_shop_customer_repository_postgresql import (
    _add_active_customer,
    _add_shop,
    _add_user,
)

NOW = datetime(2026, 8, 7, 17, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    factory = create_database_session_factory(m2_test_database)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _add_staff(session: Session, *, shop: Shop, actor: User) -> ShopStaff:
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=actor.id,
        role="cashier",
        is_active=True,
    )
    session.add(staff)
    session.flush()
    return staff


def _add_link(
    session: Session,
    *,
    target: User,
    verified: bool = True,
    unlinked: bool = False,
) -> TelegramLink:
    link = TelegramLink(
        user_id=target.id,
        telegram_chat_id=None if unlinked else (target.id.int % 8_000_000_000 + 1),
        linked_at=NOW,
        unlinked_at=NOW if unlinked else None,
        phone_verified_at=NOW if verified and not unlinked else None,
        updated_at=NOW,
    )
    session.add(link)
    session.flush()
    return link


def _locked_staff(
    session: Session,
    *,
    shop: Shop,
    actor: User,
):
    locked_shop = lock_shop_for_update(session, shop_id=ShopId(shop.id))
    assert locked_shop is not None
    locked_staff = lock_actor_shop_staff_for_update(
        session,
        locked_shop=locked_shop,
        actor_user_id=UserId(actor.id),
    )
    assert locked_staff is not None
    return locked_staff


def _counts(session: Session) -> tuple[int, int]:
    return (
        session.scalar(select(func.count()).select_from(ShopCustomer)) or 0,
        session.scalar(select(func.count()).select_from(AuditLog)) or 0,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "ineligible_state",
    (
        "disabled_user",
        "phone_changed",
        "missing_link",
        "unverified_link",
        "unlinked_link",
        "draft_customer",
    ),
)
def test_all_ineligible_states_converge_without_domain_mutation(
    db_session: Session,
    ineligible_state: str,
) -> None:
    actor = _add_user(db_session)
    target = _add_user(db_session)
    expected_phone = TransientCanonicalShopCustomerPhone(target.phone)
    target_id = discover_target_user_id(db_session, target_phone=expected_phone)
    assert target_id == target.id
    shop = _add_shop(db_session, name="Eligibility tenant")
    _add_staff(db_session, shop=shop, actor=actor)
    if ineligible_state == "draft_customer":
        customer = Customer(
            user_id=target.id,
            onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
            created_at=NOW,
            updated_at=NOW,
            activated_at=None,
        )
        db_session.add(customer)
        db_session.flush()
    else:
        _add_active_customer(db_session, user=target)
    if ineligible_state not in {"missing_link"}:
        _add_link(
            db_session,
            target=target,
            verified=ineligible_state != "unverified_link",
            unlinked=ineligible_state == "unlinked_link",
        )
    if ineligible_state == "disabled_user":
        target.is_active = False
    if ineligible_state == "phone_changed":
        target.phone = f"+998{uuid4().int % 1_000_000_000:09d}"
    db_session.flush()
    before = _counts(db_session)

    result = resolve_locked_eligible_target(
        db_session,
        locked_staff=_locked_staff(db_session, shop=shop, actor=actor),
        target_user_id=target_id,
        expected_phone=expected_phone,
    )

    assert result is None
    assert _counts(db_session) == before == (0, 0)


@pytest.mark.integration
def test_verified_active_exact_phone_chain_returns_redacted_locked_target(
    db_session: Session,
) -> None:
    actor = _add_user(db_session)
    target = _add_user(db_session)
    customer = _add_active_customer(db_session, user=target)
    _add_link(db_session, target=target)
    shop = _add_shop(db_session, name="Eligible tenant")
    _add_staff(db_session, shop=shop, actor=actor)
    phone = TransientCanonicalShopCustomerPhone(target.phone)
    target_id = discover_target_user_id(db_session, target_phone=phone)
    assert target_id is not None

    result = resolve_locked_eligible_target(
        db_session,
        locked_staff=_locked_staff(db_session, shop=shop, actor=actor),
        target_user_id=target_id,
        expected_phone=phone,
    )

    assert result is not None
    assert result.value.user_id == target.id
    assert result.value.customer_id == customer.id
    rendered = repr(result)
    assert str(target.id) not in rendered
    assert str(customer.id) not in rendered
    assert target.phone not in rendered
    assert _counts(db_session) == (0, 0)


@pytest.mark.integration
def test_missing_phone_discovery_returns_only_none(db_session: Session) -> None:
    before = _counts(db_session)

    result = discover_target_user_id(
        db_session,
        target_phone=TransientCanonicalShopCustomerPhone("+998900009999"),
    )

    assert result is None
    assert _counts(db_session) == before


@pytest.mark.integration
def test_relink_between_discovery_and_lock_is_revalidated_from_current_generation(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as setup:
        actor = _add_user(setup)
        target = _add_user(setup)
        _add_active_customer(setup, user=target)
        link = _add_link(setup, target=target)
        shop = _add_shop(setup, name="Relink race tenant")
        _add_staff(setup, shop=shop, actor=actor)
        actor_id, target_phone, shop_id, link_id = (
            actor.id,
            target.phone,
            shop.id,
            link.id,
        )
    phone = TransientCanonicalShopCustomerPhone(target_phone)
    with factory() as discovery:
        target_id = discover_target_user_id(discovery, target_phone=phone)
    assert target_id is not None
    with factory.begin() as relink:
        link = relink.get(TelegramLink, link_id)
        assert link is not None
        next_generation = NOW + timedelta(minutes=1)
        link.linked_at = next_generation
        link.phone_verified_at = next_generation
        link.updated_at = next_generation
    with factory.begin() as domain:
        actor = domain.get(User, actor_id)
        shop = domain.get(Shop, shop_id)
        assert actor is not None and shop is not None
        result = resolve_locked_eligible_target(
            domain,
            locked_staff=_locked_staff(domain, shop=shop, actor=actor),
            target_user_id=target_id,
            expected_phone=phone,
        )

    assert result is not None


@pytest.mark.integration
def test_activation_between_discovery_and_lock_is_revalidated_as_active(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as setup:
        actor = _add_user(setup)
        target = _add_user(setup)
        customer = Customer(
            user_id=target.id,
            onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
            created_at=NOW,
            updated_at=NOW,
            activated_at=None,
        )
        setup.add(customer)
        _add_link(setup, target=target)
        shop = _add_shop(setup, name="Activation race tenant")
        _add_staff(setup, shop=shop, actor=actor)
        setup.flush()
        actor_id, target_phone, shop_id, customer_id = (
            actor.id,
            target.phone,
            shop.id,
            customer.id,
        )
    phone = TransientCanonicalShopCustomerPhone(target_phone)
    with factory() as discovery:
        target_id = discover_target_user_id(discovery, target_phone=phone)
    assert target_id is not None
    with factory.begin() as activation:
        customer = activation.get(Customer, customer_id)
        assert customer is not None
        customer.onboarding_status = CUSTOMER_ONBOARDING_STATUS_ACTIVE
        customer.activated_at = NOW + timedelta(minutes=1)
        customer.updated_at = NOW + timedelta(minutes=1)
    with factory.begin() as domain:
        actor = domain.get(User, actor_id)
        shop = domain.get(Shop, shop_id)
        assert actor is not None and shop is not None
        result = resolve_locked_eligible_target(
            domain,
            locked_staff=_locked_staff(domain, shop=shop, actor=actor),
            target_user_id=target_id,
            expected_phone=phone,
        )

    assert result is not None


def test_targeting_has_no_own_customer_or_identity_decrypt_dependency() -> None:
    source = open("app/shop_customer/targeting.py", encoding="utf-8").read()
    assert "own_customer" not in source
    assert "customer_identity" not in source
    assert "decrypt" not in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
