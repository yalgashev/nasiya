from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

import app.debt.customer_accept_service as accept_service
import app.debt.expiry_service as expiry_service
import app.debt.targeting as debt_targeting
import app.debt.tenant_cancel_service as cancel_service
import app.payment.service as payment_service
import app.payment.targeting as payment_targeting
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.customer_accept_service import accept_own_customer_debt
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.customer_decision_targeting import lock_customer_debt_predecessors
from app.debt.enums import DebtStatus
from app.debt.expiry_service import expire_pending_debts
from app.debt.expiry_targeting import lock_debt_for_expiry
from app.debt.models import Debt
from app.debt.service import create_pending_debt_proposal
from app.debt.tenant_cancel_targeting import lock_tenant_debt_for_cancel
from app.debt.values import ShopCustomerId
from app.idempotency.contracts import (
    IdempotencyEndpoint,
    canonical_idempotency_key_digest,
)
from app.idempotency.models import IdempotencyKey
from app.idempotency.repository import insert_or_resolve_key
from app.offers.enums import OfferLanguage
from app.payment.models import Payment
from app.payment.repository import payment_open_set_reader_factory
from app.payment.service import PaymentMutationRejected, record_debt_payment
from app.shop.models import Shop
from tests.test_customer_debt_accept_postgresql import (
    _command as _accept_command,
)
from tests.test_customer_debt_accept_postgresql import (
    _offer_text_id,
)
from tests.test_debt_creation_gates_postgresql import (
    _add_complete_offer,
    _add_debt,
    _create_command,
    _seed_target,
)
from tests.test_payment_exposure_integration_postgresql import (
    _add_active_debt,
    _payment_command,
    _seed_authority,
)
from tests.test_payment_service_postgresql import _command
from tests.test_payment_targeting_postgresql import _seed_one
from tests.test_tenant_debt_cancel_postgresql import _command as _cancel_command

pytestmark = pytest.mark.integration

PAYMENT_TIME = datetime(2026, 8, 10, 12, tzinfo=UTC)
EVENT_TIMEOUT = 5
FUTURE_TIMEOUT = 10


def _ordered(source: str, *needles: str) -> bool:
    positions = tuple(source.index(needle) for needle in needles)
    return positions == tuple(sorted(positions))


def test_m13_m14_shared_paths_have_one_forward_order_and_append_tail() -> None:
    payment_target = inspect.getsource(
        payment_targeting.lock_tenant_payment_predecessors
    )
    assert _ordered(
        payment_target,
        "lock_shop_for_update",
        "lock_actor_shop_staff_for_update",
        "select(User)",
        "lock_existing_own_customer_for_update",
        "lock_shop_customer_by_tenant_locator",
    )

    payment = inspect.getsource(record_debt_payment)
    assert _ordered(
        payment,
        "lock_tenant_payment_predecessors",
        "insert_or_resolve_key",
        "lock_tenant_payment_debt",
        "read_locked_payment_balance",
        "insert_payment",
        "update_locked_debt",
        "append_payment_recorded_audit",
        "append_debt_paid_audit",
    )
    assert _ordered(
        inspect.getsource(debt_targeting.lock_debt_target_before_offer),
        "lock_shop_for_update",
        "lock_actor_shop_staff_for_update",
        "lock_actor_and_target_users_for_update",
        "get_telegram_link_by_user_for_update",
        "lock_active_customer_for_target_user",
    )
    assert "lock_shop_for_update" in inspect.getsource(lock_customer_debt_predecessors)
    assert "lock_shop_for_update" in inspect.getsource(lock_tenant_debt_for_cancel)
    assert _ordered(
        inspect.getsource(lock_debt_for_expiry),
        "lock_shop_for_update",
        "lock_shop_customer_by_tenant_locator",
        ".with_for_update()",
    )

    combined = "\n".join(
        inspect.getsource(operation)
        for operation in (
            record_debt_payment,
            payment_targeting.lock_tenant_payment_predecessors,
            payment_targeting.lock_tenant_payment_debt,
            create_pending_debt_proposal,
            accept_own_customer_debt,
            lock_tenant_debt_for_cancel,
            lock_debt_for_expiry,
        )
    ).casefold()
    for forbidden in (
        "sleep(",
        "retry",
        "nowait",
        "skip_locked",
        "lock_timeout",
        "pg_advisory",
    ):
        assert forbidden not in combined


