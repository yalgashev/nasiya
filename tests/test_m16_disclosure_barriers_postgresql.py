from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.db import create_database_session_factory
from app.debt import overdue_service
from app.debt.enums import DebtOverdueSource
from app.debt.models import Debt
from app.payment import service as payment_service
from app.payment import targeting as payment_targeting
from app.payment.models import Payment
from app.payment.repository import SqlAlchemyLockedDebtPostedTotalReader
from app.rating import disclosure_service
from app.rating.enums import RiskBand
from app.rating.models import DisclosureViewLog, RatingEvent
from app.shop.enums import ShopRole
from app.shop.models import ShopStaff
from tests.rating_support import materialize_overdue_candidate, record_debt_payment
from tests.test_m15_overdue_service_postgresql import NOW as BATCH_NOW
from tests.test_m15_overdue_service_postgresql import _seed_debt
from tests.test_m16_disclosure_services_postgresql import (
    Seed,
    _actor,
)
from tests.test_m16_disclosure_services_postgresql import (
    _command as disclosure_command,
)
from tests.test_payment_service_postgresql import _command as payment_command
from tests.test_payment_targeting_postgresql import _seed_one

PAYMENT_AT = datetime(2026, 8, 10, 12, tzinfo=UTC)
TASHKENT_MIDNIGHT = datetime(2026, 8, 10, 19, tzinfo=UTC)


def _seed_payoff(engine: Engine, *, late: bool) -> tuple[Seed, object, object]:
    actor_id, shop_id, _staff_id, relation_id, debt_id = _seed_one(engine)
    factory = create_database_session_factory(engine)
    with factory.begin() as session:
        debt = session.get_one(Debt, debt_id)
        debt.original_amount_uzs = Decimal("100000")
        debt.discounted_amount_uzs = Decimal("100000")
        if late:
            created_at = datetime(2026, 8, 1, 8, tzinfo=UTC)
            accepted_at = created_at + timedelta(days=1)
            debt.created_at = created_at
            debt.pending_expires_at = created_at + timedelta(hours=72)
            debt.accepted_at = accepted_at
            debt.updated_at = accepted_at
            debt.due_date = date(2026, 8, 9)
    actor, command = payment_command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100000",
        revision=2,
        key=uuid4(),
        basis="original" if late else "discounted",
    )
    return Seed(actor_id, shop_id, relation_id), actor, command


@pytest.mark.integration
@pytest.mark.parametrize(
    ("late", "expected_band"),
    ((False, RiskBand.NEW), (True, RiskBand.BLOCKED)),
)
def test_disclosure_before_payoff_is_complete_old_snapshot(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    late: bool,
    expected_band: RiskBand,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed, payment_actor, payment = _seed_payoff(m2_test_database, late=late)
    disclosure = disclosure_command(seed)
    snapshot_staged = Barrier(2)
    payment_attempted = Barrier(2)
    release_snapshot = Barrier(2)
    original_insert = disclosure_service.insert_disclosure_view_locked
    original_shop_lock = payment_targeting.lock_shop_for_update

    def pause_snapshot(*args, **kwargs):
        result = original_insert(*args, **kwargs)
        snapshot_staged.wait()
        release_snapshot.wait()
        return result

    def observe_payment_lock(*args, **kwargs):
        payment_attempted.wait()
        return original_shop_lock(*args, **kwargs)

    monkeypatch.setattr(
        disclosure_service,
        "insert_disclosure_view_locked",
        pause_snapshot,
    )
    monkeypatch.setattr(
        payment_targeting,
        "lock_shop_for_update",
        observe_payment_lock,
    )

    def disclose():
        with factory.begin() as session:
            return disclosure_service.record_risk_band_disclosure(
                session,
                actor=_actor(seed),
                command=disclosure,
                disclosure_clock=lambda: PAYMENT_AT,
            )

    def pay():
        with factory.begin() as session:
            return record_debt_payment(
                session,
                actor=payment_actor,
                command=payment,
                payment_clock=lambda: PAYMENT_AT,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        disclosure_future = executor.submit(disclose)
        snapshot_staged.wait()
        payment_future = executor.submit(pay)
        payment_attempted.wait()
        release_snapshot.wait()
        disclosed = disclosure_future.result()
        paid = payment_future.result()

    with factory() as session:
        snapshot = session.get_one(
            DisclosureViewLog,
            disclosed.disclosure_view_id.as_uuid(),
        )
        assert snapshot.band == expected_band.value
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(RatingEvent)) == 1
        assert paid.payment_id is not None


@pytest.mark.integration
@pytest.mark.parametrize(
    ("late", "expected_band"),
    ((False, RiskBand.YELLOW), (True, RiskBand.RED)),
)
def test_payoff_before_disclosure_is_complete_new_snapshot(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    late: bool,
    expected_band: RiskBand,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    seed, payment_actor, payment = _seed_payoff(m2_test_database, late=late)
    disclosure = disclosure_command(seed)
    payment_staged = Barrier(2)
    disclosure_attempted = Barrier(2)
    release_payment = Barrier(2)
    original_payment_insert = payment_service.insert_payment
    original_disclosure_lock = disclosure_service.lock_tenant_disclosure_target

    def pause_payment(*args, **kwargs):
        result = original_payment_insert(*args, **kwargs)
        payment_staged.wait()
        release_payment.wait()
        return result

    def observe_disclosure_lock(*args, **kwargs):
        disclosure_attempted.wait()
        return original_disclosure_lock(*args, **kwargs)

    monkeypatch.setattr(payment_service, "insert_payment", pause_payment)
    monkeypatch.setattr(
        disclosure_service,
        "lock_tenant_disclosure_target",
        observe_disclosure_lock,
    )

    def pay():
        with factory.begin() as session:
            return record_debt_payment(
                session,
                actor=payment_actor,
                command=payment,
                payment_clock=lambda: PAYMENT_AT,
            )

    def disclose():
        with factory.begin() as session:
            return disclosure_service.record_risk_band_disclosure(
                session,
                actor=_actor(seed),
                command=disclosure,
                disclosure_clock=lambda: TASHKENT_MIDNIGHT,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        payment_future = executor.submit(pay)
        payment_staged.wait()
        disclosure_future = executor.submit(disclose)
        disclosure_attempted.wait()
        release_payment.wait()
        paid = payment_future.result()
        disclosed = disclosure_future.result()

    with factory() as session:
        snapshot = session.get_one(
            DisclosureViewLog,
            disclosed.disclosure_view_id.as_uuid(),
        )
        assert snapshot.band == expected_band.value
        assert snapshot.created_at == TASHKENT_MIDNIGHT
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(RatingEvent)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "disclosure.risk_band_viewed")
            )
            == 1
        )
        assert paid.payment_id is not None


