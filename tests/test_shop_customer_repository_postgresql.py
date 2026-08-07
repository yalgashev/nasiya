import inspect
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.repository import lock_actor_and_target_users_for_update
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_ACTIVE, Customer
from app.customer.repository import lock_active_customer_for_target_user
from app.db import create_database_session_factory
from app.shop.models import Shop
from app.shop.repository import (
    lock_shop_for_update,
    read_locked_shop_defaults,
    update_locked_shop_defaults,
)
from app.shop.values import ShopId, UserId
from app.shop_customer.contracts import (
    ShopCustomerPolicy,
    ShopCustomerRevision,
    ShopDefaultCreditPolicy,
)
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.repository import (
    _mark_shop_customer_predecessors_locked,
    get_shop_customer_by_shop,
    insert_shop_customer,
    list_customer_own_shop_customers,
    list_shop_customers_by_shop,
    lock_shop_customer_by_pair,
    lock_shop_customer_by_tenant_locator,
    update_locked_shop_customer,
)
from app.shop_customer.values import (
    CreditLimitUzbekistanSom,
    CustomerId,
    MaxOpenDebts,
    ShopCustomerId,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    factory = create_database_session_factory(m2_test_database)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _add_user(session: Session, *, user_id: UUID | None = None) -> User:
    user = User(id=user_id or uuid4(), phone=_phone(), is_active=True)
    session.add(user)
    session.flush()
    return user


def _add_active_customer(session: Session, *, user: User) -> Customer:
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_ACTIVE,
        created_at=NOW,
        updated_at=NOW,
        activated_at=NOW,
    )
    session.add(customer)
    session.flush()
    return customer


def _add_shop(session: Session, *, name: str) -> Shop:
    shop = Shop(name=name, phone=_phone(), created_at=NOW, updated_at=NOW)
    session.add(shop)
    session.flush()
    return shop


def _locked_predecessors(
    session: Session,
    *,
    shop: Shop,
    actor: User,
    target: User,
):
    locked_shop = lock_shop_for_update(session, shop_id=ShopId(shop.id))
    assert locked_shop is not None
    locked_users = lock_actor_and_target_users_for_update(
        session,
        actor_user_id=actor.id,
        target_user_id=target.id,
    )
    assert locked_users is not None
    locked_customer = lock_active_customer_for_target_user(
        session,
        locked_users=locked_users,
    )
    assert locked_customer is not None
    return _mark_shop_customer_predecessors_locked(
        session,
        locked_shop=locked_shop,
        locked_customer=locked_customer,
    )


@pytest.mark.integration
def test_user_and_customer_locks_follow_uuid_order_and_active_target(
    db_session: Session,
) -> None:
    target = _add_user(db_session, user_id=UUID(int=10))
    actor = _add_user(db_session, user_id=UUID(int=20))
    active_customer = _add_active_customer(db_session, user=target)

    locked_users = lock_actor_and_target_users_for_update(
        db_session,
        actor_user_id=actor.id,
        target_user_id=target.id,
    )

    assert locked_users is not None
    assert locked_users.actor is actor
    assert locked_users.target is target
    assert "<redacted>" in repr(locked_users)
    locked_customer = lock_active_customer_for_target_user(
        db_session,
        locked_users=locked_users,
    )
    assert locked_customer is not None
    assert locked_customer.customer is active_customer
    assert "<redacted>" in repr(locked_customer)
    assert ".order_by(User.id.asc())" in inspect.getsource(
        lock_actor_and_target_users_for_update
    )


