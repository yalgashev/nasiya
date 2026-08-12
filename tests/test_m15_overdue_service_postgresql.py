from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import AuditLog
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.enums import DebtOverdueSource
from app.debt.models import Debt
from app.debt.overdue_service import (
    OverdueBatchTransitionError,
    OverdueTransitionOutcome,
    materialize_locked_overdue_debt,
)
from app.debt.overdue_targeting import OverdueCandidateLocator
from app.debt.repository import mark_locked_debt_transition_scope
from app.debt.values import DebtId
from app.payment.models import Payment
from app.payment.repository import SqlAlchemyLockedDebtPostedTotalReader
from app.rating.models import RatingEvent
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer
from tests.rating_support import (
    materialize_overdue_candidate,
    materialize_overdue_debts,
)

CREATED_AT = datetime(2026, 8, 1, 8, tzinfo=UTC)
ACCEPTED_AT = CREATED_AT + timedelta(days=1)
NOW = datetime(2026, 8, 9, 20, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Seed:
    actor_id: UUID
    customer_user_id: UUID
    customer_id: UUID
    shop_id: UUID
    shop_customer_id: UUID
    debt_id: UUID

    def candidate(self) -> OverdueCandidateLocator:
        return OverdueCandidateLocator(
            debt_id=DebtId(self.debt_id),
            shop_customer_id=self.shop_customer_id,
            customer_id=self.customer_id,
            shop_id=self.shop_id,
        )


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _seed_debt(
    factory: sessionmaker[Session],
    *,
    status: str = "active",
    due_date: date = date(2026, 8, 8),
    original: int = 1000,
    discounted: int = 900,
    discount_basis_points: int = 1000,
    revision: int | None = None,
) -> _Seed:
    with factory.begin() as session:
        actor = User(phone=_phone(), is_active=True)
        customer_user = User(phone=_phone(), is_active=True)
        session.add_all((actor, customer_user))
        session.flush()
        customer = Customer(
            user_id=customer_user.id,
            onboarding_status="active",
            activated_at=CREATED_AT,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        shop = Shop(
            name=f"M15 overdue {uuid4().hex[:8]}",
            phone=_phone(),
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        session.add_all((customer, shop))
        session.flush()
        relation = ShopCustomer(
            shop_id=shop.id,
            customer_id=customer.id,
            credit_limit_uzs=Decimal("1000000"),
            max_open_debts=20,
            list_status="normal",
            revision=1,
            created_by_user_id=actor.id,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        session.add(relation)
        session.flush()
        lifecycle = _lifecycle(status)
        debt = Debt(
            shop_customer_id=relation.id,
            created_by_user_id=actor.id,
            original_amount_uzs=Decimal(original),
            discount_basis_points=discount_basis_points,
            discounted_amount_uzs=Decimal(discounted),
            due_date=due_date,
            pending_expires_at=CREATED_AT + timedelta(hours=72),
            status=status,
            revision=revision or lifecycle["revision"],
            accepted_at=lifecycle["accepted_at"],
            overdue_at=lifecycle["overdue_at"],
            overdue_revision=lifecycle["overdue_revision"],
            paid_at=lifecycle["paid_at"],
            created_at=CREATED_AT,
            updated_at=lifecycle["updated_at"],
        )
        session.add(debt)
        session.flush()
        return _Seed(
            actor_id=actor.id,
            customer_user_id=customer_user.id,
            customer_id=customer.id,
            shop_id=shop.id,
            shop_customer_id=relation.id,
            debt_id=debt.id,
        )


def _lifecycle(status: str) -> dict[str, object]:
    if status == "pending":
        return {
            "revision": 1,
            "accepted_at": None,
            "overdue_at": None,
            "overdue_revision": None,
            "paid_at": None,
            "updated_at": CREATED_AT,
        }
    if status == "active":
        return {
            "revision": 2,
            "accepted_at": ACCEPTED_AT,
            "overdue_at": None,
            "overdue_revision": None,
            "paid_at": None,
            "updated_at": ACCEPTED_AT,
        }
    if status == "overdue":
        return {
            "revision": 3,
            "accepted_at": ACCEPTED_AT,
            "overdue_at": NOW - timedelta(hours=1),
            "overdue_revision": 3,
            "paid_at": None,
            "updated_at": NOW - timedelta(hours=1),
        }
    if status == "paid":
        return {
            "revision": 3,
            "accepted_at": ACCEPTED_AT,
            "overdue_at": None,
            "overdue_revision": None,
            "paid_at": ACCEPTED_AT + timedelta(days=1),
            "updated_at": ACCEPTED_AT + timedelta(days=1),
        }
    raise ValueError("unsupported test status")


@pytest.mark.integration
@pytest.mark.parametrize(
    "source", (DebtOverdueSource.BATCH, DebtOverdueSource.INLINE_PAYMENT)
)
def test_atomic_transition_is_exact_once_with_one_revision_and_audit_pair(
    m2_test_database: Engine,
    source: DebtOverdueSource,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)

    with factory.begin() as session:
        first = materialize_overdue_candidate(
            session,
            candidate=seed.candidate(),
            now=NOW,
            source=source,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )
    with factory.begin() as session:
        repeated = materialize_overdue_candidate(
            session,
            candidate=seed.candidate(),
            now=NOW,
            source=source,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )

    assert first.outcome is OverdueTransitionOutcome.TRANSITIONED
    assert repeated.outcome is OverdueTransitionOutcome.NO_OP
    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        audits = list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.object_id == seed.debt_id)
                .order_by(AuditLog.event_type)
            )
        )
        ratings = list(
            session.scalars(
                select(RatingEvent).where(RatingEvent.debt_id == seed.debt_id)
            )
        )
    assert debt.status == "overdue"
    assert debt.revision == 3
    assert debt.overdue_revision == 3
    assert debt.overdue_at == NOW
    assert debt.updated_at == NOW
    assert len(ratings) == 1
    assert ratings[0].event_type == "overdue"
    assert ratings[0].delta == -15
    assert ratings[0].occurred_at == NOW
    assert [audit.event_type for audit in audits] == [
        "debt.clawback_applied",
        "debt.overdue",
    ]
    assert {audit.payload["source"] for audit in audits} == {source.value}
    overdue_payload = next(
        audit.payload for audit in audits if audit.event_type == "debt.overdue"
    )
    clawback_payload = next(
        audit.payload for audit in audits if audit.event_type == "debt.clawback_applied"
    )
    assert overdue_payload["overdue_revision"] == 3
    assert overdue_payload["business_date"] == "2026-08-10"
    assert clawback_payload["balance_increase_uzs"] == 100


@pytest.mark.integration
def test_inline_transition_accepts_an_already_locked_debt_scope(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)

    with factory.begin() as session:
        row = session.scalar(
            select(Debt).where(Debt.id == seed.debt_id).with_for_update()
        )
        assert row is not None
        result = materialize_locked_overdue_debt(
            session,
            locked_debt=mark_locked_debt_transition_scope(
                session,
                locked_row=row,
            ),
            now=NOW,
            source=DebtOverdueSource.INLINE_PAYMENT,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )

    assert result.outcome is OverdueTransitionOutcome.TRANSITIONED
    assert result.effect is not None
    assert result.effect.source is DebtOverdueSource.INLINE_PAYMENT
    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        audit_sources = set(
            session.scalars(
                select(AuditLog.payload["source"].as_string()).where(
                    AuditLog.object_id == seed.debt_id
                )
            )
        )
    assert debt.status == "overdue"
    assert debt.revision == 3
    assert audit_sources == set()


@pytest.mark.integration
def test_locked_transition_resums_lawful_partial_payment_before_rollover(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory, revision=3)
    with factory.begin() as session:
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

    with factory.begin() as session:
        result = materialize_overdue_candidate(
            session,
            candidate=seed.candidate(),
            now=NOW,
            source=DebtOverdueSource.BATCH,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )

    assert result.outcome is OverdueTransitionOutcome.TRANSITIONED
    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        payloads = list(
            session.scalars(
                select(AuditLog.payload).where(AuditLog.object_id == seed.debt_id)
            )
        )
    assert debt.revision == 4
    assert debt.overdue_revision == 4
    assert all(payload["overdue_revision"] == 4 for payload in payloads)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("status", "due_date"),
    (
        ("pending", date(2026, 8, 8)),
        ("overdue", date(2026, 8, 8)),
        ("paid", date(2026, 8, 8)),
        ("active", date(2026, 8, 12)),
    ),
)
def test_non_active_or_not_due_locked_rows_are_no_op_before_ledger_read(
    m2_test_database: Engine,
    status: str,
    due_date: date,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory, status=status, due_date=due_date)

    class _ForbiddenReader:
        def read_posted_total_uzs(self, *, debt_id: DebtId) -> Decimal:
            raise AssertionError("ledger must not be read")

    with factory.begin() as session:
        result = materialize_overdue_candidate(
            session,
            candidate=seed.candidate(),
            now=NOW,
            source=DebtOverdueSource.BATCH,
            posted_total_reader=_ForbiddenReader(),
        )

    assert result.outcome is OverdueTransitionOutcome.NO_OP
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.object_id == seed.debt_id)
            )
            == 0
        )


