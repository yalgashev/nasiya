from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.contracts import DebtAggregate
from app.debt.models import Debt
from app.debt.overdue_ports import LockedCustomerGlobalHardBlockReadPort
from app.debt.overdue_targeting import (
    discover_overdue_candidates,
    locked_overdue_debt_row,
    resolve_and_lock_overdue_candidate,
)
from app.debt.repository import (
    LockedDebtPredecessor,
    SqlAlchemyLockedCustomerGlobalHardBlockReader,
    debt_aggregate_from_row,
    lock_customer_hard_block_scope,
    update_locked_debt,
)
from app.debt.values import CustomerId, DebtRevision, ShopCustomerId
from app.payment.models import Payment
from app.payment.repository import SqlAlchemyPaymentOpenSetReader
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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


def _customer(session: Session) -> tuple[User, Customer]:
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
    session.add(customer)
    session.flush()
    return actor, customer


def _relation(
    session: Session, *, actor: User, customer: Customer, suffix: str
) -> ShopCustomer:
    shop = Shop(
        name=f"M15 repository {suffix} {uuid4().hex[:8]}",
        phone=_phone(),
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(shop)
    session.flush()
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=20,
        list_status="normal",
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(relation)
    session.flush()
    return relation


def _debt(
    session: Session,
    *,
    actor: User,
    relation: ShopCustomer,
    status: str,
    original: int,
    discounted: int,
    due_date: date,
    debt_id: UUID | None = None,
) -> Debt:
    accepted_at = (
        NOW + timedelta(hours=1) if status in {"active", "overdue", "paid"} else None
    )
    overdue_at = NOW + timedelta(hours=2) if status in {"overdue", "paid"} else None
    paid_at = NOW + timedelta(hours=3) if status == "paid" else None
    revision = {"pending": 1, "active": 2, "overdue": 3, "paid": 4}[status]
    row = Debt(
        id=debt_id or uuid4(),
        shop_customer_id=relation.id,
        created_by_user_id=actor.id,
        original_amount_uzs=Decimal(original),
        discount_basis_points=1000,
        discounted_amount_uzs=Decimal(discounted),
        due_date=due_date,
        pending_expires_at=NOW + timedelta(hours=72),
        status=status,
        revision=revision,
        accepted_at=accepted_at,
        overdue_at=overdue_at,
        overdue_revision=3 if overdue_at is not None else None,
        paid_at=paid_at,
        created_at=NOW,
        updated_at=paid_at or overdue_at or accepted_at or NOW,
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.integration
def test_scalar_candidate_order_and_forward_locked_chain_are_authoritative(
    db_session: Session,
) -> None:
    actor, customer = _customer(db_session)
    first_relation = _relation(db_session, actor=actor, customer=customer, suffix="a")
    second_relation = _relation(db_session, actor=actor, customer=customer, suffix="b")
    identifiers = (UUID(int=101), UUID(int=102), UUID(int=103))
    later = _debt(
        db_session,
        actor=actor,
        relation=first_relation,
        status="active",
        original=100,
        discounted=90,
        due_date=date(2026, 8, 8),
        debt_id=identifiers[2],
    )
    first = _debt(
        db_session,
        actor=actor,
        relation=second_relation,
        status="active",
        original=100,
        discounted=90,
        due_date=date(2026, 8, 7),
        debt_id=identifiers[0],
    )
    second = _debt(
        db_session,
        actor=actor,
        relation=first_relation,
        status="active",
        original=100,
        discounted=90,
        due_date=date(2026, 8, 7),
        debt_id=identifiers[1],
    )

    candidates = discover_overdue_candidates(
        db_session, as_of_business_date=date(2026, 8, 9), limit=3
    )

    assert tuple(item.debt_id.as_uuid() for item in candidates) == (
        first.id,
        second.id,
        later.id,
    )
    assert all(str(identifier) not in repr(candidates) for identifier in identifiers)
    locked = resolve_and_lock_overdue_candidate(db_session, candidate=candidates[0])
    assert locked is not None
    assert locked_overdue_debt_row(db_session, locked) is first
    assert str(first.id) not in repr(locked)


@pytest.mark.integration
def test_locked_customer_hard_block_is_cross_shop_effective_and_persisted(
    db_session: Session,
) -> None:
    actor, customer = _customer(db_session)
    first_relation = _relation(db_session, actor=actor, customer=customer, suffix="a")
    second_relation = _relation(db_session, actor=actor, customer=customer, suffix="b")
    effective = _debt(
        db_session,
        actor=actor,
        relation=first_relation,
        status="active",
        original=100,
        discounted=90,
        due_date=date(2026, 8, 8),
    )
    scope = lock_customer_hard_block_scope(
        db_session, customer_id=CustomerId(customer.id)
    )
    assert scope is not None
    reader = SqlAlchemyLockedCustomerGlobalHardBlockReader(
        db_session, locked_customer=scope
    )
    assert isinstance(reader, LockedCustomerGlobalHardBlockReadPort)
    assert reader.read_global_hard_block(
        customer_id=CustomerId(customer.id),
        as_of_business_date=date(2026, 8, 9),
    ).is_blocked

    effective.status = "paid"
    effective.revision = 3
    effective.paid_at = NOW + timedelta(hours=2)
    effective.updated_at = effective.paid_at
    db_session.flush()
    assert not reader.read_global_hard_block(
        customer_id=CustomerId(customer.id),
        as_of_business_date=date(2026, 8, 9),
    ).is_blocked

    _debt(
        db_session,
        actor=actor,
        relation=second_relation,
        status="overdue",
        original=100,
        discounted=90,
        due_date=date(2026, 8, 9),
    )
    assert reader.read_global_hard_block(
        customer_id=CustomerId(customer.id),
        as_of_business_date=date(2026, 8, 9),
    ).is_blocked


@pytest.mark.integration
def test_payment_aware_exposure_and_count_include_overdue_original_basis(
    db_session: Session,
) -> None:
    actor, customer = _customer(db_session)
    relation = _relation(db_session, actor=actor, customer=customer, suffix="open")
    pending = _debt(
        db_session,
        actor=actor,
        relation=relation,
        status="pending",
        original=100,
        discounted=90,
        due_date=date(2026, 8, 12),
    )
    active = _debt(
        db_session,
        actor=actor,
        relation=relation,
        status="active",
        original=200,
        discounted=180,
        due_date=date(2026, 8, 12),
    )
    overdue = _debt(
        db_session,
        actor=actor,
        relation=relation,
        status="overdue",
        original=300,
        discounted=270,
        due_date=date(2026, 8, 8),
    )
    paid = _debt(
        db_session,
        actor=actor,
        relation=relation,
        status="paid",
        original=400,
        discounted=360,
        due_date=date(2026, 8, 7),
    )
    db_session.add_all(
        (
            Payment(
                id=uuid4(),
                debt_id=active.id,
                recorded_by_user_id=actor.id,
                amount_uzs=Decimal("50"),
                method="cash",
                debt_revision_after=3,
                created_at=NOW + timedelta(hours=2),
            ),
            Payment(
                id=uuid4(),
                debt_id=overdue.id,
                recorded_by_user_id=actor.id,
                amount_uzs=Decimal("100"),
                method="card",
                debt_revision_after=4,
                created_at=NOW + timedelta(hours=3),
            ),
            Payment(
                id=uuid4(),
                debt_id=paid.id,
                recorded_by_user_id=actor.id,
                amount_uzs=Decimal("400"),
                method="transfer",
                debt_revision_after=4,
                created_at=NOW + timedelta(hours=3),
            ),
        )
    )
    db_session.flush()
    token = LockedDebtPredecessor(
        shop_customer_id=relation.id,
        customer_id=customer.id,
        _session=db_session,
    )
    reader = SqlAlchemyPaymentOpenSetReader(db_session, locked_predecessor=token)

    assert reader.read_open_debt_exposure(
        shop_customer_id=ShopCustomerId(relation.id)
    ).value == Decimal("450")
    assert (
        reader.read_open_debt_count(shop_customer_id=ShopCustomerId(relation.id)).value
        == 3
    )
    assert pending.id is not None


@pytest.mark.integration
def test_overdue_metadata_round_trips_and_locked_update_preserves_marker(
    db_session: Session,
) -> None:
    actor, customer = _customer(db_session)
    relation = _relation(db_session, actor=actor, customer=customer, suffix="map")
    row = _debt(
        db_session,
        actor=actor,
        relation=relation,
        status="overdue",
        original=1000,
        discounted=900,
        due_date=date(2026, 8, 12),
    )

    aggregate = debt_aggregate_from_row(row)

    assert isinstance(aggregate, DebtAggregate)
    assert aggregate.overdue_at == row.overdue_at
    assert aggregate.overdue_revision == DebtRevision(3)
    updated = aggregate.record_payment(
        payment_amount_uzs=Decimal("100"),
        current_remaining_due_uzs=Decimal("1000"),
        expected_revision=DebtRevision(3),
        payment_created_at=NOW + timedelta(hours=3),
    )
    update_locked_debt(db_session, row=row, debt=updated)
    assert row.status == "overdue"
    assert row.revision == 4
    assert row.overdue_revision == 3
    assert row.overdue_at == NOW + timedelta(hours=2)


def test_repository_sources_have_no_transaction_ownership_or_inverse_lock_path() -> (
    None
):
    targeting = (PROJECT_ROOT / "app/debt/overdue_targeting.py").read_text(
        encoding="utf-8"
    )
    repository = (PROJECT_ROOT / "app/debt/repository.py").read_text(encoding="utf-8")
    payment_repository = (PROJECT_ROOT / "app/payment/repository.py").read_text(
        encoding="utf-8"
    )

    lock_body = targeting.split("def resolve_and_lock_overdue_candidate", 1)[1]
    assert lock_body.index("lock_shop_for_update") < lock_body.index(
        "lock_customer_hard_block_scope"
    )
    assert lock_body.index("select(ShopCustomer)") < lock_body.index("select(Debt)")
    for source in (targeting, repository, payment_repository):
        assert ".commit(" not in source
        assert ".rollback(" not in source
        assert ".close(" not in source
    assert "app.payment" not in targeting
    assert "app.payment" not in repository
