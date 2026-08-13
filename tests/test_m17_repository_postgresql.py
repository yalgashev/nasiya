from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import Customer
from app.debt.contracts import WriteOffReason
from app.debt.models import Debt
from app.debt.repository import (
    debt_aggregate_from_row,
    discover_written_off_candidates,
    lock_scoped_write_off_debt,
    update_locked_debt,
    validate_locked_write_off_debt,
)
from app.debt.values import DebtRevision, ShopCustomerId
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer

CREATED = datetime(2026, 8, 1, tzinfo=UTC)
ACCEPTED = datetime(2026, 8, 2, tzinfo=UTC)


def _parents(session: Session) -> tuple[User, Shop, Customer, ShopCustomer]:
    admin = User(
        phone=f"+998{uuid4().int % 1_000_000_000:09d}",
        is_active=True,
        is_platform_admin=True,
    )
    customer_user = User(phone=f"+998{uuid4().int % 1_000_000_000:09d}", is_active=True)
    session.add_all((admin, customer_user))
    session.flush()
    customer = Customer(
        user_id=customer_user.id,
        onboarding_status="active",
        activated_at=CREATED,
        created_at=CREATED,
        updated_at=CREATED,
    )
    shop = Shop(
        name=f"M17 repository {uuid4().hex[:8]}",
        phone=f"+998{uuid4().int % 1_000_000_000:09d}",
        created_at=CREATED,
        updated_at=CREATED,
    )
    session.add_all((customer, shop))
    session.flush()
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=100,
        list_status="normal",
        revision=1,
        created_by_user_id=admin.id,
        created_at=CREATED,
        updated_at=CREATED,
    )
    session.add(relation)
    session.flush()
    return admin, shop, customer, relation


def _overdue(
    *, relation: ShopCustomer, actor: User, overdue_at: datetime, debt_id=None
) -> Debt:
    return Debt(
        id=debt_id or uuid4(),
        shop_customer_id=relation.id,
        created_by_user_id=actor.id,
        original_amount_uzs=Decimal("100000"),
        discount_basis_points=1000,
        discounted_amount_uzs=Decimal("90000"),
        due_date=date(2026, 8, 4),
        pending_expires_at=CREATED + timedelta(hours=72),
        status="overdue",
        revision=3,
        accepted_at=ACCEPTED,
        overdue_at=overdue_at,
        overdue_revision=3,
        created_at=CREATED,
        updated_at=overdue_at,
    )


@pytest.mark.integration
def test_candidate_query_is_detached_ordered_and_page_50_bounded(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, _shop, _customer, relation = _parents(session)
        debts = [
            _overdue(
                relation=relation,
                actor=actor,
                overdue_at=CREATED + timedelta(days=5, microseconds=index // 2),
                debt_id=uuid4(),
            )
            for index in range(51)
        ]
        session.add_all(reversed(debts))
        session.flush()
        expected = sorted(debts, key=lambda row: (row.overdue_at, row.id))[:50]
        candidates = discover_written_off_candidates(session)
        assert len(candidates) == 50
        assert [item.debt_id.as_uuid() for item in candidates] == [
            row.id for row in expected
        ]
        assert all("redacted" in repr(item) for item in candidates)
        assert all(not hasattr(item, "_sa_instance_state") for item in candidates)


@pytest.mark.integration
def test_locked_mapping_and_marker_update_stay_session_owned(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, shop, customer, relation = _parents(session)
        row = _overdue(
            relation=relation,
            actor=actor,
            overdue_at=CREATED + timedelta(days=5),
        )
        session.add(row)
        session.flush()
        token = lock_scoped_write_off_debt(
            session,
            shop_id=shop.id,
            customer_id=customer.id,
            shop_customer_id=ShopCustomerId(relation.id),
            debt_id=debt_aggregate_from_row(row).id,
        )
        assert token is not None
        locked = validate_locked_write_off_debt(session, token)
        aggregate = debt_aggregate_from_row(locked._row)
        written_off = aggregate.mark_written_off(
            now=CREATED + timedelta(days=6),
            actor_user_id=actor.id,
            reason=WriteOffReason.COLLECTION_EXHAUSTED,
            posted_total_uzs=Decimal("1"),
            expected_revision=DebtRevision(3),
        )
        updated = update_locked_debt(session, row=locked._row, debt=written_off)
        assert updated.status == "written_off"
        assert updated.written_off_revision == 4
        assert updated.written_off_actor_user_id == actor.id
        assert updated.written_off_reason == "collection_exhausted"
        assert session.in_transaction()
        assert "redacted" in repr(token)


@pytest.mark.integration
def test_scoped_lock_rejects_foreign_parent_chain(m2_test_database: Engine) -> None:
    with Session(m2_test_database) as session, session.begin():
        actor, shop, customer, relation = _parents(session)
        row = _overdue(
            relation=relation,
            actor=actor,
            overdue_at=CREATED + timedelta(days=5),
        )
        session.add(row)
        session.flush()
        assert (
            lock_scoped_write_off_debt(
                session,
                shop_id=uuid4(),
                customer_id=customer.id,
                shop_customer_id=ShopCustomerId(relation.id),
                debt_id=debt_aggregate_from_row(row).id,
            )
            is None
        )
        assert (
            lock_scoped_write_off_debt(
                session,
                shop_id=shop.id,
                customer_id=uuid4(),
                shop_customer_id=ShopCustomerId(relation.id),
                debt_id=debt_aggregate_from_row(row).id,
            )
            is None
        )