@pytest.mark.integration
@pytest.mark.parametrize("posted", (900, 950))
def test_incoherent_active_ledger_fails_closed_without_transition(
    m2_test_database: Engine,
    posted: int,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory, revision=3)
    with factory.begin() as session:
        session.add(
            Payment(
                id=uuid4(),
                debt_id=seed.debt_id,
                recorded_by_user_id=seed.actor_id,
                amount_uzs=Decimal(posted),
                method="cash",
                debt_revision_after=3,
                created_at=ACCEPTED_AT + timedelta(hours=1),
            )
        )

    with pytest.raises(ValueError, match="ledger is incoherent"):
        with factory.begin() as session:
            materialize_overdue_candidate(
                session,
                candidate=seed.candidate(),
                now=NOW,
                source=DebtOverdueSource.BATCH,
                posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
            )

    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        assert debt.status == "active"
        assert debt.overdue_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.object_id == seed.debt_id)
            )
            == 0
        )


@pytest.mark.integration
@pytest.mark.parametrize("fault", ("debt_flush", "first_audit", "second_audit"))
def test_transition_flush_or_audit_fault_rolls_back_every_write(
    m2_test_database: Engine,
    monkeypatch,
    fault: str,
) -> None:
    from app.debt import overdue_service

    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)
    if fault == "debt_flush":
        original_update = overdue_service.update_locked_debt

        def _faulty_update(session, *, row, debt):
            original_update(session, row=row, debt=debt)
            raise RuntimeError("injected debt flush fault")

        monkeypatch.setattr(overdue_service, "update_locked_debt", _faulty_update)
    else:
        original_append = overdue_service.append_audit_event
        calls = 0
        failing_call = 1 if fault == "first_audit" else 2

        def _faulty_append(session, event):
            nonlocal calls
            calls += 1
            if calls == failing_call:
                raise RuntimeError("injected audit fault")
            original_append(session, event)

        monkeypatch.setattr(overdue_service, "append_audit_event", _faulty_append)

    with pytest.raises(RuntimeError, match="injected"):
        with factory.begin() as session:
            materialize_overdue_candidate(
                session,
                candidate=seed.candidate(),
                now=NOW,
                source=DebtOverdueSource.BATCH,
                posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
            )

    with factory() as session:
        debt = session.get_one(Debt, seed.debt_id)
        assert debt.status == "active"
        assert debt.revision == 2
        assert debt.overdue_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.object_id == seed.debt_id)
            )
            == 0
        )


