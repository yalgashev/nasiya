from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.enums import DebtOverdueSource
from app.debt.models import Debt
from app.debt.overdue_service import materialize_overdue_candidate
from app.debt.rating_ports import PendingOverdueRatingEffect
from app.debt.values import DebtId, DebtRevision
from app.idempotency.models import IdempotencyKey
from app.payment.models import Payment
from app.payment.rating_ports import PendingOnTimePaidRatingEffect
from app.payment.repository import SqlAlchemyLockedDebtPostedTotalReader
from app.payment.service import record_debt_payment
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.rating.contracts import create_on_time_paid_rating_event
from app.rating.enums import RatingEventAppendOutcome, RatingRecordingSource
from app.rating.models import RatingEvent
from app.rating.ports import LockedRatingSourceScope
from app.rating.service import append_locked_source_event
from app.rating.values import RatingEventId
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import CustomerId, ShopCustomerId
from tests.test_m15_overdue_service_postgresql import NOW as OVERDUE_NOW
from tests.test_m15_overdue_service_postgresql import _seed_debt
from tests.test_payment_service_postgresql import PAYMENT_TIME, _command
from tests.test_payment_targeting_postgresql import _seed_one


def _make_eligible_debts(engine: Engine, *, count: int) -> tuple:
    actor_id, shop_id, _staff_id, relation_id, first_id = _seed_one(engine)
    factory = create_database_session_factory(engine)
    debt_ids = [first_id]
    with factory.begin() as session:
        first = session.get_one(Debt, first_id)
        first.original_amount_uzs = Decimal("100000")
        first.discounted_amount_uzs = Decimal("100000")
        for offset in range(1, count):
            created_at = first.created_at + timedelta(microseconds=offset)
            row = Debt(
                shop_customer_id=relation_id,
                created_by_user_id=actor_id,
                original_amount_uzs=Decimal("100000"),
                discount_basis_points=0,
                discounted_amount_uzs=Decimal("100000"),
                due_date=first.due_date,
                pending_expires_at=created_at + timedelta(hours=72),
                status="active",
                revision=2,
                accepted_at=first.accepted_at,
                created_at=created_at,
                updated_at=first.accepted_at,
            )
            session.add(row)
            session.flush()
            debt_ids.append(row.id)
    return actor_id, shop_id, relation_id, tuple(debt_ids)


@pytest.mark.integration
def test_exact_payoff_replay_and_daily_cap_are_atomic(m2_test_database: Engine) -> None:
    actor_id, shop_id, relation_id, debt_ids = _make_eligible_debts(
        m2_test_database, count=2
    )
    factory = create_database_session_factory(m2_test_database)
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    first_actor, first_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )
    second_actor, second_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[1],
        amount="100000",
        revision=2,
        key=uuid4(),
    )

    with factory.begin() as session:
        first = record_debt_payment(
            session,
            actor=first_actor,
            command=first_command,
            rating_append_port=adapter,
            payment_clock=lambda: PAYMENT_TIME,
        )
    with factory.begin() as session:
        replay = record_debt_payment(
            session,
            actor=first_actor,
            command=first_command,
            rating_append_port=adapter,
            payment_clock=lambda: pytest.fail("replay captured a new clock"),
        )
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=second_actor,
            command=second_command,
            rating_append_port=adapter,
            payment_clock=lambda: PAYMENT_TIME,
        )

    assert replay.payment_id == first.payment_id
    with factory() as session:
        events = list(
            session.scalars(
                select(RatingEvent).where(RatingEvent.shop_customer_id == relation_id)
            )
        )
        assert len(events) == 1
        assert events[0].debt_id == debt_ids[0]
        assert events[0].event_type == "on_time_paid"
        assert events[0].delta == 5
        assert session.scalar(select(func.count()).select_from(Payment)) == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    ("original", "payment_at", "basis", "expected_event"),
    (
        (
            "100000",
            datetime(2026, 8, 10, 19, tzinfo=UTC),
            "discounted",
            "on_time_paid",
        ),
        (
            "100000",
            datetime(2026, 8, 12, 18, 59, 59, 999999, tzinfo=UTC),
            "discounted",
            "on_time_paid",
        ),
        (
            "100000",
            datetime(2026, 8, 12, 19, tzinfo=UTC),
            "original",
            "overdue",
        ),
        ("99999", datetime(2026, 8, 10, 19, tzinfo=UTC), "discounted", None),
    ),
)
def test_real_postgresql_tashkent_due_and_threshold_edges_keep_payment_lawful(
    m2_test_database: Engine,
    original: str,
    payment_at: datetime,
    basis: str,
    expected_event: str | None,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    accepted_at = datetime(2026, 8, 10, 18, 59, 59, 999999, tzinfo=UTC)
    with factory.begin() as session:
        debt = session.get_one(Debt, debt_id)
        debt.original_amount_uzs = Decimal(original)
        debt.discounted_amount_uzs = Decimal(original)
        debt.created_at = accepted_at - timedelta(days=1)
        debt.pending_expires_at = debt.created_at + timedelta(hours=72)
        debt.accepted_at = accepted_at
        debt.updated_at = accepted_at
        debt.due_date = date(2026, 8, 12)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount=original,
        revision=2,
        key=uuid4(),
        basis=basis,
    )

    with factory.begin() as session:
        result = record_debt_payment(
            session,
            actor=actor,
            command=command,
            rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
            payment_clock=lambda: payment_at,
        )

    assert result.payment_id is not None
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        events = tuple(session.scalars(select(RatingEvent.event_type)))
        assert events == (() if expected_event is None else (expected_event,))


