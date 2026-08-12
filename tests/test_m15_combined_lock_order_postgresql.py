from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

import app.auth.repository as auth_repository
import app.debt.overdue_service as overdue_service
import app.debt.overdue_targeting as overdue_targeting
import app.debt.targeting as debt_targeting
import app.payment.service as payment_service
import app.payment.targeting as payment_targeting
import app.shop_customer.service as shop_customer_service
from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.deps import CurrentSessionStatus
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer_activation.service import _issue_registration_otp
from app.db import create_database_session_factory
from app.debt.customer_accept_service import accept_own_customer_debt
from app.debt.customer_decision_targeting import lock_customer_debt_predecessors
from app.debt.dependencies import DebtRequestContext, DetachedDebtActorAuthority
from app.debt.enums import DebtStatus
from app.debt.expiry_targeting import lock_debt_for_expiry
from app.debt.models import Debt
from app.debt.service import create_pending_debt_proposal
from app.debt.tenant_cancel_targeting import lock_tenant_debt_for_cancel
from app.debt.values import ShopCustomerId
from app.idempotency.models import IdempotencyKey
from app.payment.repository import SqlAlchemyLockedDebtPostedTotalReader
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop_customer.contracts import (
    ShopCustomerPolicyUpdateOutcome,
    ShopDefaultPolicyUpdateOutcome,
)
from app.shop_customer.models import ShopCustomer
from app.telegram.service import unlink
from tests.rating_support import materialize_overdue_debts
from tests.test_debt_creation_gates_postgresql import (
    NOW as M13_NOW,
)
from tests.test_debt_creation_gates_postgresql import (
    _add_complete_offer,
    _create_command,
    _seed_target,
)
from tests.test_shop_customer_default_service_postgresql import (
    _command as default_command,
)
from tests.test_shop_customer_default_service_postgresql import (
    _seed as seed_default,
)
from tests.test_shop_customer_policy_service_postgresql import (
    _command as policy_command,
)
from tests.test_shop_customer_policy_service_postgresql import (
    _policy,
)
from tests.test_shop_customer_policy_service_postgresql import (
    _seed as seed_policy,
)

pytestmark = pytest.mark.integration

BATCH_NOW = datetime(2026, 8, 9, 19, tzinfo=UTC)


def _ordered(source: str, *needles: str) -> bool:
    positions = tuple(source.index(needle) for needle in needles)
    return positions == tuple(sorted(positions))


def test_inherited_graph_keeps_every_relevant_path_forward_ordered() -> None:
    batch_target = inspect.getsource(
        overdue_targeting.resolve_and_lock_overdue_candidate
    )
    assert _ordered(
        batch_target,
        "lock_shop_for_update",
        "lock_customer_hard_block_scope",
        "select(ShopCustomer)",
        "select(Debt)",
    )
    assert ".order_by(Debt.due_date, Debt.id)" in inspect.getsource(
        overdue_targeting.discover_overdue_candidates
    )

    creation_target = inspect.getsource(debt_targeting.lock_debt_target_before_offer)
    assert _ordered(
        creation_target,
        "lock_shop_for_update",
        "lock_actor_shop_staff_for_update",
        "lock_actor_and_target_users_for_update",
        "get_telegram_link_by_user_for_update",
        "lock_active_customer_for_target_user",
    )
    same_class_users = inspect.getsource(
        auth_repository.lock_actor_and_target_users_for_update
    )
    assert "tuple(sorted({actor_user_id, target_user_id}))" in same_class_users
    assert ".order_by(User.id.asc())" in same_class_users

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
    payment = inspect.getsource(payment_service.record_debt_payment)
    assert _ordered(
        payment,
        "lock_tenant_payment_predecessors",
        "insert_or_resolve_key",
        "lock_tenant_payment_debt",
        "update_locked_debt",
        "insert_payment",
        "append_pending_on_time_paid",
    )

    assert "lock_shop_for_update" in inspect.getsource(lock_customer_debt_predecessors)
    assert "lock_shop_for_update" in inspect.getsource(lock_tenant_debt_for_cancel)
    assert _ordered(
        inspect.getsource(lock_debt_for_expiry),
        "lock_shop_for_update",
        "lock_shop_customer_by_tenant_locator",
        ".with_for_update()",
    )
    for operation in (
        shop_customer_service.update_shop_default_credit_policy,
        shop_customer_service.update_shop_customer_policy,
    ):
        assert _ordered(
            inspect.getsource(operation),
            "lock_shop_for_update",
            "lock_actor_shop_staff_for_update",
        )

    activation = inspect.getsource(_issue_registration_otp)
    assert _ordered(
        activation,
        "lock_outstanding_challenge_set_by_user",
        "with_for_update=True",
        "get_telegram_link_by_user_for_update",
        "lock_existing_own_customer_for_update",
        "select_current_registration_acceptance",
    )
    customer_unlink = inspect.getsource(unlink)
    assert _ordered(
        customer_unlink,
        "lock_outstanding_telegram_link_token_set_by_user",
        "_lock_link_change_otp_state",
        "_lock_active_user",
        "get_telegram_link_by_user_for_update",
        "lock_existing_own_customer_for_update",
    )

    combined = "\n".join(
        inspect.getsource(operation)
        for operation in (
            overdue_service.materialize_overdue_debts,
            overdue_targeting.resolve_and_lock_overdue_candidate,
            create_pending_debt_proposal,
            accept_own_customer_debt,
            lock_tenant_debt_for_cancel,
            lock_debt_for_expiry,
            payment_service.record_debt_payment,
            shop_customer_service.update_shop_default_credit_policy,
            shop_customer_service.update_shop_customer_policy,
            _issue_registration_otp,
            unlink,
        )
    ).casefold()
    for forbidden in (
        "sleep(",
        "retry",
        "timeout",
        "nowait",
        "skip_locked",
        "lock_timeout",
        "pg_advisory",
    ):
        assert forbidden not in combined


