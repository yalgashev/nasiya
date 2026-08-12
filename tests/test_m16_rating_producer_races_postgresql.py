from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.idempotency.contracts import IdempotencyOutcome
from app.idempotency.models import IdempotencyKey
from app.payment.models import Payment
from app.payment.service import PaymentMutationRejected, record_debt_payment
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.rating.models import RatingEvent
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from tests.test_m16_rating_append_services_postgresql import _make_eligible_debts
from tests.test_payment_service_postgresql import PAYMENT_TIME, _command


def _run_payment(factory, *, actor, command, start: Barrier):
    start.wait()
    try:
        with factory.begin() as session:
            return record_debt_payment(
                session,
                actor=actor,
                command=command,
                rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
                payment_clock=lambda: PAYMENT_TIME,
            )
    except PaymentMutationRejected as exc:
        return exc


@pytest.mark.integration
@pytest.mark.parametrize("same_key", (True, False))
def test_parallel_exact_payoff_is_one_source_for_same_or_different_key(
    m2_test_database: Engine,
    same_key: bool,
) -> None:
    actor_id, shop_id, _relation_id, debt_ids = _make_eligible_debts(
        m2_test_database,
        count=1,
    )
    factory = create_database_session_factory(m2_test_database)
    first_key = uuid4()
    second_key = first_key if same_key else uuid4()
    actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=first_key,
    )
    _actor, second = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=second_key,
    )
    start = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                lambda command: _run_payment(
                    factory,
                    actor=actor,
                    command=command,
                    start=start,
                ),
                (first, second),
            )
        )

    if same_key:
        assert sorted(outcome.outcome for outcome in outcomes) == [
            IdempotencyOutcome.NEW,
            IdempotencyOutcome.REPLAY,
        ]
        assert outcomes[0].payment_id == outcomes[1].payment_id
    else:
        failures = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, PaymentMutationRejected)
        ]
        assert len(failures) == 1
        assert failures[0].error in {
            ErrorCode.DEBT_CHANGED,
            ErrorCode.DEBT_NOT_PAYABLE,
        }
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(RatingEvent)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2


@pytest.mark.integration
def test_parallel_eligible_debts_same_pair_day_keep_two_payments_one_bonus(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, relation_id, debt_ids = _make_eligible_debts(
        m2_test_database,
        count=2,
    )
    factory = create_database_session_factory(m2_test_database)
    commands = tuple(
        _command(
            actor_id=actor_id,
            shop_id=shop_id,
            debt_id=debt_id,
            amount="100000",
            revision=2,
            key=uuid4(),
        )[1]
        for debt_id in debt_ids
    )
    actor = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )[0]
    start = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                lambda command: _run_payment(
                    factory,
                    actor=actor,
                    command=command,
                    start=start,
                ),
                commands,
            )
        )

    assert all(outcome.outcome is IdempotencyOutcome.NEW for outcome in outcomes)
    with factory() as session:
        events = tuple(
            session.scalars(
                select(RatingEvent).where(
                    RatingEvent.shop_customer_id == relation_id,
                    RatingEvent.event_type == "on_time_paid",
                )
            )
        )
        assert session.scalar(select(func.count()).select_from(Payment)) == 2
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 2
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 4
        assert len(events) == 1


