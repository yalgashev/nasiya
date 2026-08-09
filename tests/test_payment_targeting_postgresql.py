from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.debt.presentation import DebtWebLanguage
from app.debt.values import DebtId
from app.payment import targeting
from app.payment.dependencies import DetachedPaymentActorContext
from app.payment.targeting import (
    discover_tenant_payment_target,
    lock_tenant_payment_predecessors,
)
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _add_user(session, *, active: bool = True) -> User:
    row = User(phone=_phone(), is_active=active)
    session.add(row)
    session.flush()
    return row


def _add_shop_actor(
    session,
    *,
    role: ShopRole,
    shop_status: ShopStatus = ShopStatus.ACTIVE,
    actor_active: bool = True,
    staff_active: bool = True,
) -> tuple[User, Shop, ShopStaff]:
    actor = _add_user(session, active=actor_active)
    shop = Shop(
        name=f"M14 payment target {uuid4().hex[:8]}",
        phone=_phone(),
        status=shop_status.value,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(shop)
    session.flush()
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=actor.id,
        role=role.value,
        is_active=staff_active,
        created_at=NOW,
        updated_at=NOW if staff_active else NOW + timedelta(minutes=1),
        revoked_at=None if staff_active else NOW + timedelta(minutes=1),
    )
    session.add(staff)
    session.flush()
    return actor, shop, staff


