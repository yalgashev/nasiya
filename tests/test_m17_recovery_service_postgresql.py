from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.debt.business_time import tashkent_business_date
from app.debt.models import Debt
from app.debt.repository import (
    lock_customer_hard_block_scope,
    locked_customer_global_hard_block_reader_factory,
)
from app.debt.values import CustomerId
from app.idempotency.contracts import IdempotencyOutcome
from app.payment.commands import CreatePaymentV2RawForm, assemble_create_payment_request
from app.payment.models import Payment
from app.payment.service import PaymentMutationRejected
from app.rating.current_read_service import read_locked_current_rating_state
from app.rating.enums import RiskBand
from app.rating.models import RatingEvent
from app.shop_customer.models import ShopCustomer
from tests.rating_support import record_debt_payment
from tests.test_payment_targeting_postgresql import _context, _seed_one

OVERDUE_AT = datetime(2026, 8, 13, tzinfo=UTC)
WRITTEN_OFF_AT = datetime(2026, 8, 14, tzinfo=UTC)
PARTIAL_AT = datetime(2026, 8, 15, tzinfo=UTC)
SETTLED_AT = datetime(2026, 8, 16, tzinfo=UTC)


def _seed_written_off_source(
    engine: Engine,
) -> tuple[UUID, UUID, UUID]:
    actor_id, shop_id, _staff_id, relation_id, debt_id = _seed_one(engine)
    with Session(engine) as session, session.begin():
        debt = session.get_one(Debt, debt_id)
        debt.due_date = date(2026, 8, 12)
        debt.status = "written_off"
        debt.revision = 4
        debt.updated_at = WRITTEN_OFF_AT
        debt.overdue_at = OVERDUE_AT
        debt.overdue_revision = 3
        debt.written_off_at = WRITTEN_OFF_AT
        debt.written_off_revision = 4
        debt.written_off_reason = "collection_exhausted"
        debt.written_off_actor_user_id = actor_id
        session.add_all(
            (
                RatingEvent(
                    id=uuid4(),
                    shop_customer_id=relation_id,
                    debt_id=debt_id,
                    event_type="overdue",
                    delta=-15,
                    occurred_at=OVERDUE_AT,
                    business_date=tashkent_business_date(OVERDUE_AT),
                    recording_source="live",
                ),
                RatingEvent(
                    id=uuid4(),
                    shop_customer_id=relation_id,
                    debt_id=debt_id,
                    event_type="written_off",
                    delta=-40,
                    occurred_at=WRITTEN_OFF_AT,
                    business_date=tashkent_business_date(WRITTEN_OFF_AT),
                    recording_source="live",
                ),
                AuditLog(
                    event_type="debt.written_off",
                    actor_kind="USER",
                    actor_user_id=actor_id,
                    object_type="debt",
                    object_id=debt_id,
                    payload={
                        "reason_provided": True,
                        "from_status": "overdue",
                        "to_status": "written_off",
                        "written_off_revision": 4,
                    },
                    occurred_at=WRITTEN_OFF_AT,
                ),
            )
        )
    return actor_id, shop_id, debt_id


def _command(
    *,
    actor_id: UUID,
    shop_id: UUID,
    debt_id: UUID,
    amount: str,
    revision: int,
    key: UUID,
):
    actor = _context(actor_id, shop_id)
    assembled = assemble_create_payment_request(
        actor=actor,
        form=CreatePaymentV2RawForm(
            debt_id=str(debt_id),
            amount_uzs=amount,
            method="cash",
            idempotency_key=str(key),
            expected_revision=str(revision),
            expected_balance_basis="original",
        ),
        header_idempotency_key=str(key),
    )
    assert assembled.error is None and assembled.command is not None
    return actor, assembled.command


def _current_rating(engine: Engine, *, debt_id: UUID):
    with Session(engine) as session, session.begin():
        customer_id = session.scalar(
            select(ShopCustomer.customer_id)
            .join(Debt, Debt.shop_customer_id == ShopCustomer.id)
            .where(Debt.id == debt_id)
        )
        assert customer_id is not None
        locked = lock_customer_hard_block_scope(
            session, customer_id=CustomerId(customer_id)
        )
        assert locked is not None
        return read_locked_current_rating_state(
            session,
            locked_customer=locked,
            as_of_business_date=date(2026, 8, 16),
            global_hard_block_reader=locked_customer_global_hard_block_reader_factory(
                session, locked
            ),
        )