def _add_overdue_candidate(
    session,
    *,
    shop_customer_id,
    actor_user_id,
    amount: str = "100",
) -> Debt:
    created_at = datetime(2026, 8, 1, tzinfo=UTC)
    accepted_at = created_at + timedelta(days=3)
    debt = Debt(
        shop_customer_id=shop_customer_id,
        created_by_user_id=actor_user_id,
        original_amount_uzs=Decimal(amount),
        discount_basis_points=0,
        discounted_amount_uzs=Decimal(amount),
        due_date=date(2026, 8, 8),
        pending_expires_at=created_at + timedelta(hours=72),
        status=DebtStatus.ACTIVE.value,
        revision=2,
        accepted_at=accepted_at,
        created_at=created_at,
        updated_at=accepted_at,
    )
    session.add(debt)
    session.flush()
    return debt


def test_batch_audit_vs_cross_shop_create_finishes_in_complete_blocked_state(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000", max_open_debts=3)
        _add_complete_offer(session, actor=seed.actor)
        debt = _add_overdue_candidate(
            session,
            shop_customer_id=seed.shop_customer.id,
            actor_user_id=seed.actor.id,
        )

        other_actor = User(phone=f"+998{seed.shop.id.int % 1_000_000_000:09d}")
        other_shop = Shop(
            name="Cross-shop M15 barrier",
            phone=f"+998{debt.id.int % 1_000_000_000:09d}",
            status=ShopStatus.ACTIVE.value,
            created_at=M13_NOW,
            updated_at=M13_NOW,
        )
        session.add_all((other_actor, other_shop))
        session.flush()
        session.add(
            ShopStaff(
                shop_id=other_shop.id,
                user_id=other_actor.id,
                role=ShopRole.OWNER.value,
                is_active=True,
                created_at=M13_NOW,
                updated_at=M13_NOW,
            )
        )
        other_relation = ShopCustomer(
            shop_id=other_shop.id,
            customer_id=seed.customer.id,
            credit_limit_uzs=Decimal("1000"),
            max_open_debts=3,
            list_status="normal",
            revision=1,
            created_by_user_id=other_actor.id,
            created_at=M13_NOW,
            updated_at=M13_NOW,
        )
        session.add(other_relation)
        session.flush()
        authority = DetachedDebtActorAuthority(
            status=CurrentSessionStatus.AUTHENTICATED,
            actor_user_id=other_actor.id,
            current_shop_id=other_shop.id,
            request_context=DebtRequestContext(is_htmx=False),
        )
        relation_id = other_relation.id
        debt_id = debt.id

    batch_holds_customer = Event()
    release_batch = Event()
    create_attempted_customer = Event()
    original_append = overdue_service.append_audit_event
    original_customer_lock = debt_targeting.lock_active_customer_for_target_user
    appended = 0

    def pause_after_first_audit(*args, **kwargs):
        nonlocal appended
        result = original_append(*args, **kwargs)
        appended += 1
        if appended == 1:
            batch_holds_customer.set()
            release_batch.wait()
        return result

    def observe_customer_lock(*args, **kwargs):
        create_attempted_customer.set()
        return original_customer_lock(*args, **kwargs)

    monkeypatch.setattr(overdue_service, "append_audit_event", pause_after_first_audit)
    monkeypatch.setattr(
        debt_targeting,
        "lock_active_customer_for_target_user",
        observe_customer_lock,
    )

    def run_batch():
        return materialize_overdue_debts(
            factory,
            now=BATCH_NOW,
            batch_size=1,
            posted_total_reader_factory=SqlAlchemyLockedDebtPostedTotalReader,
        )

    def run_create():
        with factory.begin() as session:
            return create_pending_debt_proposal(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(relation_id),
                command=_create_command(),
                hard_block_clock=lambda: BATCH_NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        batch_future = pool.submit(run_batch)
        batch_holds_customer.wait()
        create_future = pool.submit(run_create)
        create_attempted_customer.wait()
        assert not create_future.done()
        release_batch.set()
        batch_result = batch_future.result()
        create_result = create_future.result()

    assert batch_result.transitioned_count == 1
    assert create_result.error is ErrorCode.CUSTOMER_RATING_BLOCKED
    with factory() as session:
        assert session.get_one(Debt, debt_id).status == DebtStatus.OVERDUE.value
        assert session.scalar(select(func.count()).select_from(Debt)) == 1
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2


@pytest.mark.parametrize("mutation", ("policy", "default"))
def test_batch_vs_m12_policy_mutations_serialize_at_shop_predecessor(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    seeded = (
        seed_policy(m2_test_database)
        if mutation == "policy"
        else seed_default(m2_test_database)
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        relation = session.get_one(ShopCustomer, seeded["row"])
        debt = _add_overdue_candidate(
            session,
            shop_customer_id=relation.id,
            actor_user_id=seeded["authority"].actor_user_id,
        )
        debt_id = debt.id

    batch_holds_shop = Event()
    release_batch = Event()
    mutation_attempted_shop = Event()
    original_append = overdue_service.append_audit_event
    original_shop_lock = shop_customer_service.lock_shop_for_update
    appended = 0

    def pause_after_first_audit(*args, **kwargs):
        nonlocal appended
        result = original_append(*args, **kwargs)
        appended += 1
        if appended == 1:
            batch_holds_shop.set()
            release_batch.wait()
        return result

    def observe_shop_lock(*args, **kwargs):
        mutation_attempted_shop.set()
        return original_shop_lock(*args, **kwargs)

    monkeypatch.setattr(overdue_service, "append_audit_event", pause_after_first_audit)
    monkeypatch.setattr(
        shop_customer_service, "lock_shop_for_update", observe_shop_lock
    )

    def run_batch():
        return materialize_overdue_debts(
            factory,
            now=BATCH_NOW,
            batch_size=1,
            posted_total_reader_factory=SqlAlchemyLockedDebtPostedTotalReader,
        )

    def run_mutation():
        with factory.begin() as session:
            if mutation == "policy":
                return shop_customer_service.update_shop_customer_policy(
                    session,
                    authority=seeded["authority"],
                    command=policy_command(
                        seeded["row"], policy=_policy(credit="2000000", debts=3)
                    ),
                    now=BATCH_NOW + timedelta(minutes=1),
                    audit_writer=SqlAlchemyAuditWriter(session),
                )
            return shop_customer_service.update_shop_default_credit_policy(
                session,
                authority=seeded["authority"],
                command=default_command(
                    expected_updated_at=seeded["updated_at"],
                    credit="4000000",
                    debts=4,
                ),
                now=BATCH_NOW + timedelta(minutes=1),
                audit_writer=SqlAlchemyAuditWriter(session),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        batch_future = pool.submit(run_batch)
        batch_holds_shop.wait()
        mutation_future = pool.submit(run_mutation)
        mutation_attempted_shop.wait()
        assert not mutation_future.done()
        release_batch.set()
        batch_result = batch_future.result()
        mutation_result = mutation_future.result()

    assert batch_result.transitioned_count == 1
    if mutation == "policy":
        assert mutation_result.outcome is ShopCustomerPolicyUpdateOutcome.CHANGED
    else:
        assert mutation_result.outcome is ShopDefaultPolicyUpdateOutcome.CHANGED
    with factory() as session:
        assert session.get_one(Debt, debt_id).status == DebtStatus.OVERDUE.value
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 3