@pytest.mark.integration
def test_shop_customer_reads_are_tenant_or_own_customer_scoped(
    db_session: Session,
) -> None:
    actor = _add_user(db_session)
    target = _add_user(db_session)
    customer = _add_active_customer(db_session, user=target)
    first_shop = _add_shop(db_session, name="First tenant")
    second_shop = _add_shop(db_session, name="Second tenant")
    row_id = ShopCustomerId(uuid4())
    predecessors = _locked_predecessors(
        db_session,
        shop=first_shop,
        actor=actor,
        target=target,
    )
    defaults = read_locked_shop_defaults(
        db_session,
        locked_shop=predecessors.locked_shop,
    )
    row = insert_shop_customer(
        db_session,
        locked_predecessors=predecessors,
        shop_customer_id=row_id,
        snapshot=defaults.for_new_link(),
        created_by_user_id=UserId(actor.id),
        now=NOW,
    )
    db_session.flush()

    assert list_shop_customers_by_shop(db_session, shop_id=ShopId(first_shop.id)) == [
        row
    ]
    assert list_shop_customers_by_shop(db_session, shop_id=ShopId(second_shop.id)) == []
    assert (
        get_shop_customer_by_shop(
            db_session,
            shop_id=ShopId(first_shop.id),
            shop_customer_id=row_id,
        )
        is row
    )
    assert (
        get_shop_customer_by_shop(
            db_session,
            shop_id=ShopId(second_shop.id),
            shop_customer_id=row_id,
        )
        is None
    )
    assert list_customer_own_shop_customers(
        db_session,
        customer_id=CustomerId(customer.id),
    ) == [row]
    assert (
        list_customer_own_shop_customers(
            db_session,
            customer_id=CustomerId(uuid4()),
        )
        == []
    )


@pytest.mark.integration
def test_pair_and_tenant_locks_gate_insert_and_complete_update(
    db_session: Session,
) -> None:
    actor = _add_user(db_session)
    target = _add_user(db_session)
    _add_active_customer(db_session, user=target)
    shop = _add_shop(db_session, name="Marker tenant")
    predecessors = _locked_predecessors(
        db_session,
        shop=shop,
        actor=actor,
        target=target,
    )

    assert (
        lock_shop_customer_by_pair(
            db_session,
            locked_predecessors=predecessors,
        )
        is None
    )
    row_id = ShopCustomerId(uuid4())
    row = insert_shop_customer(
        db_session,
        locked_predecessors=predecessors,
        shop_customer_id=row_id,
        snapshot=read_locked_shop_defaults(
            db_session,
            locked_shop=predecessors.locked_shop,
        ).for_new_link(),
        created_by_user_id=UserId(actor.id),
        now=NOW,
    )
    db_session.flush()
    locked_row = lock_shop_customer_by_tenant_locator(
        db_session,
        locked_shop=predecessors.locked_shop,
        shop_customer_id=row_id,
    )
    assert locked_row is not None
    replacement = ShopCustomerPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal("2500000")),
        max_open_debts=MaxOpenDebts(4),
        list_status=ShopCustomerListStatus.WHITELISTED,
    )

    updated = update_locked_shop_customer(
        db_session,
        locked_shop_customer=locked_row,
        policy=replacement,
        revision=ShopCustomerRevision(2),
        now=NOW + timedelta(minutes=1),
    )

    assert updated is row
    assert updated.credit_limit_uzs == Decimal("2500000")
    assert updated.max_open_debts == 4
    assert updated.list_status == ShopCustomerListStatus.WHITELISTED.value
    assert updated.revision == 2


@pytest.mark.integration
def test_shop_default_update_requires_locked_shop_and_is_complete(
    db_session: Session,
) -> None:
    shop = _add_shop(db_session, name="Defaults tenant")
    locked_shop = lock_shop_for_update(db_session, shop_id=ShopId(shop.id))
    assert locked_shop is not None
    replacement = ShopDefaultCreditPolicy(
        credit_limit=CreditLimitUzbekistanSom(Decimal("5000000")),
        max_open_debts=MaxOpenDebts(5),
    )

    updated = update_locked_shop_defaults(
        db_session,
        locked_shop=locked_shop,
        defaults=replacement,
        now=NOW + timedelta(minutes=1),
    )

    assert updated is shop
    assert (
        read_locked_shop_defaults(
            db_session,
            locked_shop=locked_shop,
        )
        == replacement
    )


def test_repository_surface_is_marker_ordered_and_borrowed_session_safe() -> None:
    source = Path("app/shop_customer/repository.py").read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "select(ShopCustomer)" in source
    assert "ShopCustomer.shop_id ==" in source
    assert "ShopCustomer.customer_id ==" in source
    assert "select(ShopCustomer).where(ShopCustomer.id" not in source
    assert "_validate_locked_shop_token" in source
    assert "_validate_locked_active_target_customer" in source