@pytest.mark.integration
def test_batch_overdue_before_disclosure_is_one_complete_blocked_snapshot(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = create_database_session_factory(m2_test_database)
    overdue_seed = _seed_debt(factory)
    with factory.begin() as session:
        session.add(
            ShopStaff(
                shop_id=overdue_seed.shop_id,
                user_id=overdue_seed.actor_id,
                role=ShopRole.CASHIER.value,
                is_active=True,
                created_at=PAYMENT_AT,
                updated_at=PAYMENT_AT,
            )
        )
    seed = Seed(
        overdue_seed.actor_id,
        overdue_seed.shop_id,
        overdue_seed.shop_customer_id,
    )
    disclosure = disclosure_command(seed)
    batch_staged = Barrier(2)
    disclosure_attempted = Barrier(2)
    release_batch = Barrier(2)
    original_audit = overdue_service.append_audit_event
    original_disclosure_lock = disclosure_service.lock_tenant_disclosure_target
    audit_calls = 0

    def pause_after_first_batch_audit(session, audit_event):
        nonlocal audit_calls
        original_audit(session, audit_event)
        audit_calls += 1
        if audit_calls == 1:
            batch_staged.wait()
            release_batch.wait()

    def observe_disclosure_lock(*args, **kwargs):
        disclosure_attempted.wait()
        return original_disclosure_lock(*args, **kwargs)

    monkeypatch.setattr(
        overdue_service,
        "append_audit_event",
        pause_after_first_batch_audit,
    )
    monkeypatch.setattr(
        disclosure_service,
        "lock_tenant_disclosure_target",
        observe_disclosure_lock,
    )

    def run_batch():
        with factory.begin() as session:
            return materialize_overdue_candidate(
                session,
                candidate=overdue_seed.candidate(),
                now=BATCH_NOW,
                source=DebtOverdueSource.BATCH,
                posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
            )

    def disclose():
        with factory.begin() as session:
            return disclosure_service.record_risk_band_disclosure(
                session,
                actor=_actor(seed),
                command=disclosure,
                disclosure_clock=lambda: BATCH_NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        batch_future = executor.submit(run_batch)
        batch_staged.wait()
        disclosure_future = executor.submit(disclose)
        disclosure_attempted.wait()
        release_batch.wait()
        batch_result = batch_future.result()
        disclosed = disclosure_future.result()

    assert batch_result.outcome.value == "transitioned"
    with factory() as session:
        snapshot = session.get_one(
            DisclosureViewLog,
            disclosed.disclosure_view_id.as_uuid(),
        )
        assert snapshot.band == RiskBand.BLOCKED.value
        assert session.scalar(select(func.count()).select_from(RatingEvent)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "disclosure.risk_band_viewed")
            )
            == 1
        )