def _add_customer(
    session, *, target_active: bool = False, customer_active: bool = False
) -> tuple[User, Customer]:
    target = _add_user(session, active=target_active)
    customer = Customer(
        user_id=target.id,
        onboarding_status="active" if customer_active else "draft",
        activated_at=NOW if customer_active else None,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()
    return target, customer


def _add_relation_and_debt(
    session,
    *,
    actor: User,
    shop: Shop,
    customer: Customer,
    list_status: str = "blacklisted",
) -> tuple[ShopCustomer, Debt]:
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=5,
        list_status=list_status,
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(relation)
    session.flush()
    debt = Debt(
        shop_customer_id=relation.id,
        created_by_user_id=actor.id,
        original_amount_uzs=Decimal("1000"),
        discount_basis_points=0,
        discounted_amount_uzs=Decimal("1000"),
        due_date=date(2026, 8, 20),
        pending_expires_at=NOW + timedelta(hours=72),
        status="active",
        revision=2,
        accepted_at=NOW + timedelta(hours=1),
        created_at=NOW,
        updated_at=NOW + timedelta(hours=1),
    )
    session.add(debt)
    session.flush()
    return relation, debt


def _actor_context(actor: User, shop: Shop) -> DetachedPaymentActorContext:
    return DetachedPaymentActorContext(
        actor_user_id=actor.id,
        current_shop_id=shop.id,
        role_hint=ShopRole.OWNER,
        language=DebtWebLanguage.UZ_LATN,
    )


def _seed_one(
    engine: Engine,
    *,
    role: ShopRole = ShopRole.CASHIER,
    shop_status: ShopStatus = ShopStatus.ACTIVE,
    actor_active: bool = True,
    staff_active: bool = True,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    factory = create_database_session_factory(engine)
    with factory.begin() as seed:
        actor, shop, staff = _add_shop_actor(
            seed,
            role=role,
            shop_status=shop_status,
            actor_active=actor_active,
            staff_active=staff_active,
        )
        _target, customer = _add_customer(seed)
        relation, debt = _add_relation_and_debt(
            seed, actor=actor, shop=shop, customer=customer
        )
        return actor.id, shop.id, staff.id, relation.id, debt.id


def _context(actor_id: UUID, shop_id: UUID) -> DetachedPaymentActorContext:
    return DetachedPaymentActorContext(
        actor_user_id=actor_id,
        current_shop_id=shop_id,
        role_hint=ShopRole.OWNER,
        language=DebtWebLanguage.UZ_LATN,
    )


@pytest.mark.integration
@pytest.mark.parametrize("role", tuple(ShopRole))
def test_all_live_shop_roles_lock_state_agnostic_repayment_predecessors(
    m2_test_database: Engine, role: ShopRole
) -> None:
    actor_id, shop_id, _staff_id, relation_id, debt_id = _seed_one(
        m2_test_database, role=role
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        actor = _context(actor_id, shop_id)
        candidate = discover_tenant_payment_target(
            session, actor=actor, debt_id=DebtId(debt_id)
        )
        result = lock_tenant_payment_predecessors(
            session, actor=actor, candidate=candidate
        )

        assert result.error is None
        assert result.locked is not None
        assert result.locked.role is role
        assert result.locked.shop_customer_id == relation_id
        assert result.locked.customer.onboarding_status == "draft"
        assert result.locked.locked_shop_customer.row.list_status == "blacklisted"
        assert result.locked.customer.user_id != actor_id
        target = session.get(User, result.locked.customer.user_id)
        assert target is not None and not target.is_active


@pytest.mark.integration
def test_payment_predecessor_chain_does_not_lock_debt(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    first = factory()
    try:
        first.begin()
        actor = _context(actor_id, shop_id)
        candidate = discover_tenant_payment_target(
            first, actor=actor, debt_id=DebtId(debt_id)
        )
        result = lock_tenant_payment_predecessors(
            first, actor=actor, candidate=candidate
        )
        assert result.error is None

        with factory.begin() as observer:
            locked_id = observer.scalar(
                select(Debt.id).where(Debt.id == debt_id).with_for_update(nowait=True)
            )
            assert locked_id == debt_id
    finally:
        first.rollback()
        first.close()


@pytest.mark.integration
def test_cross_shop_forged_candidate_and_absent_debt_are_indistinguishable_zero_write(
    m2_test_database: Engine,
) -> None:
    actor_a, shop_a, _staff_a, _relation_a, _debt_a = _seed_one(m2_test_database)
    actor_b, shop_b, _staff_b, _relation_b, debt_b = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    writes: list[str] = []

    def capture_writes(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(m2_test_database, "before_cursor_execute", capture_writes)
    try:
        with factory.begin() as session:
            authority_a = _context(actor_a, shop_a)
            authority_b = _context(actor_b, shop_b)
            cross_shop = discover_tenant_payment_target(
                session, actor=authority_a, debt_id=DebtId(debt_b)
            )
            absent = discover_tenant_payment_target(
                session, actor=authority_a, debt_id=DebtId(uuid4())
            )
            candidate_b = discover_tenant_payment_target(
                session, actor=authority_b, debt_id=DebtId(debt_b)
            )

            cross_result = lock_tenant_payment_predecessors(
                session, actor=authority_a, candidate=cross_shop
            )
            absent_result = lock_tenant_payment_predecessors(
                session, actor=authority_a, candidate=absent
            )
            forged_result = lock_tenant_payment_predecessors(
                session, actor=authority_a, candidate=candidate_b
            )

        assert cross_shop is None and absent is None
        assert cross_result.error is ErrorCode.DEBT_UNAVAILABLE
        assert absent_result.error is ErrorCode.DEBT_UNAVAILABLE
        assert forged_result.error is ErrorCode.DEBT_UNAVAILABLE
        assert writes == []
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_writes)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("shop_status", "actor_active", "staff_active", "expected"),
    (
        (ShopStatus.SUSPENDED, True, True, ErrorCode.SHOP_SUSPENDED),
        (ShopStatus.ACTIVE, False, True, ErrorCode.FORBIDDEN),
        (ShopStatus.ACTIVE, True, False, ErrorCode.FORBIDDEN),
    ),
)
def test_non_live_actor_or_shop_is_denied_without_writes(
    m2_test_database: Engine,
    shop_status: ShopStatus,
    actor_active: bool,
    staff_active: bool,
    expected: ErrorCode,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(
        m2_test_database,
        shop_status=shop_status,
        actor_active=actor_active,
        staff_active=staff_active,
    )
    factory = create_database_session_factory(m2_test_database)
    writes: list[str] = []

    def capture_writes(_conn, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(m2_test_database, "before_cursor_execute", capture_writes)
    try:
        with factory.begin() as session:
            actor = _context(actor_id, shop_id)
            candidate = discover_tenant_payment_target(
                session, actor=actor, debt_id=DebtId(debt_id)
            )
            result = lock_tenant_payment_predecessors(
                session, actor=actor, candidate=candidate
            )
        assert result.error is expected
        assert writes == []
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture_writes)


@pytest.mark.integration
def test_candidate_parent_is_rechecked_after_predecessor_locks(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as seed:
        actor, shop, _staff = _add_shop_actor(seed, role=ShopRole.CASHIER)
        _target_a, customer_a = _add_customer(seed)
        _relation_a, debt = _add_relation_and_debt(
            seed, actor=actor, shop=shop, customer=customer_a
        )
        _target_b, customer_b = _add_customer(seed)
        relation_b, _other_debt = _add_relation_and_debt(
            seed, actor=actor, shop=shop, customer=customer_b
        )
        actor_id, shop_id, debt_id, relation_b_id = (
            actor.id,
            shop.id,
            debt.id,
            relation_b.id,
        )

    session = factory()
    try:
        actor_context = _context(actor_id, shop_id)
        candidate = discover_tenant_payment_target(
            session, actor=actor_context, debt_id=DebtId(debt_id)
        )
        assert candidate is not None
        with factory.begin() as reparent:
            row = reparent.get(Debt, debt_id)
            assert row is not None
            row.shop_customer_id = relation_b_id
            row.updated_at = NOW + timedelta(hours=2)

        result = lock_tenant_payment_predecessors(
            session, actor=actor_context, candidate=candidate
        )
        assert result.error is ErrorCode.DEBT_UNAVAILABLE
    finally:
        session.rollback()
        session.close()


@pytest.mark.integration
def test_customer_lock_serializes_same_target_across_shops(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as seed:
        actor_a, shop_a, _staff_a = _add_shop_actor(seed, role=ShopRole.CASHIER)
        actor_b, shop_b, _staff_b = _add_shop_actor(seed, role=ShopRole.MANAGER)
        _target, customer = _add_customer(seed)
        _relation_a, debt_a = _add_relation_and_debt(
            seed, actor=actor_a, shop=shop_a, customer=customer
        )
        _relation_b, debt_b = _add_relation_and_debt(
            seed, actor=actor_b, shop=shop_b, customer=customer
        )
        ids = (actor_a.id, shop_a.id, debt_a.id, actor_b.id, shop_b.id, debt_b.id)

    actor_a_id, shop_a_id, debt_a_id, actor_b_id, shop_b_id, debt_b_id = ids
    first = factory()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        first.begin()
        first_actor = _context(actor_a_id, shop_a_id)
        first_candidate = discover_tenant_payment_target(
            first, actor=first_actor, debt_id=DebtId(debt_a_id)
        )
        first_result = lock_tenant_payment_predecessors(
            first, actor=first_actor, candidate=first_candidate
        )
        assert first_result.error is None

        customer_lock_attempted = Event()
        original_lock = targeting.lock_existing_own_customer_for_update

        def observed_customer_lock(session, *, actor_user_id):
            customer_lock_attempted.set()
            return original_lock(session, actor_user_id=actor_user_id)

        monkeypatch.setattr(
            targeting,
            "lock_existing_own_customer_for_update",
            observed_customer_lock,
        )

        def run_second():
            with factory.begin() as second:
                second_actor = _context(actor_b_id, shop_b_id)
                second_candidate = discover_tenant_payment_target(
                    second, actor=second_actor, debt_id=DebtId(debt_b_id)
                )
                return lock_tenant_payment_predecessors(
                    second, actor=second_actor, candidate=second_candidate
                )

        future = executor.submit(run_second)
        assert customer_lock_attempted.wait(timeout=5)
        with pytest.raises(FutureTimeoutError):
            future.result(timeout=0.25)
        first.commit()
        second_result = future.result(timeout=5)
        assert second_result.error is None
    finally:
        if first.in_transaction():
            first.rollback()
        first.close()
        executor.shutdown(wait=False, cancel_futures=True)