@pytest.mark.parametrize("same_debt", (True, False))
def test_payment_paths_wait_at_forward_shop_predecessor_before_debt_lock(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    same_debt: bool,
) -> None:
    actor_id, shop_id, _staff_id, relation_id, first_debt_id = _seed_one(
        m2_test_database
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        second_debt_id = first_debt_id
        if not same_debt:
            second_debt_id = _add_active_debt(
                session,
                shop_customer_id=relation_id,
                actor_id=actor_id,
                original="1000",
                discounted="1000",
            ).id

    actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=first_debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    _actor, second = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=second_debt_id,
        amount="100",
        revision=2,
        key=uuid4(),
    )
    first_holds_locks = Event()
    release_first = Event()
    original_insert_payment = payment_service.insert_payment

    def pause_first_at_append(*args, **kwargs):
        result = original_insert_payment(*args, **kwargs)
        payment = kwargs["payment"]
        if payment.amount.value == Decimal("400"):
            first_holds_locks.set()
            assert release_first.wait(timeout=EVENT_TIMEOUT)
        return result

    monkeypatch.setattr(payment_service, "insert_payment", pause_first_at_append)

    def run(command):
        try:
            with factory.begin() as session:
                return record_debt_payment(
                    session,
                    actor=actor,
                    command=command,
                    payment_clock=lambda: PAYMENT_TIME,
                )
        except PaymentMutationRejected as exc:
            return exc

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        first_future = pool.submit(run, first)
        assert first_holds_locks.wait(timeout=EVENT_TIMEOUT)

        second_shop_attempted = Event()
        original_shop_lock = payment_targeting.lock_shop_for_update

        def observe_second_shop_lock(*args, **kwargs):
            second_shop_attempted.set()
            return original_shop_lock(*args, **kwargs)

        monkeypatch.setattr(
            payment_targeting, "lock_shop_for_update", observe_second_shop_lock
        )
        second_future = pool.submit(run, second)
        assert second_shop_attempted.wait(timeout=EVENT_TIMEOUT)
        assert not second_future.done()
    finally:
        release_first.set()
    first_result = first_future.result(timeout=FUTURE_TIMEOUT)
    second_result = second_future.result(timeout=FUTURE_TIMEOUT)
    pool.shutdown(wait=True)

    assert not isinstance(first_result, PaymentMutationRejected)
    if same_debt:
        assert isinstance(second_result, PaymentMutationRejected)
        assert second_result.error is ErrorCode.DEBT_CHANGED
    else:
        assert not isinstance(second_result, PaymentMutationRejected)

    with factory() as session:
        expected = 1 if same_debt else 2
        assert session.scalar(select(func.count()).select_from(Payment)) == expected
        assert (
            session.scalar(select(func.count()).select_from(IdempotencyKey)) == expected
        )
        assert session.scalar(select(func.count()).select_from(AuditLog)) == expected