@pytest.mark.integration
def test_suspended_shop_inactive_users_and_draft_customer_do_not_deny_time_transition(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)
    with factory.begin() as session:
        session.get_one(Shop, seed.shop_id).status = "suspended"
        session.get_one(User, seed.actor_id).is_active = False
        session.get_one(User, seed.customer_user_id).is_active = False
        customer = session.get_one(Customer, seed.customer_id)
        customer.onboarding_status = "draft"
        customer.activated_at = None
        customer.updated_at = NOW

    with factory.begin() as session:
        result = materialize_overdue_candidate(
            session,
            candidate=seed.candidate(),
            now=NOW,
            source=DebtOverdueSource.BATCH,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )

    assert result.outcome is OverdueTransitionOutcome.TRANSITIONED


@pytest.mark.integration
def test_moved_or_missing_discovered_chain_is_safe_no_op(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)
    with factory.begin() as session:
        second_shop = Shop(
            name=f"M15 moved {uuid4().hex[:8]}",
            phone=_phone(),
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        session.add(second_shop)
        session.flush()
        second_relation = ShopCustomer(
            shop_id=second_shop.id,
            customer_id=seed.customer_id,
            credit_limit_uzs=Decimal("1000000"),
            max_open_debts=20,
            list_status="normal",
            revision=1,
            created_by_user_id=seed.actor_id,
            created_at=CREATED_AT,
            updated_at=CREATED_AT,
        )
        session.add(second_relation)
        session.flush()
        session.get_one(Debt, seed.debt_id).shop_customer_id = second_relation.id

    with factory.begin() as session:
        moved = materialize_overdue_candidate(
            session,
            candidate=seed.candidate(),
            now=NOW,
            source=DebtOverdueSource.BATCH,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )
        missing_candidate = OverdueCandidateLocator(
            debt_id=DebtId(uuid4()),
            shop_customer_id=seed.shop_customer_id,
            customer_id=seed.customer_id,
            shop_id=seed.shop_id,
        )
        missing = materialize_overdue_candidate(
            session,
            candidate=missing_candidate,
            now=NOW,
            source=DebtOverdueSource.BATCH,
            posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
        )

    assert moved.outcome is OverdueTransitionOutcome.NO_OP
    assert missing.outcome is OverdueTransitionOutcome.NO_OP


@pytest.mark.integration
def test_bounded_batch_is_deterministic_and_one_transaction_per_candidate(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seeds = tuple(_seed_debt(factory) for _ in range(3))

    first = materialize_overdue_debts(
        factory,
        now=NOW,
        batch_size=2,
        posted_total_reader_factory=SqlAlchemyLockedDebtPostedTotalReader,
    )
    second = materialize_overdue_debts(
        factory,
        now=NOW,
        batch_size=2,
        posted_total_reader_factory=SqlAlchemyLockedDebtPostedTotalReader,
    )

    assert (
        first.candidates_considered,
        first.transitioned_count,
        first.no_op_count,
    ) == (
        2,
        2,
        0,
    )
    assert (
        second.candidates_considered,
        second.transitioned_count,
        second.no_op_count,
    ) == (1, 1, 0)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Debt).where(Debt.status == "overdue")
            )
            == 3
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type.in_(("debt.overdue", "debt.clawback_applied"))
                )
            )
            == 6
        )
        assert {session.get_one(Debt, seed.debt_id).status for seed in seeds} == {
            "overdue"
        }


