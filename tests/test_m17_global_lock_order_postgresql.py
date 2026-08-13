from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from inspect import getsource
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.debt.write_off_service as write_off_service
import app.payment.service as payment_service
from app.audit.models import AuditLog
from app.debt.models import Debt
from app.debt.write_off_service import WriteOffMutationRejected
from app.idempotency.models import IdempotencyKey
from app.payment.models import Payment
from app.payment.repository import SqlAlchemyLockedDebtPostedTotalReader
from app.payment.service import PaymentMutationRejected
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.rating.models import RatingEvent
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from tests.rating_support import record_debt_payment
from tests.test_m17_recovery_service_postgresql import _command as _payment_command
from tests.test_m17_write_off_service_postgresql import (
    WRITTEN_OFF,
    _seed_source,
)
from tests.test_m17_write_off_service_postgresql import (
    _command as _write_off_command,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]


def test_same_write_off_key_waits_then_resolves_exactly_once(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin, debt = _seed_source(session)
        command = _write_off_command(admin, debt, key=uuid4())
        debt_id = debt.id

    both_passed_initial_read = Barrier(2)
    original_find = write_off_service.find_completed_key

    def synchronize_initial_read(*args, **kwargs):
        result = original_find(*args, **kwargs)
        both_passed_initial_read.wait()
        return result

    monkeypatch.setattr(
        write_off_service,
        "find_completed_key",
        synchronize_initial_read,
    )

    def worker() -> str:
        with Session(m2_test_database) as session, session.begin():
            return write_off_service.write_off_overdue_debt(
                session,
                command=command,
                rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
                posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
                clock=lambda: WRITTEN_OFF,
            ).outcome.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _index: worker(), range(2)))

    assert sorted(outcomes) == ["new", "replay"]
    with Session(m2_test_database) as session:
        assert _count(session, RatingEvent, debt_id, "written_off") == 1
        assert _count(session, AuditLog, debt_id, "debt.written_off") == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(IdempotencyKey)
                .where(IdempotencyKey.endpoint == "admin.debts.write_off")
            )
            == 1
        )


def test_write_off_before_late_payoff_is_one_complete_written_off_state(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin, debt = _seed_source(session)
        write_off = _write_off_command(admin, debt)
        actor_id = admin.id
        relation = debt.shop_customer_id
        debt_id = debt.id
        shop_id = session.get_one(ShopCustomer, relation).shop_id
        session.get_one(Shop, shop_id).status = "active"
        session.add(
            ShopStaff(
                shop_id=shop_id,
                user_id=actor_id,
                role=ShopRole.OWNER.value,
                is_active=True,
                created_at=WRITTEN_OFF,
                updated_at=WRITTEN_OFF,
            )
        )

    actor, payment = _payment_command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100000",
        revision=3,
        key=uuid4(),
    )

    write_off_staged = Barrier(2)
    payment_attempted_shop = Barrier(2)
    release_write_off = Barrier(2)
    original_update = write_off_service.update_locked_debt
    original_payment_predecessors = payment_service.lock_tenant_payment_predecessors

    def pause_write_off(*args, **kwargs):
        result = original_update(*args, **kwargs)
        write_off_staged.wait()
        release_write_off.wait()
        return result

    def observe_payment(*args, **kwargs):
        payment_attempted_shop.wait()
        return original_payment_predecessors(*args, **kwargs)

    monkeypatch.setattr(write_off_service, "update_locked_debt", pause_write_off)
    monkeypatch.setattr(
        payment_service,
        "lock_tenant_payment_predecessors",
        observe_payment,
    )

    def write_off_worker() -> str:
        with Session(m2_test_database) as session, session.begin():
            return write_off_service.write_off_overdue_debt(
                session,
                command=write_off,
                rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
                posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
                clock=lambda: WRITTEN_OFF,
            ).outcome.value

    def payment_worker() -> str:
        try:
            with Session(m2_test_database) as session, session.begin():
                record_debt_payment(
                    session,
                    actor=actor,
                    command=payment,
                    payment_clock=lambda: WRITTEN_OFF + timedelta(microseconds=1),
                )
        except PaymentMutationRejected as exc:
            return exc.error.value
        raise AssertionError("late payoff unexpectedly mutated after write-off")

    with ThreadPoolExecutor(max_workers=2) as executor:
        write_future = executor.submit(write_off_worker)
        write_off_staged.wait()
        payment_future = executor.submit(payment_worker)
        payment_attempted_shop.wait()
        payment_completed_before_release = payment_future.done()
        release_write_off.wait()
        assert not payment_completed_before_release
        assert write_future.result() == "new"
        assert payment_future.result() == "DEBT_CHANGED"

    with Session(m2_test_database) as session:
        assert session.get_one(Debt, debt_id).status == "written_off"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.debt_id == debt_id)
            )
            == 0
        )
        assert _count(session, RatingEvent, debt_id, "written_off") == 1


