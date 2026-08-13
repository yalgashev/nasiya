from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.models import User
from app.customer.models import Customer
from app.debt.business_time import tashkent_business_date
from app.debt.commands import WriteOffDebtCommand
from app.debt.contracts import WriteOffReason
from app.debt.models import Debt
from app.debt.values import DebtId, DebtRevision
from app.debt.write_off_service import write_off_overdue_debt
from app.idempotency.contracts import (
    IdempotencyOutcome,
    create_write_off_debt_request_hash_v1,
    parse_idempotency_key,
)
from app.idempotency.models import IdempotencyKey
from app.offers.authorization import require_platform_admin_actor
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.rating.models import RatingEvent
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer

CREATED = datetime(2026, 8, 1, tzinfo=UTC)
OVERDUE = datetime(2026, 8, 6, tzinfo=UTC)
WRITTEN_OFF = OVERDUE + timedelta(days=1)


def _seed_source(session: Session) -> tuple[User, Debt]:
    admin = User(
        phone=f"+998{uuid4().int % 1_000_000_000:09d}",
        is_active=True,
        is_platform_admin=True,
    )
    customer_user = User(
        phone=f"+998{uuid4().int % 1_000_000_000:09d}", is_active=False
    )
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
        name=f"M17 write-off {uuid4().hex[:8]}",
        phone=f"+998{uuid4().int % 1_000_000_000:09d}",
        status="suspended",
        created_at=CREATED,
        updated_at=CREATED,
    )
    session.add_all((customer, shop))
    session.flush()
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=2,
        list_status="normal",
        revision=1,
        created_by_user_id=admin.id,
        created_at=CREATED,
        updated_at=CREATED,
    )
    session.add(relation)
    session.flush()
    debt = Debt(
        id=uuid4(),
        shop_customer_id=relation.id,
        created_by_user_id=admin.id,
        original_amount_uzs=Decimal("100000"),
        discount_basis_points=1000,
        discounted_amount_uzs=Decimal("90000"),
        due_date=date(2026, 8, 4),
        pending_expires_at=CREATED + timedelta(hours=72),
        status="overdue",
        revision=3,
        accepted_at=CREATED + timedelta(days=1),
        overdue_at=OVERDUE,
        overdue_revision=3,
        created_at=CREATED,
        updated_at=OVERDUE,
    )
    session.add(debt)
    session.flush()
    session.add(
        RatingEvent(
            id=uuid4(),
            shop_customer_id=relation.id,
            debt_id=debt.id,
            event_type="overdue",
            delta=-15,
            occurred_at=OVERDUE,
            business_date=tashkent_business_date(OVERDUE),
            recording_source="live",
        )
    )
    session.add_all(
        (
            AuditLog(
                event_type="debt.overdue",
                actor_kind="SYSTEM",
                actor_user_id=None,
                object_type="debt",
                object_id=debt.id,
                payload={
                    "source": "batch",
                    "from_status": "active",
                    "to_status": "overdue",
                    "overdue_revision": 3,
                    "business_date": "2026-08-06",
                },
                occurred_at=OVERDUE,
            ),
            AuditLog(
                event_type="debt.clawback_applied",
                actor_kind="SYSTEM",
                actor_user_id=None,
                object_type="debt",
                object_id=debt.id,
                payload={
                    "source": "batch",
                    "from_basis": "discounted",
                    "to_basis": "original",
                    "balance_increase_uzs": 10000,
                    "overdue_revision": 3,
                },
                occurred_at=OVERDUE,
            ),
        )
    )
    session.flush()
    return admin, debt


def _command(admin: User, debt: Debt, *, key=None) -> WriteOffDebtCommand:
    actor = require_platform_admin_actor(admin)
    debt_id = DebtId(debt.id)
    revision = DebtRevision(3)
    reason = WriteOffReason.COLLECTION_EXHAUSTED
    return WriteOffDebtCommand(
        actor=actor,
        debt_id=debt_id,
        expected_revision=revision,
        reason=reason,
        idempotency_key=parse_idempotency_key(str(key or uuid4())),
        request_hash=create_write_off_debt_request_hash_v1(
            actor_user_id=admin.id,
            debt_id=debt_id,
            expected_revision=revision,
            reason=reason,
        ),
    )


@pytest.mark.integration
def test_atomic_write_off_appends_exact_source_audit_and_replays_without_clock(
    m2_test_database: Engine,
) -> None:
    key = uuid4()
    with Session(m2_test_database) as session, session.begin():
        admin, debt = _seed_source(session)
        admin_id = admin.id
        debt_id = debt.id
        command = _command(admin, debt, key=key)
        result = write_off_overdue_debt(
            session,
            command=command,
            rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
            clock=lambda: WRITTEN_OFF,
        )
        assert result.outcome is IdempotencyOutcome.NEW
        assert debt.status == "written_off"
        assert debt.written_off_at == WRITTEN_OFF
        assert debt.written_off_actor_user_id == admin.id
        assert (
            session.scalar(
                select(func.count())
                .select_from(RatingEvent)
                .where(
                    RatingEvent.debt_id == debt.id,
                    RatingEvent.event_type == "written_off",
                    RatingEvent.delta == -40,
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.object_id == debt.id,
                    AuditLog.event_type == "debt.written_off",
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyKey)
                .where(IdempotencyKey.endpoint == "admin.debts.write_off")
            )
            == 1
        )

    with Session(m2_test_database) as session, session.begin():
        admin = session.scalar(select(User).where(User.id == admin_id))
        debt = session.get_one(Debt, debt_id)
        assert admin is not None
        replay = write_off_overdue_debt(
            session,
            command=_command(admin, debt, key=key),
            rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
            clock=lambda: (_ for _ in ()).throw(AssertionError("clock called")),
        )
        assert replay.outcome is IdempotencyOutcome.REPLAY


@pytest.mark.integration
def test_write_off_fault_rolls_back_entire_unit(m2_test_database: Engine) -> None:
    class FaultPort(SqlAlchemyLockedRatingAppendAdapter):
        def append_pending_written_off(self, session, *, locked_source, effect):
            raise RuntimeError("synthetic rating fault")

    with Session(m2_test_database) as session, session.begin():
        admin, debt = _seed_source(session)
        admin_id = admin.id
        debt_id = debt.id

    with pytest.raises(RuntimeError, match="synthetic rating fault"):
        with Session(m2_test_database) as session, session.begin():
            admin = session.get_one(User, admin_id)
            debt = session.get_one(Debt, debt_id)
            write_off_overdue_debt(
                session,
                command=_command(admin, debt),
                rating_append_port=FaultPort(),
                clock=lambda: WRITTEN_OFF,
            )
    with Session(m2_test_database) as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "overdue"
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyKey)
                .where(IdempotencyKey.endpoint == "admin.debts.write_off")
            )
            == 0
        )
