from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.enums import DebtOverdueSource
from app.debt.models import Debt
from app.debt.overdue_service import (
    OverdueTransitionOutcome,
    materialize_overdue_candidate,
    materialize_overdue_debts,
)
from app.debt.overdue_targeting import discover_overdue_batch
from app.debt.values import MAX_DEBT_AMOUNT_UZS
from app.idempotency.models import IdempotencyKey
from app.payment import service as payment_service
from app.payment import targeting as payment_targeting
from app.payment.models import Payment
from app.payment.repository import SqlAlchemyLockedDebtPostedTotalReader
from app.payment.service import PaymentMutationRejected, record_debt_payment
from app.shop.enums import ShopStatus
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer
from tests.test_m15_overdue_service_postgresql import (
    ACCEPTED_AT,
    _seed_debt,
)
from tests.test_m15_overdue_service_postgresql import (
    NOW as BATCH_NOW,
)
from tests.test_payment_service_postgresql import _command
from tests.test_payment_targeting_postgresql import _seed_one

WAIT_SECONDS = 10
BOUNDARY_PAYMENT_TIME = datetime(2026, 8, 9, 18, 59, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _PaymentRaceSeed:
    actor_id: UUID
    shop_id: UUID
    shop_customer_id: UUID
    customer_id: UUID
    debt_id: UUID


def _seed_payment_race(engine: Engine) -> _PaymentRaceSeed:
    actor_id, shop_id, _staff_id, relation_id, debt_id = _seed_one(engine)
    factory = create_database_session_factory(engine)
    with factory.begin() as session:
        debt = session.get_one(Debt, debt_id)
        relation = session.get_one(ShopCustomer, relation_id)
        created_at = datetime(2026, 8, 1, 8, tzinfo=UTC)
        accepted_at = created_at + timedelta(days=1)
        debt.created_at = created_at
        debt.pending_expires_at = created_at + timedelta(hours=72)
        debt.accepted_at = accepted_at
        debt.due_date = date(2026, 8, 9)
        debt.updated_at = accepted_at
        return _PaymentRaceSeed(
            actor_id=actor_id,
            shop_id=shop_id,
            shop_customer_id=relation_id,
            customer_id=relation.customer_id,
            debt_id=debt_id,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("amount", "expected_status", "expected_batch_counts", "expected_revision"),
    (
        ("100", "overdue", (1, 0), 4),
        ("1000", "paid", (0, 1), 3),
    ),
)
def test_on_time_payment_holds_lock_before_stale_batch_revalidation(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    amount: str,
    expected_status: str,
    expected_batch_counts: tuple[int, int],
    expected_revision: int,
) -> None:
    """Cover payment-before-batch for partial boundary payment and exact payoff."""

    from app.debt import overdue_service

    seed = _seed_payment_race(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, command = _command(
        actor_id=seed.actor_id,
        shop_id=seed.shop_id,
        debt_id=seed.debt_id,
        amount=amount,
        revision=2,
        key=uuid4(),
    )
    payment_holds_debt = Barrier(2)
    release_payment = Event()
    batch_discovered = Event()
    original_insert_payment = payment_service.insert_payment
    original_discover = overdue_service.discover_overdue_batch

    def pause_payment_after_insert(*args, **kwargs):
        result = original_insert_payment(*args, **kwargs)
        payment_holds_debt.wait(timeout=WAIT_SECONDS)
        assert release_payment.wait(timeout=WAIT_SECONDS)
        return result

    def observe_batch_discovery(session, *, now, batch_size):
        result = original_discover(session, now=now, batch_size=batch_size)
        assert len(result.candidates) == 1
        batch_discovered.set()
        return result

    monkeypatch.setattr(payment_service, "insert_payment", pause_payment_after_insert)
    monkeypatch.setattr(
        overdue_service,
        "discover_overdue_batch",
        observe_batch_discovery,
    )

    def pay():
        with factory.begin() as session:
            return record_debt_payment(
                session,
                actor=actor,
                command=command,
                payment_clock=lambda: BOUNDARY_PAYMENT_TIME,
            )

    def run_batch():
        return materialize_overdue_debts(
            factory,
            now=BATCH_NOW,
            batch_size=1,
            posted_total_reader_factory=SqlAlchemyLockedDebtPostedTotalReader,
        )

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        payment_future = pool.submit(pay)
        payment_holds_debt.wait(timeout=WAIT_SECONDS)
        batch_future = pool.submit(run_batch)
        assert batch_discovered.wait(timeout=WAIT_SECONDS)
        release_payment.set()
        payment_result = payment_future.result(timeout=WAIT_SECONDS)
        batch_result = batch_future.result(timeout=WAIT_SECONDS)
    finally:
        release_payment.set()
        pool.shutdown(wait=True)

    assert payment_result.payment_id is not None
    assert (
        batch_result.transitioned_count,
        batch_result.no_op_count,
    ) == expected_batch_counts
    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        event_types = tuple(
            session.scalars(select(AuditLog.event_type).order_by(AuditLog.event_type))
        )
        assert debt.status == expected_status
        assert debt.revision == expected_revision
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
    if expected_status == "overdue":
        assert debt.overdue_revision == 4
        assert event_types == (
            "debt.clawback_applied",
            "debt.overdue",
            "payment.recorded",
        )
    else:
        assert debt.overdue_revision is None
        assert event_types == ("debt.paid", "payment.recorded")


@pytest.mark.integration
def test_batch_holds_lock_before_boundary_payment_and_is_the_only_winner(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover batch-before-payment without time-based coordination."""

    from app.debt import overdue_service

    seed = _seed_payment_race(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, command = _command(
        actor_id=seed.actor_id,
        shop_id=seed.shop_id,
        debt_id=seed.debt_id,
        amount="100",
        revision=2,
        key=uuid4(),
    )
    batch_holds_debt = Barrier(2)
    release_batch = Event()
    payment_attempted_shop_lock = Event()
    original_append = overdue_service.append_audit_event
    original_shop_lock = payment_targeting.lock_shop_for_update
    append_calls = 0

    def pause_after_first_audit(session, event):
        nonlocal append_calls
        original_append(session, event)
        append_calls += 1
        if append_calls == 1:
            batch_holds_debt.wait(timeout=WAIT_SECONDS)
            assert release_batch.wait(timeout=WAIT_SECONDS)

    def observe_payment_shop_lock(*args, **kwargs):
        payment_attempted_shop_lock.set()
        return original_shop_lock(*args, **kwargs)

    monkeypatch.setattr(overdue_service, "append_audit_event", pause_after_first_audit)
    monkeypatch.setattr(
        payment_targeting,
        "lock_shop_for_update",
        observe_payment_shop_lock,
    )

    def run_batch():
        return materialize_overdue_debts(
            factory,
            now=BATCH_NOW,
            batch_size=1,
            posted_total_reader_factory=SqlAlchemyLockedDebtPostedTotalReader,
        )

    def pay():
        try:
            with factory.begin() as session:
                return record_debt_payment(
                    session,
                    actor=actor,
                    command=command,
                    payment_clock=lambda: BOUNDARY_PAYMENT_TIME,
                )
        except PaymentMutationRejected as exc:
            return exc

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        batch_future = pool.submit(run_batch)
        batch_holds_debt.wait(timeout=WAIT_SECONDS)
        payment_future = pool.submit(pay)
        assert payment_attempted_shop_lock.wait(timeout=WAIT_SECONDS)
        release_batch.set()
        batch_result = batch_future.result(timeout=WAIT_SECONDS)
        payment_result = payment_future.result(timeout=WAIT_SECONDS)
    finally:
        release_batch.set()
        pool.shutdown(wait=True)

    assert (batch_result.transitioned_count, batch_result.no_op_count) == (1, 0)
    assert isinstance(payment_result, PaymentMutationRejected)
    assert payment_result.error is ErrorCode.DEBT_CHANGED
    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        event_types = tuple(
            session.scalars(select(AuditLog.event_type).order_by(AuditLog.event_type))
        )
        assert debt.status == "overdue"
        assert debt.revision == debt.overdue_revision == 3
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
    assert event_types == ("debt.clawback_applied", "debt.overdue")


@pytest.mark.integration
@pytest.mark.parametrize("stale_change", ("revision", "paid"))
def test_detached_candidate_revalidates_stale_status_and_revision(
    m2_test_database: Engine,
    stale_change: str,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)
    with factory.begin() as session:
        batch = discover_overdue_batch(session, now=BATCH_NOW, batch_size=1)
    assert len(batch.candidates) == 1
    candidate = batch.candidates[0]

    with factory.begin() as session:
        debt = session.get_one(Debt, seed.debt_id)
        if stale_change == "revision":
            debt.revision = 3
            debt.updated_at = ACCEPTED_AT + timedelta(hours=1)
            session.add(
                Payment(
                    id=uuid4(),
                    debt_id=seed.debt_id,
                    recorded_by_user_id=seed.actor_id,
                    amount_uzs=Decimal("100"),
                    method="cash",
                    debt_revision_after=3,
                    created_at=ACCEPTED_AT + timedelta(hours=1),
                )
            )
        else:
            paid_at = ACCEPTED_AT + timedelta(days=1)
            debt.status = "paid"
            debt.revision = 3
            debt.paid_at = paid_at
            debt.updated_at = paid_at

    with factory.begin() as session:
        result = materialize_overdue_candidate(
            session,
            candidate=candidate,
            now=batch.normalized_now,
            source=DebtOverdueSource.BATCH,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )

    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        overdue_audits = int(
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.object_id == seed.debt_id)
            )
            or 0
        )
    if stale_change == "revision":
        assert result.outcome is OverdueTransitionOutcome.TRANSITIONED
        assert debt.status == "overdue"
        assert debt.revision == debt.overdue_revision == 4
        assert overdue_audits == 2
    else:
        assert result.outcome is OverdueTransitionOutcome.NO_OP
        assert debt.status == "paid"
        assert debt.revision == 3
        assert overdue_audits == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    (
        "original",
        "discounted",
        "discount_basis_points",
        "shop_status",
        "expected_increase",
    ),
    (
        (1000, 1000, 0, ShopStatus.ACTIVE, 0),
        (1000, 1000, 0, ShopStatus.SUSPENDED, 0),
        (
            int(MAX_DEBT_AMOUNT_UZS),
            100_000_000,
            9999,
            ShopStatus.ACTIVE,
            999_900_000_000,
        ),
        (
            int(MAX_DEBT_AMOUNT_UZS),
            100_000_000,
            9999,
            ShopStatus.SUSPENDED,
            999_900_000_000,
        ),
    ),
)
def test_zero_discount_max_money_and_suspension_transition_matrix(
    m2_test_database: Engine,
    original: int,
    discounted: int,
    discount_basis_points: int,
    shop_status: ShopStatus,
    expected_increase: int,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(
        factory,
        original=original,
        discounted=discounted,
        discount_basis_points=discount_basis_points,
    )
    if shop_status is ShopStatus.SUSPENDED:
        with factory.begin() as session:
            session.get_one(Shop, seed.shop_id).status = ShopStatus.SUSPENDED.value

    with factory.begin() as session:
        result = materialize_overdue_candidate(
            session,
            candidate=seed.candidate(),
            now=BATCH_NOW,
            source=DebtOverdueSource.BATCH,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )

    assert result.outcome is OverdueTransitionOutcome.TRANSITIONED
    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        events = tuple(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.object_id == seed.debt_id)
                .order_by(AuditLog.event_type)
            )
        )
    assert debt.status == "overdue"
    assert debt.revision == debt.overdue_revision == 3
    assert tuple(event.event_type for event in events) == (
        "debt.clawback_applied",
        "debt.overdue",
    )
    clawback = events[0]
    assert clawback.payload["balance_increase_uzs"] == expected_increase
    assert str(seed.debt_id) not in repr(seed.candidate())