@pytest.mark.integration
def test_locked_source_append_treats_only_exact_source_as_no_op(
    m2_test_database: Engine,
) -> None:
    _actor_id, _shop_id, relation_id, debt_ids = _make_eligible_debts(
        m2_test_database, count=1
    )
    factory = create_database_session_factory(m2_test_database)
    occurred_at = PAYMENT_TIME
    event = create_on_time_paid_rating_event(
        event_id=RatingEventId(uuid4()),
        shop_customer_id=ShopCustomerId(relation_id),
        debt_id=DebtId(debt_ids[0]),
        payment_created_at=occurred_at,
        recording_source=RatingRecordingSource.LIVE,
        source_revision=DebtRevision(3),
    )

    with factory.begin() as session:
        customer_id = session.scalar(
            select(Customer.id)
            .join(ShopCustomer, ShopCustomer.customer_id == Customer.id)
            .where(ShopCustomer.id == relation_id)
        )
        assert customer_id is not None
        session.get_one(Customer, customer_id, with_for_update=True)
        locked = LockedRatingSourceScope(
            customer_id=CustomerId(customer_id),
            shop_customer_id=ShopCustomerId(relation_id),
            debt_id=DebtId(debt_ids[0]),
            _session=session,
        )
        first = append_locked_source_event(session, locked_source=locked, event=event)
        exact_replay = create_on_time_paid_rating_event(
            event_id=RatingEventId(uuid4()),
            shop_customer_id=event.shop_customer_id,
            debt_id=event.debt_id,
            payment_created_at=occurred_at,
            recording_source=RatingRecordingSource.LIVE,
            source_revision=event.source_revision,
        )
        second = append_locked_source_event(
            session,
            locked_source=locked,
            event=exact_replay,
        )

    assert first.outcome is RatingEventAppendOutcome.APPENDED
    assert second.outcome is RatingEventAppendOutcome.SOURCE_ALREADY_EXISTS


class _FailingRatingAdapter(SqlAlchemyLockedRatingAppendAdapter):
    def append_pending_on_time_paid(
        self, session, *, locked_debt, effect: PendingOnTimePaidRatingEffect
    ):
        raise RuntimeError("redacted rating fault")

    def append_pending_overdue(
        self, session, *, locked_source, effect: PendingOverdueRatingEffect
    ):
        raise RuntimeError("redacted rating fault")


@pytest.mark.integration
def test_positive_rating_fault_rolls_back_the_complete_payment(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _relation_id, debt_ids = _make_eligible_debts(
        m2_test_database, count=1
    )
    factory = create_database_session_factory(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )

    with pytest.raises(RuntimeError, match="redacted rating fault"):
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                rating_append_port=_FailingRatingAdapter(),
                payment_clock=lambda: PAYMENT_TIME,
            )

    with factory() as session:
        debt = session.get_one(Debt, debt_ids[0])
        assert debt.status == "active" and debt.revision == 2
        for model in (Payment, RatingEvent, AuditLog, IdempotencyKey):
            assert session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.integration
def test_overdue_rating_fault_rolls_back_transition_and_audits(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)

    with pytest.raises(RuntimeError, match="redacted rating fault"):
        with factory.begin() as session:
            materialize_overdue_candidate(
                session,
                candidate=seed.candidate(),
                now=OVERDUE_NOW,
                source=DebtOverdueSource.BATCH,
                posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
                rating_append_port=_FailingRatingAdapter(),
            )

    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        assert debt.status == "active" and debt.revision == 2
        assert session.scalar(select(func.count()).select_from(RatingEvent)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
