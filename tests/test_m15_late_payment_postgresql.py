from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.enums import DebtBalanceBasis
from app.debt.models import Debt
from app.debt.service import create_pending_debt_proposal
from app.debt.values import ShopCustomerId, ShopId
from app.idempotency.contracts import IdempotencyOutcome
from app.idempotency.models import IdempotencyKey
from app.payment.commands import CreatePaymentV2RawForm, assemble_create_payment_request
from app.payment.models import Payment
from app.payment.read_service import compose_payment_receipt
from app.payment.repository import get_tenant_payment
from app.payment.service import (
    PaymentMutationRejected,
    resolve_completed_m14_payment_replay,
)
from app.payment.values import PaymentId
from app.rating.models import RatingEvent
from tests.rating_support import record_debt_payment
from tests.test_debt_creation_gates_postgresql import (
    _add_complete_offer,
    _create_command,
    _seed_target,
)
from tests.test_payment_service_postgresql import _command
from tests.test_payment_targeting_postgresql import _seed_one

LATE_NOW = datetime(2026, 8, 9, 19, tzinfo=UTC)
ON_TIME_NOW = datetime(2026, 8, 9, 18, 59, 59, 999999, tzinfo=UTC)


def _seed_discounted_late(engine: Engine) -> tuple:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(engine)
    factory = create_database_session_factory(engine)
    with factory.begin() as session:
        debt = session.get_one(Debt, debt_id)
        debt.original_amount_uzs = Decimal("1000")
        debt.discount_basis_points = 2000
        debt.discounted_amount_uzs = Decimal("800")
        debt.created_at = datetime(2026, 8, 1, tzinfo=UTC)
        debt.pending_expires_at = datetime(2026, 8, 4, tzinfo=UTC)
        debt.accepted_at = datetime(2026, 8, 4, tzinfo=UTC)
        debt.updated_at = debt.accepted_at
        debt.due_date = date(2026, 8, 9)
    return actor_id, shop_id, debt_id


@pytest.mark.integration
@pytest.mark.parametrize(
    ("amount", "expected_status", "expected_revision", "audit_count"),
    (("100", "overdue", 4, 3), ("1000", "paid", 4, 4)),
)
def test_inline_late_payment_rolls_over_then_records_atomically(
    m2_test_database: Engine,
    amount: str,
    expected_status: str,
    expected_revision: int,
    audit_count: int,
) -> None:
    actor_id, shop_id, debt_id = _seed_discounted_late(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount=amount,
        revision=2,
        key=uuid4(),
        basis="original",
    )
    factory = create_database_session_factory(m2_test_database)

    with factory.begin() as session:
        result = record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: LATE_NOW,
        )

    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        payment = session.get_one(Payment, result.payment_id.as_uuid())
        assert debt.status == expected_status
        assert debt.revision == expected_revision
        assert debt.overdue_revision == 3
        assert debt.overdue_at == LATE_NOW
        assert debt.updated_at == payment.created_at == LATE_NOW
        assert payment.debt_revision_after == 4
        assert payment.amount_uzs == Decimal(amount)
        assert session.scalar(select(func.count()).select_from(AuditLog)) == audit_count
        rating = session.scalar(
            select(RatingEvent).where(RatingEvent.debt_id == debt_id)
        )
        assert rating is not None
        assert rating.event_type == "overdue" and rating.delta == -15
        assert rating.occurred_at == LATE_NOW
        if expected_status == "paid":
            assert debt.paid_at == LATE_NOW
        else:
            assert debt.paid_at is None


