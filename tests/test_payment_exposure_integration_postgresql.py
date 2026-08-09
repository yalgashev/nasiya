from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine

from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.debt.service import create_pending_debt_proposal
from app.debt.values import ShopCustomerId
from app.payment.commands import CreatePaymentV2RawForm, assemble_create_payment_request
from app.payment.models import Payment
from app.payment.repository import payment_open_set_reader_factory
from app.payment.service import record_debt_payment
from tests.test_debt_creation_gates_postgresql import (
    NOW,
    _add_complete_offer,
    _create_command,
    _seed_target,
)
from tests.test_payment_targeting_postgresql import _context

PAYMENT_TIME = datetime(2026, 8, 10, 12, tzinfo=UTC)


def _add_active_debt(
    session,
    *,
    shop_customer_id,
    actor_id,
    original: str,
    discounted: str,
) -> Debt:
    row = Debt(
        shop_customer_id=shop_customer_id,
        created_by_user_id=actor_id,
        original_amount_uzs=Decimal(original),
        discount_basis_points=0,
        discounted_amount_uzs=Decimal(discounted),
        due_date=date(2026, 8, 20),
        pending_expires_at=NOW + timedelta(hours=72),
        status="active",
        revision=2,
        accepted_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _payment_command(actor_id, shop_id, debt_id, *, amount: str):
    key = str(uuid4())
    actor = _context(actor_id, shop_id)
    assembled = assemble_create_payment_request(
        actor=actor,
        form=CreatePaymentV2RawForm(
            debt_id=str(debt_id),
            amount_uzs=amount,
            method="cash",
            idempotency_key=key,
            expected_revision="2",
            expected_balance_basis="discounted",
        ),
        header_idempotency_key=key,
    )
    assert assembled.command is not None
    return actor, assembled.command


@pytest.mark.integration
def test_m13_create_uses_original_minus_posted_payment_exposure(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000")
        _add_complete_offer(session, actor=seed.actor)
        debt = _add_active_debt(
            session,
            shop_customer_id=seed.shop_customer.id,
            actor_id=seed.actor.id,
            original="1000",
            discounted="800",
        )
        session.add(
            Payment(
                id=uuid4(),
                debt_id=debt.id,
                recorded_by_user_id=seed.actor.id,
                amount_uzs=Decimal("200"),
                method="cash",
                debt_revision_after=2,
                created_at=PAYMENT_TIME,
            )
        )
        session.flush()

        denied = create_pending_debt_proposal(
            session,
            authority=seed.authority,
            shop_customer_id=ShopCustomerId(seed.shop_customer.id),
            command=_create_command(amount="300"),
            open_set_reader_factory=payment_open_set_reader_factory,
        )

    # Exposure is original 1000 - posted 200 = 800, not discounted 800 - 200.
    assert denied.error is ErrorCode.CREDIT_LIMIT_EXCEEDED


@pytest.mark.integration
def test_partial_payment_frees_original_exposure_for_next_debt(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000", max_open_debts=3)
        _add_complete_offer(session, actor=seed.actor)
        debt = _add_active_debt(
            session,
            shop_customer_id=seed.shop_customer.id,
            actor_id=seed.actor.id,
            original="1000",
            discounted="1000",
        )
        actor_id, shop_id, relation_id, debt_id = (
            seed.actor.id,
            seed.shop.id,
            seed.shop_customer.id,
            debt.id,
        )
    actor, command = _payment_command(actor_id, shop_id, debt_id, amount="400")
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: PAYMENT_TIME,
        )
    with factory.begin() as session:
        created = create_pending_debt_proposal(
            session,
            authority=_seed_authority(actor_id, shop_id),
            shop_customer_id=ShopCustomerId(relation_id),
            command=_create_command(amount="400"),
            open_set_reader_factory=payment_open_set_reader_factory,
        )

    assert created.error is None and created.debt_id is not None


@pytest.mark.integration
def test_paid_debt_is_excluded_from_exposure_and_open_count(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000", max_open_debts=1)
        _add_complete_offer(session, actor=seed.actor)
        debt = _add_active_debt(
            session,
            shop_customer_id=seed.shop_customer.id,
            actor_id=seed.actor.id,
            original="1000",
            discounted="1000",
        )
        actor_id, shop_id, relation_id, debt_id = (
            seed.actor.id,
            seed.shop.id,
            seed.shop_customer.id,
            debt.id,
        )

    actor, command = _payment_command(actor_id, shop_id, debt_id, amount="1000")
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: PAYMENT_TIME,
        )
    with factory.begin() as session:
        seed_authority = _seed_authority(actor_id, shop_id)
        created = create_pending_debt_proposal(
            session,
            authority=seed_authority,
            shop_customer_id=ShopCustomerId(relation_id),
            command=_create_command(amount="1000"),
            open_set_reader_factory=payment_open_set_reader_factory,
        )

    assert created.error is None and created.debt_id is not None


@pytest.mark.integration
def test_concurrent_payment_and_new_debt_serialize_to_a_lawful_before_or_after_state(
    m2_test_database: Engine,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed_target(session, credit_limit="1000", max_open_debts=3)
        _add_complete_offer(session, actor=seed.actor)
        debt = _add_active_debt(
            session,
            shop_customer_id=seed.shop_customer.id,
            actor_id=seed.actor.id,
            original="1000",
            discounted="1000",
        )
        actor_id, shop_id, relation_id, debt_id = (
            seed.actor.id,
            seed.shop.id,
            seed.shop_customer.id,
            debt.id,
        )
    actor, payment_command = _payment_command(actor_id, shop_id, debt_id, amount="400")
    authority = _seed_authority(actor_id, shop_id)
    debt_command = _create_command(amount="400")
    start = Barrier(2)

    def pay():
        start.wait()
        with factory.begin() as session:
            return record_debt_payment(
                session,
                actor=actor,
                command=payment_command,
                payment_clock=lambda: PAYMENT_TIME,
            )

    def create():
        start.wait()
        with factory.begin() as session:
            return create_pending_debt_proposal(
                session,
                authority=authority,
                shop_customer_id=ShopCustomerId(relation_id),
                command=debt_command,
                open_set_reader_factory=payment_open_set_reader_factory,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        payment_result = pool.submit(pay)
        debt_result = pool.submit(create)
        assert payment_result.result().payment_id is not None
        created = debt_result.result()

    assert created.error in {None, ErrorCode.CREDIT_LIMIT_EXCEEDED}


def _seed_authority(actor_id, shop_id):
    from app.auth.deps import CurrentSessionStatus
    from app.debt.dependencies import DebtRequestContext, DetachedDebtActorAuthority

    return DetachedDebtActorAuthority(
        status=CurrentSessionStatus.AUTHENTICATED,
        actor_user_id=actor_id,
        current_shop_id=shop_id,
        request_context=DebtRequestContext(is_htmx=False),
    )
