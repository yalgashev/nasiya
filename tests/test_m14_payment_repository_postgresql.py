from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.debt.repository import mark_debt_predecessor_locked
from app.debt.values import DebtId, ShopCustomerId
from app.payment.models import Payment
from app.payment.repository import (
    SqlAlchemyPaymentOpenSetReader,
    get_customer_owned_payment,
    get_tenant_payment,
    historical_balance_after,
    list_customer_owned_debt_payments,
    list_tenant_debt_payments,
    payment_aggregate_from_row,
    posted_payment_total,
    remaining_due,
)
from app.payment.values import PaymentId
from app.shop.models import Shop
from app.shop.repository import lock_shop_for_update
from app.shop.values import ShopId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.repository import lock_shop_customer_by_tenant_locator

NOW = datetime(2026, 8, 9, 8, tzinfo=UTC)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _tenant(
    session: Session, *, name: str
) -> tuple[User, Customer, Shop, ShopCustomer]:
    actor = User(phone=_phone(), is_active=True)
    customer_user = User(phone=_phone(), is_active=True)
    session.add_all((actor, customer_user))
    session.flush()
    customer = Customer(
        user_id=customer_user.id,
        onboarding_status="active",
        activated_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    shop = Shop(name=name, phone=_phone(), created_at=NOW, updated_at=NOW)
    session.add_all((customer, shop))
    session.flush()
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=5,
        list_status="normal",
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(relation)
    session.flush()
    return actor, customer, shop, relation


def _debt(
    session: Session,
    *,
    relation: ShopCustomer,
    actor: User,
    original: str,
    discounted: str,
    status: str,
    revision: int,
) -> Debt:
    accepted_at = NOW + timedelta(hours=1) if status in {"active", "paid"} else None
    paid_at = NOW + timedelta(hours=4) if status == "paid" else None
    row = Debt(
        shop_customer_id=relation.id,
        created_by_user_id=actor.id,
        original_amount_uzs=Decimal(original),
        discount_basis_points=0,
        discounted_amount_uzs=Decimal(discounted),
        due_date=date(2026, 8, 20),
        pending_expires_at=NOW + timedelta(hours=72),
        status=status,
        revision=revision,
        accepted_at=accepted_at,
        paid_at=paid_at,
        created_at=NOW,
        updated_at=paid_at or accepted_at or NOW,
    )
    session.add(row)
    session.flush()
    return row


def _payment(
    session: Session,
    *,
    debt: Debt,
    actor: User,
    amount: str,
    revision: int,
    method: str = "cash",
) -> Payment:
    row = Payment(
        id=uuid4(),
        debt_id=debt.id,
        recorded_by_user_id=actor.id,
        amount_uzs=Decimal(amount),
        method=method,
        debt_revision_after=revision,
        created_at=NOW + timedelta(hours=revision),
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.integration
def test_tenant_and_customer_payment_reads_are_join_scoped_and_ordered(
    db_session: Session,
) -> None:
    actor, customer, shop, relation = _tenant(db_session, name="Tenant A")
    debt = _debt(
        db_session,
        relation=relation,
        actor=actor,
        original="1000",
        discounted="800",
        status="active",
        revision=4,
    )
    second = _payment(
        db_session, debt=debt, actor=actor, amount="300", revision=4, method="card"
    )
    first = _payment(db_session, debt=debt, actor=actor, amount="200", revision=3)
    other_actor, other_customer, other_shop, other_relation = _tenant(
        db_session, name="Tenant B"
    )
    other_debt = _debt(
        db_session,
        relation=other_relation,
        actor=other_actor,
        original="500",
        discounted="500",
        status="active",
        revision=3,
    )
    other_payment = _payment(
        db_session, debt=other_debt, actor=other_actor, amount="100", revision=3
    )

    tenant_rows = list_tenant_debt_payments(
        db_session, shop_id=ShopId(shop.id), debt_id=DebtId(debt.id)
    )
    own_rows = list_customer_owned_debt_payments(
        db_session, customer_id=customer.id, debt_id=DebtId(debt.id)
    )

    assert [row.payment.id for row in tenant_rows] == [first.id, second.id]
    assert [row.payment.id for row in own_rows] == [first.id, second.id]
    assert all(row.shop_name == "Tenant A" for row in tenant_rows)
    assert (
        get_tenant_payment(
            db_session, shop_id=ShopId(shop.id), payment_id=PaymentId(other_payment.id)
        )
        is None
    )
    assert (
        get_customer_owned_payment(
            db_session,
            customer_id=customer.id,
            payment_id=PaymentId(other_payment.id),
        )
        is None
    )
    assert (
        get_tenant_payment(
            db_session, shop_id=ShopId(other_shop.id), payment_id=PaymentId(first.id)
        )
        is None
    )
    assert (
        get_customer_owned_payment(
            db_session,
            customer_id=other_customer.id,
            payment_id=PaymentId(first.id),
        )
        is None
    )
    aggregate = payment_aggregate_from_row(first)
    assert aggregate.id == PaymentId(first.id)
    assert aggregate.debt_id == DebtId(debt.id)


@pytest.mark.integration
def test_historical_current_balance_and_payment_aware_exposure_are_derived(
    db_session: Session,
) -> None:
    actor, _customer, shop, relation = _tenant(db_session, name="Balances")
    active = _debt(
        db_session,
        relation=relation,
        actor=actor,
        original="1000",
        discounted="800",
        status="active",
        revision=4,
    )
    first = _payment(db_session, debt=active, actor=actor, amount="200", revision=3)
    _payment(db_session, debt=active, actor=actor, amount="300", revision=4)
    paid = _debt(
        db_session,
        relation=relation,
        actor=actor,
        original="900",
        discounted="600",
        status="paid",
        revision=3,
    )
    _payment(db_session, debt=paid, actor=actor, amount="600", revision=3)
    _debt(
        db_session,
        relation=relation,
        actor=actor,
        original="400",
        discounted="400",
        status="pending",
        revision=1,
    )

    assert posted_payment_total(db_session, debt_id=DebtId(active.id)).value == Decimal(
        "500"
    )
    assert historical_balance_after(
        db_session, debt=active, payment=first
    ).value == Decimal("600")
    assert remaining_due(db_session, debt=active).value == Decimal("300")

    locked_shop = lock_shop_for_update(db_session, shop_id=ShopId(shop.id))
    assert locked_shop is not None
    locked_relation = lock_shop_customer_by_tenant_locator(
        db_session,
        locked_shop=locked_shop,
        shop_customer_id=ShopCustomerId(relation.id),
    )
    assert locked_relation is not None
    predecessor = mark_debt_predecessor_locked(
        db_session, locked_shop_customer=locked_relation
    )
    reader = SqlAlchemyPaymentOpenSetReader(db_session, locked_predecessor=predecessor)

    assert reader.read_open_debt_exposure(
        shop_customer_id=ShopCustomerId(relation.id)
    ).value == Decimal("900")
    assert (
        reader.read_open_debt_count(shop_customer_id=ShopCustomerId(relation.id)).value
        == 2
    )


@pytest.mark.integration
def test_payment_open_set_reader_rejects_foreign_parent_and_session_tokens(
    db_session: Session, m2_test_database: Engine
) -> None:
    actor, _customer, shop, relation = _tenant(db_session, name="Lock proof")
    _debt(
        db_session,
        relation=relation,
        actor=actor,
        original="100",
        discounted="100",
        status="pending",
        revision=1,
    )
    locked_shop = lock_shop_for_update(db_session, shop_id=ShopId(shop.id))
    assert locked_shop is not None
    locked_relation = lock_shop_customer_by_tenant_locator(
        db_session,
        locked_shop=locked_shop,
        shop_customer_id=ShopCustomerId(relation.id),
    )
    assert locked_relation is not None
    predecessor = mark_debt_predecessor_locked(
        db_session, locked_shop_customer=locked_relation
    )
    reader = SqlAlchemyPaymentOpenSetReader(db_session, locked_predecessor=predecessor)

    with pytest.raises(ValueError, match="not locked predecessor"):
        reader.read_open_debt_exposure(shop_customer_id=ShopCustomerId(uuid4()))

    other_session = create_database_session_factory(m2_test_database)()
    try:
        with pytest.raises(RuntimeError, match="different SQLAlchemy session"):
            SqlAlchemyPaymentOpenSetReader(
                other_session, locked_predecessor=predecessor
            )
    finally:
        other_session.rollback()
        other_session.close()