@pytest.mark.integration
def test_each_batch_candidate_has_an_independent_commit_boundary(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    tuple(_seed_debt(factory) for _ in range(3))
    reader_count = 0

    class _FailingSecondReader:
        def __init__(self, session: Session, *, should_fail: bool) -> None:
            self._delegate = SqlAlchemyLockedDebtPostedTotalReader(session)
            self._should_fail = should_fail

        def read_posted_total_uzs(self, *, debt_id: DebtId) -> Decimal:
            if self._should_fail:
                raise RuntimeError("second candidate fault")
            return self._delegate.read_posted_total_uzs(debt_id=debt_id)

    def _reader_factory(session: Session):
        nonlocal reader_count
        reader_count += 1
        return _FailingSecondReader(session, should_fail=reader_count == 2)

    with pytest.raises(OverdueBatchTransitionError):
        materialize_overdue_debts(
            factory,
            now=NOW,
            batch_size=3,
            posted_total_reader_factory=_reader_factory,
        )

    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Debt).where(Debt.status == "overdue")
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Debt).where(Debt.status == "active")
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.event_type.in_(("debt.overdue", "debt.clawback_applied"))
                )
            )
            == 2
        )


@pytest.mark.integration
def test_overlapping_batch_runs_are_exact_once(
    m2_test_database: Engine,
    monkeypatch,
) -> None:
    from app.debt import overdue_service

    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)
    original_discover = overdue_service.discover_overdue_batch
    barrier = Barrier(2)

    def _synchronized_discovery(session, *, now, batch_size):
        batch = original_discover(session, now=now, batch_size=batch_size)
        barrier.wait(timeout=10)
        return batch

    monkeypatch.setattr(
        overdue_service, "discover_overdue_batch", _synchronized_discovery
    )

    def _run_batch():
        return materialize_overdue_debts(
            factory,
            now=NOW,
            batch_size=1,
            posted_total_reader_factory=SqlAlchemyLockedDebtPostedTotalReader,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: _run_batch(), range(2)))

    assert sorted(
        (result.transitioned_count, result.no_op_count) for result in results
    ) == [(0, 1), (1, 0)]
    with factory() as session:
        assert session.get_one(Debt, seed.debt_id).revision == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.object_id == seed.debt_id)
            )
            == 2
        )


@pytest.mark.integration
def test_batch_wraps_candidate_failure_without_raw_identifier(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed = _seed_debt(factory)

    class _LeakingReader:
        def read_posted_total_uzs(self, *, debt_id: DebtId) -> Decimal:
            raise RuntimeError(f"sensitive {debt_id.as_uuid()}")

    with pytest.raises(OverdueBatchTransitionError) as caught:
        materialize_overdue_debts(
            factory,
            now=NOW,
            batch_size=1,
            posted_total_reader_factory=lambda _session: _LeakingReader(),
        )

    assert str(seed.debt_id) not in str(caught.value)
    assert caught.value.__cause__ is None
    with factory() as session:
        assert session.get_one(Debt, seed.debt_id).status == "active"