@pytest.mark.integration
def test_same_customer_two_shops_serialize_complete_two_pair_bonus_state(
    m2_test_database: Engine,
) -> None:
    actor_id, first_shop_id, first_relation_id, debt_ids = _make_eligible_debts(
        m2_test_database,
        count=1,
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        first_relation = session.get_one(ShopCustomer, first_relation_id)
        second_shop = Shop(
            name=f"M16 producer {uuid4().hex[:8]}",
            phone=f"+998{uuid4().int % 1_000_000_000:09d}",
            created_at=PAYMENT_TIME - timedelta(days=10),
            updated_at=PAYMENT_TIME - timedelta(days=10),
        )
        session.add(second_shop)
        session.flush()
        session.add(
            ShopStaff(
                shop_id=second_shop.id,
                user_id=actor_id,
                role=ShopRole.CASHIER.value,
                is_active=True,
                created_at=PAYMENT_TIME - timedelta(days=10),
                updated_at=PAYMENT_TIME - timedelta(days=10),
            )
        )
        second_relation = ShopCustomer(
            shop_id=second_shop.id,
            customer_id=first_relation.customer_id,
            credit_limit_uzs=Decimal("1000000"),
            max_open_debts=5,
            list_status="normal",
            revision=1,
            created_by_user_id=actor_id,
            created_at=PAYMENT_TIME - timedelta(days=10),
            updated_at=PAYMENT_TIME - timedelta(days=10),
        )
        session.add(second_relation)
        session.flush()
        first_debt = session.get_one(Debt, debt_ids[0])
        second_debt = Debt(
            shop_customer_id=second_relation.id,
            created_by_user_id=actor_id,
            original_amount_uzs=Decimal("100000"),
            discount_basis_points=0,
            discounted_amount_uzs=Decimal("100000"),
            due_date=first_debt.due_date,
            pending_expires_at=first_debt.pending_expires_at,
            status="active",
            revision=2,
            accepted_at=first_debt.accepted_at,
            created_at=first_debt.created_at,
            updated_at=first_debt.accepted_at,
        )
        session.add(second_debt)
        session.flush()
        second_shop_id = second_shop.id
        second_relation_id = second_relation.id
        second_debt_id = second_debt.id

    first_actor, first_command = _command(
        actor_id=actor_id,
        shop_id=first_shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )
    second_actor, second_command = _command(
        actor_id=actor_id,
        shop_id=second_shop_id,
        debt_id=second_debt_id,
        amount="100000",
        revision=2,
        key=uuid4(),
    )
    start = Barrier(2)

    def run(pair):
        actor, command = pair
        return _run_payment(factory, actor=actor, command=command, start=start)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            pool.map(
                run,
                ((first_actor, first_command), (second_actor, second_command)),
            )
        )

    assert all(outcome.outcome is IdempotencyOutcome.NEW for outcome in outcomes)
    with factory() as session:
        events = tuple(session.scalars(select(RatingEvent)))
        assert len(events) == 2
        assert {event.shop_customer_id for event in events} == {
            first_relation_id,
            second_relation_id,
        }
        assert session.scalar(select(func.count()).select_from(Payment)) == 2


class _NthAuditFault:
    def __init__(self, original, *, fail_on: int) -> None:
        self._original = original
        self._fail_on = fail_on
        self._calls = 0

    def __call__(self, *args, **kwargs):
        self._calls += 1
        if self._calls == self._fail_on:
            raise RuntimeError("redacted audit fault")
        return self._original(*args, **kwargs)


@pytest.mark.integration
@pytest.mark.parametrize("fail_on", (1, 2))
def test_first_or_second_payment_audit_fault_rolls_back_bonus_and_financial_unit(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fail_on: int,
) -> None:
    from app.payment import service

    actor_id, shop_id, _relation_id, debt_ids = _make_eligible_debts(
        m2_test_database,
        count=1,
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
    original_payment = service.append_payment_recorded_audit
    original_paid = service.append_debt_paid_audit
    calls = 0

    def maybe_fail_payment(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise RuntimeError("redacted audit fault")
        return original_payment(*args, **kwargs)

    def maybe_fail_paid(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_on:
            raise RuntimeError("redacted audit fault")
        return original_paid(*args, **kwargs)

    monkeypatch.setattr(service, "append_payment_recorded_audit", maybe_fail_payment)
    monkeypatch.setattr(service, "append_debt_paid_audit", maybe_fail_paid)

    with pytest.raises(RuntimeError, match="redacted audit fault"):
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
                payment_clock=lambda: PAYMENT_TIME,
            )

    with factory() as session:
        debt = session.get_one(Debt, debt_ids[0])
        assert debt.status == "active" and debt.revision == 2
        for model in (Payment, RatingEvent, IdempotencyKey, AuditLog):
            assert session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.integration
def test_suspended_exact_payoff_is_zero_write_and_redacted(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _relation_id, debt_ids = _make_eligible_debts(
        m2_test_database,
        count=1,
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        from app.shop.models import Shop

        session.get_one(Shop, shop_id).status = "suspended"
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )

    with pytest.raises(PaymentMutationRejected) as caught:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                rating_append_port=SqlAlchemyLockedRatingAppendAdapter(),
                payment_clock=lambda: PAYMENT_TIME,
            )

    assert caught.value.error is ErrorCode.SHOP_SUSPENDED
    assert str(debt_ids[0]) not in str(caught.value)
    with factory() as session:
        for model in (Payment, RatingEvent, IdempotencyKey, AuditLog):
            assert session.scalar(select(func.count()).select_from(model)) == 0