@pytest.mark.integration
def test_debt_lock_midnight_wait_uses_post_lock_clock_and_rejects_stale_basis(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_discounted_late(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=2,
        key=uuid4(),
        basis="discounted",
    )
    factory = create_database_session_factory(m2_test_database)
    lock_held = Event()
    release_lock = Event()
    clock_called = Event()

    def hold_debt_lock() -> None:
        with factory.begin() as session:
            session.scalar(select(Debt).where(Debt.id == debt_id).with_for_update())
            lock_held.set()
            assert release_lock.wait(timeout=10)

    def attempt_payment() -> ErrorCode:
        assert lock_held.wait(timeout=10)

        def clock() -> datetime:
            clock_called.set()
            return LATE_NOW

        try:
            with factory.begin() as session:
                record_debt_payment(
                    session,
                    actor=actor,
                    command=command,
                    payment_clock=clock,
                )
        except PaymentMutationRejected as rejected:
            return rejected.error
        raise AssertionError("stale midnight basis unexpectedly mutated")

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder = pool.submit(hold_debt_lock)
        attempt = pool.submit(attempt_payment)
        assert lock_held.wait(timeout=10)
        assert not clock_called.is_set()
        release_lock.set()
        holder.result(timeout=10)
        assert attempt.result(timeout=10) is ErrorCode.DEBT_CHANGED

    assert clock_called.is_set()
    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "active" and debt.revision == 2
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0


@pytest.mark.integration
def test_on_time_basis_stays_discounted_at_last_tashkent_microsecond(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_discounted_late(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="800",
        revision=2,
        key=uuid4(),
        basis="discounted",
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        result = record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: ON_TIME_NOW,
        )
    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "paid" and debt.revision == 3
        assert debt.overdue_at is None and debt.overdue_revision is None
        payment = session.get_one(Payment, result.payment_id.as_uuid())
        assert payment.amount_uzs == Decimal("800")


@pytest.mark.integration
def test_persisted_overdue_partial_adds_only_one_revision_and_preserves_marker(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_discounted_late(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=2,
        key=uuid4(),
        basis="original",
    )
    with factory.begin() as session:
        record_debt_payment(
            session, actor=actor, command=first, payment_clock=lambda: LATE_NOW
        )
    actor, second = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=4,
        key=uuid4(),
        basis="original",
    )
    later = datetime(2026, 8, 10, 8, tzinfo=UTC)
    with factory.begin() as session:
        record_debt_payment(
            session, actor=actor, command=second, payment_clock=lambda: later
        )
    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "overdue" and debt.revision == 5
        assert debt.overdue_revision == 3 and debt.overdue_at == LATE_NOW
        assert debt.updated_at == later
        assert session.scalar(select(func.count()).select_from(Payment)) == 2
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 4


@pytest.mark.integration
def test_missing_basis_resolves_only_an_already_completed_v1_row(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    key = uuid4()
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=2,
        key=key,
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )

    assembled = assemble_create_payment_request(
        actor=actor,
        form=CreatePaymentV2RawForm(
            debt_id=str(debt_id),
            amount_uzs="100",
            method="cash",
            idempotency_key=str(key),
            expected_revision="2",
            expected_balance_basis=None,
        ),
        header_idempotency_key=str(key),
    )
    assert assembled.legacy_completed_replay is not None
    with factory.begin() as session:
        row = session.scalar(select(IdempotencyKey))
        assert row is not None
        row.request_hash = assembled.legacy_completed_replay.request_hash.value

    with factory.begin() as session:
        replay = resolve_completed_m14_payment_replay(
            session,
            actor=actor,
            candidate=assembled.legacy_completed_replay,
        )
    assert replay.outcome is IdempotencyOutcome.REPLAY
    assert replay.payment_id == created.payment_id

    absent_key = uuid4()
    absent = assemble_create_payment_request(
        actor=actor,
        form=CreatePaymentV2RawForm(
            debt_id=str(debt_id),
            amount_uzs="100",
            method="cash",
            idempotency_key=str(absent_key),
            expected_revision="3",
            expected_balance_basis=None,
        ),
        header_idempotency_key=str(absent_key),
    )
    assert absent.legacy_completed_replay is not None
    with pytest.raises(PaymentMutationRejected) as rejected:
        with factory.begin() as session:
            resolve_completed_m14_payment_replay(
                session,
                actor=actor,
                candidate=absent.legacy_completed_replay,
            )
    assert rejected.value.error is ErrorCode.VALIDATION_ERROR


@pytest.mark.integration
def test_pre_clawback_receipt_stays_discounted_while_current_uses_original(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_discounted_late(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, on_time = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=2,
        key=uuid4(),
        basis="discounted",
    )
    with factory.begin() as session:
        first = record_debt_payment(
            session,
            actor=actor,
            command=on_time,
            payment_clock=lambda: ON_TIME_NOW,
        )
    actor, late = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=3,
        key=uuid4(),
        basis="original",
    )
    with factory.begin() as session:
        second = record_debt_payment(
            session,
            actor=actor,
            command=late,
            payment_clock=lambda: LATE_NOW,
        )

    with factory() as session:
        first_row = get_tenant_payment(
            session,
            shop_id=ShopId(shop_id),
            payment_id=PaymentId(first.payment_id.as_uuid()),
        )
        second_row = get_tenant_payment(
            session,
            shop_id=ShopId(shop_id),
            payment_id=PaymentId(second.payment_id.as_uuid()),
        )
        assert first_row is not None and second_row is not None
        first_receipt = compose_payment_receipt(
            session, row=first_row, server_now=LATE_NOW
        )
        second_receipt = compose_payment_receipt(
            session, row=second_row, server_now=LATE_NOW
        )
        assert first_receipt.historical_balance_basis is DebtBalanceBasis.DISCOUNTED
        assert first_receipt.historical_balance_after.value == Decimal("700")
        assert first_receipt.current_balance_basis is DebtBalanceBasis.ORIGINAL
        assert first_receipt.current_balance.value == Decimal("800")
        assert second_receipt.historical_balance_basis is DebtBalanceBasis.ORIGINAL
        assert second_receipt.historical_balance_after.value == Decimal("800")


@pytest.mark.integration
def test_multiple_receipts_preserve_pre_and_post_clawback_paid_late_history(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_discounted_late(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    payment_specs = (
        ("100", 2, "discounted", ON_TIME_NOW),
        ("200", 3, "discounted", ON_TIME_NOW),
        ("300", 4, "original", LATE_NOW),
        ("400", 6, "original", LATE_NOW.replace(second=1)),
    )
    results = []
    for amount, revision, basis, captured_at in payment_specs:
        actor, command = _command(
            actor_id=actor_id,
            shop_id=shop_id,
            debt_id=debt_id,
            amount=amount,
            revision=revision,
            key=uuid4(),
            basis=basis,
        )
        with factory.begin() as session:
            results.append(
                record_debt_payment(
                    session,
                    actor=actor,
                    command=command,
                    payment_clock=lambda captured_at=captured_at: captured_at,
                )
            )

    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "paid"
        assert debt.revision == 7 and debt.overdue_revision == 5
        receipts = []
        for result in results:
            row = get_tenant_payment(
                session,
                shop_id=ShopId(shop_id),
                payment_id=PaymentId(result.payment_id.as_uuid()),
            )
            assert row is not None
            receipts.append(
                compose_payment_receipt(session, row=row, server_now=LATE_NOW)
            )

    assert [receipt.historical_balance_basis for receipt in receipts] == [
        DebtBalanceBasis.DISCOUNTED,
        DebtBalanceBasis.DISCOUNTED,
        DebtBalanceBasis.ORIGINAL,
        DebtBalanceBasis.ORIGINAL,
    ]
    assert [receipt.historical_balance_after.value for receipt in receipts] == [
        Decimal("700"),
        Decimal("500"),
        Decimal("400"),
        Decimal("0"),
    ]
    assert all(
        receipt.current_balance_basis is DebtBalanceBasis.ORIGINAL
        and receipt.current_balance.value == Decimal("0")
        and receipt.current_debt_status.value == "paid"
        for receipt in receipts
    )


@pytest.mark.integration
def test_full_late_payoff_is_the_authoritative_unblock_source_state(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000", max_open_debts=3)
        _add_complete_offer(session, actor=seed.actor)
        debt = Debt(
            shop_customer_id=seed.shop_customer.id,
            created_by_user_id=seed.actor.id,
            original_amount_uzs=Decimal("100"),
            discount_basis_points=0,
            discounted_amount_uzs=Decimal("100"),
            due_date=date(2026, 8, 9),
            pending_expires_at=datetime(2026, 8, 4, tzinfo=UTC),
            status="active",
            revision=2,
            accepted_at=datetime(2026, 8, 4, tzinfo=UTC),
            created_at=datetime(2026, 8, 1, tzinfo=UTC),
            updated_at=datetime(2026, 8, 4, tzinfo=UTC),
        )
        session.add(debt)
        session.flush()
        actor_id = seed.actor.id
        shop_id = seed.shop.id
        debt_id = debt.id
        authority = seed.authority
        relation_id = seed.shop_customer.id

    with factory.begin() as session:
        blocked = create_pending_debt_proposal(
            session,
            authority=authority,
            shop_customer_id=ShopCustomerId(relation_id),
            command=_create_command(),
            hard_block_clock=lambda: LATE_NOW,
        )
    assert blocked.error is ErrorCode.CUSTOMER_RATING_BLOCKED

    actor, payoff = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=2,
        key=uuid4(),
        basis="original",
    )
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=actor,
            command=payoff,
            payment_clock=lambda: LATE_NOW,
        )
    with factory.begin() as session:
        allowed = create_pending_debt_proposal(
            session,
            authority=authority,
            shop_customer_id=ShopCustomerId(relation_id),
            command=_create_command(),
            hard_block_clock=lambda: LATE_NOW,
        )
    assert allowed.error is None