@pytest.mark.parametrize(
    ("original", "discounted", "payment_amount", "new_amount", "max_open"),
    (
        ("1000", "1000", "400", "400", 3),
        ("1000", "1000", "1000", "1000", 1),
        ("1000", "800", "200", "200", 3),
    ),
)
def test_payment_before_new_debt_is_a_complete_after_state_under_shop_barrier(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    original: str,
    discounted: str,
    payment_amount: str,
    new_amount: str,
    max_open: int,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000", max_open_debts=max_open)
        _add_complete_offer(session, actor=seed.actor)
        debt = _add_active_debt(
            session,
            shop_customer_id=seed.shop_customer.id,
            actor_id=seed.actor.id,
            original=original,
            discounted=discounted,
        )
        actor_id = seed.actor.id
        shop_id = seed.shop.id
        relation_id = seed.shop_customer.id
        debt_id = debt.id
    actor, payment_command = _payment_command(
        actor_id, shop_id, debt_id, amount=payment_amount
    )
    debt_authority = _seed_authority(actor_id, shop_id)
    first_holds_locks = Event()
    release_first = Event()
    original_insert_payment = payment_service.insert_payment

    def pause_payment_append(*args, **kwargs):
        result = original_insert_payment(*args, **kwargs)
        first_holds_locks.set()
        assert release_first.wait(timeout=EVENT_TIMEOUT)
        return result

    monkeypatch.setattr(payment_service, "insert_payment", pause_payment_append)

    def pay():
        with factory.begin() as session:
            return record_debt_payment(
                session,
                actor=actor,
                command=payment_command,
                payment_clock=lambda: PAYMENT_TIME,
            )

    def create():
        with factory.begin() as session:
            return create_pending_debt_proposal(
                session,
                authority=debt_authority,
                shop_customer_id=ShopCustomerId(relation_id),
                command=_create_command(amount=new_amount),
                open_set_reader_factory=payment_open_set_reader_factory,
            )

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        payment_future = pool.submit(pay)
        assert first_holds_locks.wait(timeout=EVENT_TIMEOUT)

        create_shop_attempted = Event()
        original_shop_lock = debt_targeting.lock_shop_for_update

        def observe_create_shop_lock(*args, **kwargs):
            create_shop_attempted.set()
            return original_shop_lock(*args, **kwargs)

        monkeypatch.setattr(
            debt_targeting, "lock_shop_for_update", observe_create_shop_lock
        )
        create_future = pool.submit(create)
        assert create_shop_attempted.wait(timeout=EVENT_TIMEOUT)
        assert not create_future.done()
    finally:
        release_first.set()
    payment_result = payment_future.result(timeout=FUTURE_TIMEOUT)
    create_result = create_future.result(timeout=FUTURE_TIMEOUT)
    pool.shutdown(wait=True)

    assert payment_result.payment_id is not None
    assert create_result.error is None and create_result.debt_id is not None
    with factory() as session:
        original_debt = session.get(Debt, debt_id)
        created_debt = session.get(Debt, create_result.debt_id.as_uuid())
        assert original_debt is not None and created_debt is not None
        posted = session.scalar(
            select(func.sum(Payment.amount_uzs)).where(Payment.debt_id == debt_id)
        )
        assert posted == Decimal(payment_amount)
        expected_remaining = Decimal(discounted) - Decimal(payment_amount)
        assert expected_remaining >= 0
        if expected_remaining == 0:
            assert original_debt.status == "paid"
        else:
            assert original_debt.status == "active"
        assert created_debt.status == "pending"
        original_exposure = (
            Decimal("0")
            if original_debt.status == "paid"
            else Decimal(original) - Decimal(payment_amount)
        )
        assert original_exposure + created_debt.original_amount_uzs == Decimal("1000")


def test_pending_accept_completes_before_payment_as_one_forward_ordered_history(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session)
        _add_complete_offer(session, actor=seed.actor)
        _add_debt(session, seed=seed, amount="100", status=DebtStatus.PENDING)
        debt = session.scalar(
            select(Debt).where(Debt.shop_customer_id == seed.shop_customer.id)
        )
        assert debt is not None
        authority = CustomerDebtAuthority(
            user_id=seed.target.id,
            customer_id=seed.customer.id,
        )
        accept_command = _accept_command(
            debt_id=debt.id,
            offer_text_id=_offer_text_id(session, OfferLanguage.RU),
        )
        actor_id, shop_id, debt_id = seed.actor.id, seed.shop.id, debt.id
    actor, payment_command = _payment_command(actor_id, shop_id, debt_id, amount="100")
    transition_holds_locks = Event()
    release_transition = Event()
    original_append = accept_service.append_audit_event

    def pause_accept_append(*args, **kwargs):
        result = original_append(*args, **kwargs)
        transition_holds_locks.set()
        assert release_transition.wait(timeout=EVENT_TIMEOUT)
        return result

    monkeypatch.setattr(accept_service, "append_audit_event", pause_accept_append)

    def accept():
        with factory.begin() as session:
            return accept_own_customer_debt(
                session,
                authority=authority,
                command=accept_command,
            )

    def pay():
        try:
            with factory.begin() as session:
                return record_debt_payment(
                    session,
                    actor=actor,
                    command=payment_command,
                    payment_clock=lambda: PAYMENT_TIME,
                )
        except PaymentMutationRejected as exc:
            return exc

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        accept_future = pool.submit(accept)
        assert transition_holds_locks.wait(timeout=EVENT_TIMEOUT)
        payment_shop_attempted = Event()
        original_shop_lock = payment_targeting.lock_shop_for_update

        def observe_payment_shop_lock(*args, **kwargs):
            payment_shop_attempted.set()
            return original_shop_lock(*args, **kwargs)

        monkeypatch.setattr(
            payment_targeting, "lock_shop_for_update", observe_payment_shop_lock
        )
        payment_future = pool.submit(pay)
        assert payment_shop_attempted.wait(timeout=EVENT_TIMEOUT)
        assert not payment_future.done()
    finally:
        release_transition.set()
    accept_result = accept_future.result(timeout=FUTURE_TIMEOUT)
    payment_result = payment_future.result(timeout=FUTURE_TIMEOUT)
    pool.shutdown(wait=True)

    assert accept_result.error is None
    assert not isinstance(payment_result, PaymentMutationRejected)
    with factory() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == DebtStatus.PAID.value
        assert debt.revision == 3
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 3


@pytest.mark.parametrize("transition", ("cancel", "expire"))
def test_pending_terminal_transition_completes_before_payment_with_zero_ledger_write(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session)
        _add_debt(session, seed=seed, amount="100", status=DebtStatus.PENDING)
        debt = session.scalar(
            select(Debt).where(Debt.shop_customer_id == seed.shop_customer.id)
        )
        assert debt is not None
        actor_id, shop_id, debt_id = seed.actor.id, seed.shop.id, debt.id
        pending_expires_at = debt.pending_expires_at
        cancel_command = _cancel_command(debt_id=debt.id)
        debt_authority = seed.authority
    actor, payment_command = _payment_command(actor_id, shop_id, debt_id, amount="100")
    transition_holds_locks = Event()
    release_transition = Event()
    transition_module = cancel_service if transition == "cancel" else expiry_service
    original_append = transition_module.append_audit_event

    def pause_terminal_append(*args, **kwargs):
        result = original_append(*args, **kwargs)
        transition_holds_locks.set()
        assert release_transition.wait(timeout=EVENT_TIMEOUT)
        return result

    monkeypatch.setattr(transition_module, "append_audit_event", pause_terminal_append)

    def terminate():
        if transition == "cancel":
            with factory.begin() as session:
                return cancel_service.cancel_tenant_debt(
                    session,
                    authority=debt_authority,
                    command=cancel_command,
                )
        return expire_pending_debts(
            factory,
            now=pending_expires_at,
            batch_size=1,
        )

    def pay():
        try:
            with factory.begin() as session:
                return record_debt_payment(
                    session,
                    actor=actor,
                    command=payment_command,
                    payment_clock=lambda: PAYMENT_TIME,
                )
        except PaymentMutationRejected as exc:
            return exc

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        transition_future = pool.submit(terminate)
        assert transition_holds_locks.wait(timeout=EVENT_TIMEOUT)
        payment_shop_attempted = Event()
        original_shop_lock = payment_targeting.lock_shop_for_update

        def observe_payment_shop_lock(*args, **kwargs):
            payment_shop_attempted.set()
            return original_shop_lock(*args, **kwargs)

        monkeypatch.setattr(
            payment_targeting, "lock_shop_for_update", observe_payment_shop_lock
        )
        payment_future = pool.submit(pay)
        assert payment_shop_attempted.wait(timeout=EVENT_TIMEOUT)
        assert not payment_future.done()
    finally:
        release_transition.set()
    transition_result = transition_future.result(timeout=FUTURE_TIMEOUT)
    payment_result = payment_future.result(timeout=FUTURE_TIMEOUT)
    pool.shutdown(wait=True)

    if transition == "cancel":
        assert transition_result.error is None
        expected_status = DebtStatus.CANCELLED
    else:
        assert transition_result.expired_count == 1
        expected_status = DebtStatus.EXPIRED
    assert isinstance(payment_result, PaymentMutationRejected)
    assert payment_result.error is ErrorCode.DEBT_NOT_PAYABLE
    with factory() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == expected_status.value
        assert debt.revision == 2
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1


def test_payment_clock_is_not_captured_until_the_forward_lock_wait_finishes(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1",
        revision=2,
        key=uuid4(),
    )
    blocker = factory()
    blocker.begin()
    locked_shop = blocker.scalar(
        select(Shop).where(Shop.id == shop_id).with_for_update()
    )
    assert locked_shop is not None

    shop_lock_attempted = Event()
    clock_called = Event()
    original_shop_lock = payment_targeting.lock_shop_for_update

    def observe_shop_lock(*args, **kwargs):
        shop_lock_attempted.set()
        return original_shop_lock(*args, **kwargs)

    def clock() -> datetime:
        clock_called.set()
        return PAYMENT_TIME

    monkeypatch.setattr(payment_targeting, "lock_shop_for_update", observe_shop_lock)

    def pay():
        with factory.begin() as session:
            return record_debt_payment(
                session,
                actor=actor,
                command=command,
                payment_clock=clock,
            )

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(pay)
    try:
        assert shop_lock_attempted.wait(timeout=EVENT_TIMEOUT)
        assert not future.done()
        assert not clock_called.is_set()
    finally:
        blocker.rollback()
        blocker.close()
    result = future.result(timeout=FUTURE_TIMEOUT)
    pool.shutdown(wait=True)

    assert result.payment_id is not None
    assert clock_called.is_set()


def test_same_key_waits_for_unique_resolution_then_persists_one_key(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    key = uuid4()
    _actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=key,
    )
    blocker = factory()
    blocker.begin()
    insert_or_resolve_key(
        blocker,
        actor_user_id=actor_id,
        endpoint=IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
        key_digest=canonical_idempotency_key_digest(command.idempotency_key),
        request_hash=command.request_hash,
        result_object_id=uuid4(),
        now=PAYMENT_TIME,
    )

    key_insert_attempted = Event()

    def insert_same_key():
        key_insert_attempted.set()
        with factory.begin() as session:
            return insert_or_resolve_key(
                session,
                actor_user_id=actor_id,
                endpoint=IdempotencyEndpoint.SHOP_DEBT_PAYMENTS_CREATE,
                key_digest=canonical_idempotency_key_digest(command.idempotency_key),
                request_hash=command.request_hash,
                result_object_id=uuid4(),
                now=PAYMENT_TIME,
            )

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(insert_same_key)
    try:
        assert key_insert_attempted.wait(timeout=EVENT_TIMEOUT)
        assert not future.done()
    finally:
        if blocker.in_transaction():
            blocker.rollback()
        blocker.close()
    result = future.result(timeout=FUTURE_TIMEOUT)
    pool.shutdown(wait=True)

    assert result.outcome.value == "new"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert session.scalar(select(Shop.id).where(Shop.id == shop_id)) == shop_id