@pytest.mark.integration
def test_partial_then_exact_full_recovery_is_atomic_and_history_preserving(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_written_off_source(m2_test_database)
    actor, partial_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=4,
        key=uuid4(),
    )
    with Session(m2_test_database) as session, session.begin():
        partial = record_debt_payment(
            session,
            actor=actor,
            command=partial_command,
            payment_clock=lambda: PARTIAL_AT,
        )
        debt = session.get_one(Debt, debt_id)
        assert partial.outcome is IdempotencyOutcome.NEW
        assert debt.status == "written_off" and debt.revision == 5
        assert debt.written_off_settled_at is None and debt.paid_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(RatingEvent)
                .where(
                    RatingEvent.debt_id == debt_id,
                    RatingEvent.event_type == "written_off_settled",
                )
            )
            == 0
        )
    partial_rating = _current_rating(m2_test_database, debt_id=debt_id)
    assert partial_rating.current_score == 5
    assert partial_rating.band is RiskBand.BLOCKED

    settlement_key = uuid4()
    actor, settlement_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="600",
        revision=5,
        key=settlement_key,
    )
    with Session(m2_test_database) as session, session.begin():
        settled = record_debt_payment(
            session,
            actor=actor,
            command=settlement_command,
            payment_clock=lambda: SETTLED_AT,
        )
        debt = session.get_one(Debt, debt_id)
        assert settled.outcome is IdempotencyOutcome.NEW
        assert debt.status == "written_off_settled" and debt.revision == 6
        assert debt.written_off_settled_at == SETTLED_AT
        assert debt.written_off_settled_revision == 6
        assert debt.paid_at is None
        events = tuple(
            session.scalars(
                select(RatingEvent)
                .where(RatingEvent.debt_id == debt_id)
                .order_by(RatingEvent.occurred_at, RatingEvent.event_type)
            )
        )
        assert [(row.event_type, row.delta) for row in events] == [
            ("overdue", -15),
            ("written_off", -40),
            ("written_off_settled", 10),
        ]
        assert events[-1].occurred_at == SETTLED_AT
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.object_id == debt_id,
                    AuditLog.event_type == "debt.written_off_settled",
                )
            )
            == 1
        )
        assert session.scalar(
            select(func.sum(Payment.amount_uzs)).where(Payment.debt_id == debt_id)
        ) == Decimal("1000")
    settled_rating = _current_rating(m2_test_database, debt_id=debt_id)
    assert settled_rating.current_score == 15
    assert settled_rating.band is RiskBand.RED

    with Session(m2_test_database) as session, session.begin():
        replay = record_debt_payment(
            session,
            actor=actor,
            command=settlement_command,
            payment_clock=lambda: (_ for _ in ()).throw(AssertionError("clock called")),
        )
        assert replay.outcome is IdempotencyOutcome.REPLAY


@pytest.mark.integration
def test_settlement_rating_fault_rolls_back_payment_debt_audits_and_key(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_written_off_source(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1000",
        revision=4,
        key=uuid4(),
    )

    from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter

    class FaultPort(SqlAlchemyLockedRatingAppendAdapter):
        def append_pending_written_off_settled(self, session, *, locked_debt, effect):
            raise RuntimeError("synthetic settlement rating fault")

    from app.payment.service import record_debt_payment as service

    with pytest.raises(RuntimeError, match="synthetic settlement rating fault"):
        with Session(m2_test_database) as session, session.begin():
            service(
                session,
                actor=actor,
                command=command,
                rating_append_port=FaultPort(),
                payment_clock=lambda: SETTLED_AT,
            )

    with Session(m2_test_database) as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "written_off" and debt.revision == 4
        assert (
            session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.debt_id == debt_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(RatingEvent)
                .where(
                    RatingEvent.debt_id == debt_id,
                    RatingEvent.event_type == "written_off_settled",
                )
            )
            == 0
        )


@pytest.mark.integration
def test_two_terminal_attempts_serialize_to_one_payment_and_one_plus_ten(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_written_off_source(m2_test_database)
    commands = tuple(
        _command(
            actor_id=actor_id,
            shop_id=shop_id,
            debt_id=debt_id,
            amount="1000",
            revision=4,
            key=uuid4(),
        )
        for _ in range(2)
    )
    start = Barrier(2)

    def worker(item):
        actor, command = item
        start.wait()
        try:
            with Session(m2_test_database) as session, session.begin():
                result = record_debt_payment(
                    session,
                    actor=actor,
                    command=command,
                    payment_clock=lambda: SETTLED_AT,
                )
            return result.outcome.value
        except PaymentMutationRejected as exc:
            return exc.error.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(worker, commands))

    assert sorted(outcomes) == ["DEBT_NOT_PAYABLE", "new"]
    with Session(m2_test_database) as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "written_off_settled" and debt.revision == 5
        assert (
            session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.debt_id == debt_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(RatingEvent)
                .where(
                    RatingEvent.debt_id == debt_id,
                    RatingEvent.event_type == "written_off_settled",
                )
            )
            == 1
        )