def test_late_payoff_before_write_off_is_one_complete_paid_state(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        admin, debt = _seed_source(session)
        write_off = _write_off_command(admin, debt)
        actor_id = admin.id
        relation = debt.shop_customer_id
        debt_id = debt.id
        shop_id = session.get_one(ShopCustomer, relation).shop_id
        session.get_one(Shop, shop_id).status = "active"
        session.add(
            ShopStaff(
                shop_id=shop_id,
                user_id=actor_id,
                role=ShopRole.OWNER.value,
                is_active=True,
                created_at=WRITTEN_OFF,
                updated_at=WRITTEN_OFF,
            )
        )

    actor, payment = _payment_command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100000",
        revision=3,
        key=uuid4(),
    )

    payment_staged = Barrier(2)
    write_off_attempted_shop = Barrier(2)
    release_payment = Barrier(2)
    original_insert_payment = payment_service.insert_payment
    original_write_predecessors = write_off_service.lock_admin_write_off_predecessors

    def pause_payment(*args, **kwargs):
        result = original_insert_payment(*args, **kwargs)
        payment_staged.wait()
        release_payment.wait()
        return result

    def observe_write_off(*args, **kwargs):
        write_off_attempted_shop.wait()
        return original_write_predecessors(*args, **kwargs)

    monkeypatch.setattr(payment_service, "insert_payment", pause_payment)
    monkeypatch.setattr(
        write_off_service,
        "lock_admin_write_off_predecessors",
        observe_write_off,
    )

    def payment_worker() -> str:
        with Session(m2_test_database) as session, session.begin():
            return record_debt_payment(
                session,
                actor=actor,
                command=payment,
                payment_clock=lambda: WRITTEN_OFF,
            ).outcome.value

    def write_off_worker() -> str:
        try:
            with Session(m2_test_database) as session, session.begin():
                write_off_service.write_off_overdue_debt(
                    session,
                    command=write_off,
                    rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
                    posted_total_reader=SqlAlchemyLockedDebtPostedTotalReader(session),
                    clock=lambda: WRITTEN_OFF + timedelta(microseconds=1),
                )
        except WriteOffMutationRejected as exc:
            return exc.failure.value
        raise AssertionError("write-off unexpectedly mutated after payoff")

    with ThreadPoolExecutor(max_workers=2) as executor:
        payment_future = executor.submit(payment_worker)
        payment_staged.wait()
        write_future = executor.submit(write_off_worker)
        write_off_attempted_shop.wait()
        write_off_completed_before_release = write_future.done()
        release_payment.wait()
        assert not write_off_completed_before_release
        assert payment_future.result() == "new"
        assert write_future.result() == "DEBT_CHANGED"

    with Session(m2_test_database) as session:
        assert session.get_one(Debt, debt_id).status == "paid"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Payment)
                .where(Payment.debt_id == debt_id)
            )
            == 1
        )
        assert _count(session, RatingEvent, debt_id, "written_off") == 0
        assert _count(session, AuditLog, debt_id, "debt.written_off") == 0


def test_m17_stage_order_extends_the_inherited_append_tail() -> None:
    write_off = getsource(write_off_service.write_off_overdue_debt)
    _assert_ordered(
        write_off,
        "insert_or_resolve_key",
        "update_locked_debt",
        "append_pending_written_off(",
        "append_debt_written_off_audit",
        "WriteOffDebtMutationResult(",
    )
    payment = getsource(payment_service.record_debt_payment)
    _assert_ordered(
        payment,
        "update_locked_debt",
        "insert_payment",
        "append_pending_written_off_settled(",
        "append_payment_recorded_audit",
        "append_debt_written_off_settled_audit",
    )

    evidence = {
        "tests/test_m14_combined_lock_order_postgresql.py": (
            "test_payment_before_new_debt_is_a_complete_after_state_under_shop_barrier",
            "test_pending_accept_completes_before_payment_as_one_forward_ordered_history",
            "test_same_key_waits_for_unique_resolution_then_persists_one_key",
        ),
        "tests/test_m15_combined_lock_order_postgresql.py": (
            "test_batch_audit_vs_cross_shop_create_finishes_in_complete_blocked_state",
            "test_batch_vs_m12_policy_mutations_serialize_at_shop_predecessor",
        ),
        "tests/test_m16_combined_lock_order_postgresql.py": (
            "test_cross_shop_disclosure_and_create_serialize_at_customer_forward_lock",
        ),
        "tests/test_m16_disclosure_barriers_postgresql.py": (
            "test_disclosure_before_payoff_is_complete_old_snapshot",
            "test_payoff_before_disclosure_is_complete_new_snapshot",
            "test_batch_overdue_before_disclosure_is_one_complete_blocked_snapshot",
        ),
        "tests/test_m17_recovery_service_postgresql.py": (
            "test_two_terminal_attempts_serialize_to_one_payment_and_one_plus_ten",
        ),
    }
    for relative_path, test_names in evidence.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for test_name in test_names:
            assert f"def {test_name}" in source

    this_source = (ROOT / "tests/test_m17_global_lock_order_postgresql.py").read_text(
        encoding="utf-8"
    )
    barrier_sources = (
        this_source.split("def test_m17_stage_order_extends", 1)[0]
        + (ROOT / "tests/test_m17_recovery_service_postgresql.py").read_text(
            encoding="utf-8"
        )
    ).casefold()
    for forbidden in ("sleep(", "retry", "timeout", "nowait", "skip_locked"):
        assert forbidden not in barrier_sources


def _count(
    session: Session,
    model: type[RatingEvent] | type[AuditLog],
    debt_id,
    event_type: str,
) -> int:
    debt_column = AuditLog.object_id if model is AuditLog else RatingEvent.debt_id
    return int(
        session.scalar(
            select(func.count())
            .select_from(model)
            .where(
                debt_column == debt_id,
                model.event_type == event_type,
            )
        )
        or 0
    )


def _assert_ordered(source: str, *needles: str) -> None:
    positions = tuple(source.index(needle) for needle in needles)
    assert positions == tuple(sorted(positions))
