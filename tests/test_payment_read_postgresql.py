from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine

from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.customer_authority import CustomerDebtAuthority
from app.debt.enums import DebtBalanceBasis, DebtStatus
from app.debt.models import Debt
from app.debt.presentation import DebtWebLanguage
from app.debt.values import DebtId, DiscountedAmountUZS, OriginalAmountUZS
from app.idempotency.contracts import IdempotencyOutcome
from app.idempotency.models import IdempotencyKey
from app.payment.commands import CreatePaymentRawForm, assemble_create_payment_command
from app.payment.dependencies import DetachedPaymentReadActorContext
from app.payment.models import Payment
from app.payment.read_service import (
    get_own_customer_payment_receipt,
    get_tenant_payment_receipt,
    get_tenant_payment_receipt_for_result,
    list_customer_debts_with_payment_progress,
    list_own_customer_payment_history,
    list_payment_progress_for_debts,
    list_tenant_customer_debts_with_payment_progress,
    list_tenant_payment_history,
)
from app.payment.service import PaymentMutationRejected, record_debt_payment
from app.payment.values import (
    PaymentId,
    PostedPaymentTotalUZS,
    calculate_payment_exposure,
    open_debt_count_contribution,
)
from app.shop.enums import ShopRole, ShopStatus
from app.shop.models import Shop, ShopStaff
from app.shop.values import ShopId
from app.shop_customer.values import ShopCustomerId
from tests.test_payment_targeting_postgresql import (
    NOW,
    _add_customer,
    _add_relation_and_debt,
    _add_shop_actor,
    _context,
)

PAYMENT_TIME = datetime(2026, 8, 10, 12, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ReadSeed:
    actor_id: UUID
    shop_id: UUID
    staff_id: UUID
    customer_user_id: UUID
    customer_id: UUID
    relation_id: UUID
    debt_id: UUID


def _seed_read_graph(
    engine: Engine,
    *,
    discounted: str = "900",
    due_date: date = date(2026, 8, 20),
    shop_status: ShopStatus = ShopStatus.ACTIVE,
) -> ReadSeed:
    factory = create_database_session_factory(engine)
    with factory.begin() as session:
        actor, shop, staff = _add_shop_actor(
            session,
            role=ShopRole.CASHIER,
            shop_status=shop_status,
        )
        customer_user, customer = _add_customer(
            session,
            target_active=True,
            customer_active=True,
        )
        relation, debt = _add_relation_and_debt(
            session,
            actor=actor,
            shop=shop,
            customer=customer,
            list_status="normal",
        )
        debt.discounted_amount_uzs = Decimal(discounted)
        debt.discount_basis_points = 1000 if discounted == "900" else 0
        debt.due_date = due_date
        debt.updated_at = NOW + timedelta(hours=2)
        return ReadSeed(
            actor_id=actor.id,
            shop_id=shop.id,
            staff_id=staff.id,
            customer_user_id=customer_user.id,
            customer_id=customer.id,
            relation_id=relation.id,
            debt_id=debt.id,
        )


def _add_second_shop_for_customer(
    engine: Engine,
    *,
    customer_id: UUID,
    discounted: str = "900",
) -> ReadSeed:
    factory = create_database_session_factory(engine)
    with factory.begin() as session:
        customer = session.get(Customer, customer_id)
        assert customer is not None
        customer_user_id = customer.user_id
        actor, shop, staff = _add_shop_actor(session, role=ShopRole.OWNER)
        relation, debt = _add_relation_and_debt(
            session,
            actor=actor,
            shop=shop,
            customer=customer,
            list_status="normal",
        )
        debt.discounted_amount_uzs = Decimal(discounted)
        debt.discount_basis_points = 1000 if discounted == "900" else 0
        debt.updated_at = NOW + timedelta(hours=2)
        return ReadSeed(
            actor_id=actor.id,
            shop_id=shop.id,
            staff_id=staff.id,
            customer_user_id=customer_user_id,
            customer_id=customer.id,
            relation_id=relation.id,
            debt_id=debt.id,
        )


def _read_actor(seed: ReadSeed, *, actor_id: UUID | None = None):
    return DetachedPaymentReadActorContext(
        actor_user_id=actor_id or seed.actor_id,
        current_shop_id=seed.shop_id,
        role_hint=ShopRole.CASHIER,
        language=DebtWebLanguage.UZ_LATN,
    )


def _payment_command(
    seed: ReadSeed,
    *,
    amount: str,
    revision: int,
    key: UUID,
    method: str = "card",
):
    actor = _context(seed.actor_id, seed.shop_id)
    assembled = assemble_create_payment_command(
        actor=actor,
        form=CreatePaymentRawForm(
            debt_id=str(seed.debt_id),
            amount_uzs=amount,
            method=method,
            idempotency_key=str(key),
            expected_revision=str(revision),
        ),
        header_idempotency_key=str(key),
    )
    assert assembled.error is None and assembled.command is not None
    return actor, assembled.command


def _record(
    engine: Engine,
    seed: ReadSeed,
    *,
    amount: str,
    revision: int,
    key: UUID,
    method: str = "card",
):
    factory = create_database_session_factory(engine)
    actor, command = _payment_command(
        seed,
        amount=amount,
        revision=revision,
        key=key,
        method=method,
    )
    with factory.begin() as session:
        return record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: PAYMENT_TIME,
        )


def _ledger_counts(factory) -> tuple[int, int, int, int]:
    with factory() as session:
        return (
            int(session.scalar(select(func.count()).select_from(Payment))),
            int(session.scalar(select(func.count()).select_from(IdempotencyKey))),
            int(session.scalar(select(func.count()).select_from(AuditLog))),
            int(session.scalar(select(func.max(Debt.revision))) or 0),
        )


@pytest.mark.integration
def test_effective_overdue_progress_is_scoped_batched_and_read_only(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(
        m2_test_database,
        discounted="900",
        due_date=date(2026, 8, 9),
    )
    other_shop = _add_second_shop_for_customer(
        m2_test_database,
        customer_id=seed.customer_id,
    )
    factory = create_database_session_factory(m2_test_database)
    with factory() as session:
        before = session.get_one(Debt, seed.debt_id)
        before_state = (
            before.status,
            before.revision,
            before.overdue_at,
            before.overdue_revision,
            int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.object_id == seed.debt_id)
                )
                or 0
            ),
        )

    with factory() as session:
        foreign_tenant_projection = list_tenant_customer_debts_with_payment_progress(
            session,
            shop_id=ShopId(seed.shop_id),
            shop_customer_id=ShopCustomerId(other_shop.relation_id),
            server_now=PAYMENT_TIME,
        )
    assert foreign_tenant_projection == ()

    writes: list[str] = []
    selects: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        upper = statement.lstrip().upper()
        if upper.startswith("SELECT"):
            selects.append(statement)
        elif upper.startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(m2_test_database, "before_cursor_execute", capture)
    try:
        with factory() as session:
            tenant_projection = list_tenant_customer_debts_with_payment_progress(
                session,
                shop_id=ShopId(seed.shop_id),
                shop_customer_id=ShopCustomerId(seed.relation_id),
                server_now=PAYMENT_TIME,
            )
            authority = CustomerDebtAuthority(
                user_id=seed.customer_user_id,
                customer_id=seed.customer_id,
            )
            customer_projection = list_customer_debts_with_payment_progress(
                session,
                authority=authority,
                server_now=PAYMENT_TIME,
            )
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture)

    assert len(tenant_projection) == 1
    tenant_progress = tenant_projection[0].payment_progress
    assert tenant_progress is not None
    assert tenant_projection[0].status is DebtStatus.ACTIVE
    assert tenant_progress.is_effectively_overdue is True
    assert tenant_progress.balance_basis is DebtBalanceBasis.ORIGINAL
    assert tenant_progress.remaining_due_uzs == Decimal("1000")

    customer_progress = next(
        item.payment_progress
        for item in customer_projection
        if item.payment_progress is not None
        and item.payment_progress.is_effectively_overdue
    )
    assert customer_progress.balance_basis is DebtBalanceBasis.ORIGINAL
    assert customer_progress.remaining_due_uzs == Decimal("1000")
    assert writes == []
    # Tenant is one debt list plus one grouped Payment sum plus its existing
    # presentation list; customer is the same two debt/shop batches around one
    # grouped Payment sum. Both remain constant as the own-customer set grows.
    assert len(selects) == 8

    with factory() as session:
        after = session.get_one(Debt, seed.debt_id)
        after_state = (
            after.status,
            after.revision,
            after.overdue_at,
            after.overdue_revision,
            int(
                session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.object_id == seed.debt_id)
                )
                or 0
            ),
        )
    assert after_state == before_state


@pytest.mark.integration
def test_new_replay_and_paid_receipts_share_locator_with_stable_history(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    read_actor = _read_actor(seed)
    key_one, key_two, key_final = uuid4(), uuid4(), uuid4()
    actor, command_one = _payment_command(seed, amount="300", revision=2, key=key_one)

    writer = factory()
    try:
        writer.begin()
        created = record_debt_payment(
            writer,
            actor=actor,
            command=command_one,
            payment_clock=lambda: PAYMENT_TIME,
        )
        inside = get_tenant_payment_receipt_for_result(
            writer,
            actor=read_actor,
            result=created,
        )
        assert inside.error is None and inside.receipt is not None
        assert inside.receipt.historical_balance_after.value == Decimal("600")
        assert inside.receipt.current_balance.value == Decimal("600")

        with factory() as observer:
            invisible = get_tenant_payment_receipt_for_result(
                observer,
                actor=read_actor,
                result=created,
            )
        assert invisible.error is ErrorCode.PAYMENT_UNAVAILABLE
        writer.commit()
    finally:
        writer.close()

    with factory.begin() as session:
        replay_immediate = record_debt_payment(
            session,
            actor=actor,
            command=command_one,
            payment_clock=lambda: pytest.fail("replay must not capture time"),
        )
        immediate_receipt = get_tenant_payment_receipt_for_result(
            session,
            actor=read_actor,
            result=replay_immediate,
        )
    assert replay_immediate.outcome is IdempotencyOutcome.REPLAY
    assert replay_immediate.payment_id == created.payment_id
    assert immediate_receipt.receipt == inside.receipt
    assert _ledger_counts(factory) == (1, 1, 1, 3)

    second = _record(
        m2_test_database,
        seed,
        amount="200",
        revision=3,
        key=key_two,
        method="transfer",
    )
    with factory.begin() as session:
        replay_old = record_debt_payment(
            session,
            actor=actor,
            command=command_one,
            payment_clock=lambda: pytest.fail("old replay must not capture time"),
        )
        old_after_later = get_tenant_payment_receipt_for_result(
            session, actor=read_actor, result=replay_old
        )
    assert replay_old.payment_id == created.payment_id
    assert old_after_later.receipt is not None
    assert old_after_later.receipt.historical_balance_after.value == Decimal("600")
    assert old_after_later.receipt.current_balance.value == Decimal("400")
    assert old_after_later.receipt.current_debt_status is DebtStatus.ACTIVE

    final = _record(
        m2_test_database,
        seed,
        amount="400",
        revision=4,
        key=key_final,
        method="cash",
    )
    before_paid_replays = _ledger_counts(factory)
    assert before_paid_replays == (3, 3, 4, 5)
    for result, key, amount, revision, method, historical in (
        (created, key_one, "300", 2, "card", "600"),
        (second, key_two, "200", 3, "transfer", "400"),
        (final, key_final, "400", 4, "cash", "0"),
    ):
        replay_actor, replay_command = _payment_command(
            seed,
            amount=amount,
            revision=revision,
            key=key,
            method=method,
        )
        with factory.begin() as session:
            replay = record_debt_payment(
                session,
                actor=replay_actor,
                command=replay_command,
                payment_clock=lambda: pytest.fail("paid replay captured time"),
            )
            receipt_result = get_tenant_payment_receipt_for_result(
                session, actor=read_actor, result=replay
            )
        assert replay.payment_id == result.payment_id
        assert receipt_result.receipt is not None
        assert receipt_result.receipt.amount.value == Decimal(amount)
        assert receipt_result.receipt.method.value == method
        assert receipt_result.receipt.created_at == PAYMENT_TIME
        assert receipt_result.receipt.historical_balance_after.value == Decimal(
            historical
        )
        assert receipt_result.receipt.current_balance.value == Decimal("0")
        assert receipt_result.receipt.current_debt_status is DebtStatus.PAID
    assert _ledger_counts(factory) == before_paid_replays

    denied_actor, denied_command = _payment_command(
        seed, amount="1", revision=5, key=uuid4()
    )
    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=denied_actor,
                command=denied_command,
                payment_clock=lambda: PAYMENT_TIME,
            )
    assert captured.value.error is ErrorCode.DEBT_NOT_PAYABLE
    assert _ledger_counts(factory) == before_paid_replays


@pytest.mark.integration
def test_tenant_customer_idor_and_privacy_matrix_is_generic_and_read_only(
    m2_test_database: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    own = _seed_read_graph(m2_test_database)
    foreign = _seed_read_graph(m2_test_database)
    own_key, foreign_key = uuid4(), uuid4()
    own_result = _record(m2_test_database, own, amount="100", revision=2, key=own_key)
    foreign_result = _record(
        m2_test_database, foreign, amount="100", revision=2, key=foreign_key
    )
    factory = create_database_session_factory(m2_test_database)

    with factory.begin() as session:
        session.add(
            ShopStaff(
                shop_id=own.shop_id,
                user_id=own.customer_user_id,
                role=ShopRole.CASHIER.value,
                is_active=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        orphan_user = User(
            phone=f"+998{uuid4().int % 1_000_000_000:09d}",
            is_active=True,
        )
        session.add(orphan_user)
        session.flush()
        orphan_user_id = orphan_user.id

    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    event.listen(m2_test_database, "before_cursor_execute", capture)
    try:

        def tenant_receipt_select_count(payment_id: PaymentId) -> int:
            statements.clear()
            with factory() as count_session:
                get_tenant_payment_receipt(
                    count_session,
                    actor=_read_actor(own),
                    payment_id=payment_id,
                )
            return sum(
                statement.lstrip().upper().startswith("SELECT")
                for statement in statements
            )

        assert tenant_receipt_select_count(foreign_result.payment_id) == (
            tenant_receipt_select_count(PaymentId(uuid4()))
        )

        def customer_receipt_select_count(payment_id: PaymentId) -> int:
            statements.clear()
            with factory() as count_session:
                customer_user = count_session.get(User, own.customer_user_id)
                assert customer_user is not None
                get_own_customer_payment_receipt(
                    count_session,
                    authenticated_user=customer_user,
                    payment_id=payment_id,
                )
            return sum(
                statement.lstrip().upper().startswith("SELECT")
                for statement in statements
            )

        assert customer_receipt_select_count(foreign_result.payment_id) == (
            customer_receipt_select_count(PaymentId(uuid4()))
        )

        with factory() as session:
            own_actor = _read_actor(own)
            cross_debt = list_tenant_payment_history(
                session, actor=own_actor, debt_id=DebtId(foreign.debt_id)
            )
            missing_debt = list_tenant_payment_history(
                session, actor=own_actor, debt_id=DebtId(uuid4())
            )
            cross_receipt = get_tenant_payment_receipt(
                session,
                actor=own_actor,
                payment_id=foreign_result.payment_id,
            )
            missing_receipt = get_tenant_payment_receipt(
                session,
                actor=own_actor,
                payment_id=PaymentId(uuid4()),
            )
            customer_user = session.get(User, own.customer_user_id)
            assert customer_user is not None
            other_customer = get_own_customer_payment_receipt(
                session,
                authenticated_user=customer_user,
                payment_id=foreign_result.payment_id,
            )
            guessed_customer = get_own_customer_payment_receipt(
                session,
                authenticated_user=customer_user,
                payment_id=PaymentId(uuid4()),
            )
            orphan = session.get(User, orphan_user_id)
            assert orphan is not None
            missing_customer_chain = get_own_customer_payment_receipt(
                session,
                authenticated_user=orphan,
                payment_id=own_result.payment_id,
            )
            foreign_history = list_own_customer_payment_history(
                session,
                authenticated_user=customer_user,
                debt_id=DebtId(foreign.debt_id),
            )
            missing_history = list_own_customer_payment_history(
                session,
                authenticated_user=customer_user,
                debt_id=DebtId(uuid4()),
            )

            dual_actor = _read_actor(own, actor_id=own.customer_user_id)
            dual_tenant = get_tenant_payment_receipt(
                session,
                actor=dual_actor,
                payment_id=own_result.payment_id,
            )
            dual_customer = get_own_customer_payment_receipt(
                session,
                authenticated_user=customer_user,
                payment_id=own_result.payment_id,
            )
            wrong_tenant_actor = DetachedPaymentReadActorContext(
                actor_user_id=own.actor_id,
                current_shop_id=foreign.shop_id,
                role_hint=ShopRole.CASHIER,
                language=DebtWebLanguage.UZ_LATN,
            )
            wrong_tenant = get_tenant_payment_receipt(
                session,
                actor=wrong_tenant_actor,
                payment_id=foreign_result.payment_id,
            )

        assert cross_debt.error is missing_debt.error is ErrorCode.DEBT_UNAVAILABLE
        assert (
            cross_receipt.error
            is missing_receipt.error
            is ErrorCode.PAYMENT_UNAVAILABLE
        )
        assert (
            other_customer.error
            is guessed_customer.error
            is missing_customer_chain.error
            is ErrorCode.PAYMENT_UNAVAILABLE
        )
        assert foreign_history.error is missing_history.error is None
        assert foreign_history.history == missing_history.history == ()
        assert dual_tenant.error is dual_customer.error is None
        assert wrong_tenant.error is ErrorCode.FORBIDDEN

        with factory.begin() as session:
            staff = session.get(ShopStaff, own.staff_id)
            assert staff is not None
            staff.is_active = False
            staff.revoked_at = PAYMENT_TIME
            staff.updated_at = PAYMENT_TIME
        statements.clear()
        with factory() as session:
            revoked_own = get_tenant_payment_receipt(
                session,
                actor=_read_actor(own),
                payment_id=own_result.payment_id,
            )
            revoked_foreign = get_tenant_payment_receipt(
                session,
                actor=_read_actor(own),
                payment_id=foreign_result.payment_id,
            )
        assert revoked_own.error is revoked_foreign.error is ErrorCode.FORBIDDEN
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture)

    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for statement in statements
    )
    with factory() as session:
        payment = session.get(Payment, own_result.payment_id.as_uuid())
        actor_user = session.get(User, own.actor_id)
        customer_user = session.get(User, own.customer_user_id)
        shop = session.get(Shop, own.shop_id)
        key_digest = session.scalar(
            select(IdempotencyKey.key_digest).where(
                IdempotencyKey.result_object_id == own_result.payment_id.as_uuid()
            )
        )
        audits = tuple(
            session.scalars(
                select(AuditLog).where(
                    AuditLog.event_type.in_(("payment.recorded", "debt.paid"))
                )
            )
        )
        assert payment is not None
        assert actor_user is not None and customer_user is not None
        assert shop is not None and isinstance(key_digest, str)
        safe_objects = (
            repr(payment),
            repr(own_result),
            repr(dual_tenant),
            repr(dual_customer),
            *(repr(item) for item in dual_customer.history),
            *(repr(audit) for audit in audits),
            *(repr(audit.payload) for audit in audits),
            caplog.text,
        )
        forbidden_canaries = (
            str(own.actor_id),
            str(own.shop_id),
            str(own.customer_id),
            str(own_result.payment_id.as_uuid()),
            str(foreign_result.payment_id.as_uuid()),
            str(own_key),
            str(foreign_key),
            key_digest,
            actor_user.phone,
            customer_user.phone,
            shop.phone,
        )
        for rendered in safe_objects:
            assert not any(canary in rendered for canary in forbidden_canaries)
        receipt = dual_customer.receipt
        assert receipt is not None
        receipt_fields = {field.name for field in fields(receipt)}
        assert not receipt_fields.intersection(
            {
                "recorded_by_user_id",
                "customer_id",
                "payment_id",
                "idempotency_key",
                "key_digest",
                "card_number",
                "bank_reference",
                "terminal_id",
            }
        )
        for audit in audits:
            assert not set(audit.payload).intersection(
                {
                    "actor_user_id",
                    "customer_id",
                    "payment_id",
                    "idempotency_key",
                    "key_digest",
                    "card_number",
                    "bank_reference",
                    "terminal_id",
                }
            )


@pytest.mark.integration
def test_balance_history_receipt_sequence_and_query_count_matrix(
    m2_test_database: Engine,
) -> None:
    seed = _seed_read_graph(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    read_actor = _read_actor(seed)
    keys = (uuid4(), uuid4(), uuid4())

    def assert_stage(
        *,
        posted: str,
        remaining: str,
        exposure: str,
        status: DebtStatus,
        open_count: int,
        revisions: tuple[int, ...],
    ) -> None:
        with factory() as session:
            debt = session.get(Debt, seed.debt_id)
            assert debt is not None
            progress = list_payment_progress_for_debts(
                session, debts=(debt,), server_now=PAYMENT_TIME
            )[debt.id]
            history = list_tenant_payment_history(
                session, actor=read_actor, debt_id=DebtId(debt.id)
            )
            exposure_value = calculate_payment_exposure(
                status=DebtStatus(debt.status),
                original_amount=OriginalAmountUZS(debt.original_amount_uzs),
                discounted_amount=DiscountedAmountUZS(debt.discounted_amount_uzs),
                posted_total=PostedPaymentTotalUZS(Decimal(posted)),
            )
        assert progress.posted_total_uzs == Decimal(posted)
        assert progress.remaining_due_uzs == Decimal(remaining)
        assert progress.status is status
        assert exposure_value.value == Decimal(exposure)
        assert open_debt_count_contribution(status) == open_count
        assert history.error is None
        assert (
            tuple(item.debt_revision_after.value for item in history.history)
            == revisions
        )

    assert_stage(
        posted="0",
        remaining="900",
        exposure="1000",
        status=DebtStatus.ACTIVE,
        open_count=1,
        revisions=(),
    )
    first = _record(m2_test_database, seed, amount="200", revision=2, key=keys[0])
    assert_stage(
        posted="200",
        remaining="700",
        exposure="800",
        status=DebtStatus.ACTIVE,
        open_count=1,
        revisions=(3,),
    )
    middle = _record(
        m2_test_database,
        seed,
        amount="300",
        revision=3,
        key=keys[1],
        method="transfer",
    )
    assert_stage(
        posted="500",
        remaining="400",
        exposure="500",
        status=DebtStatus.ACTIVE,
        open_count=1,
        revisions=(3, 4),
    )
    final = _record(
        m2_test_database,
        seed,
        amount="400",
        revision=4,
        key=keys[2],
        method="cash",
    )
    assert_stage(
        posted="900",
        remaining="0",
        exposure="0",
        status=DebtStatus.PAID,
        open_count=0,
        revisions=(3, 4, 5),
    )
    with factory() as session:
        receipts = tuple(
            get_tenant_payment_receipt_for_result(
                session, actor=read_actor, result=result
            ).receipt
            for result in (first, middle, final)
        )
    assert all(receipt is not None for receipt in receipts)
    assert tuple(
        receipt.historical_balance_after.value  # type: ignore[union-attr]
        for receipt in receipts
    ) == (Decimal("700"), Decimal("400"), Decimal("0"))
    assert (
        tuple(
            receipt.current_balance.value  # type: ignore[union-attr]
            for receipt in receipts
        )
        == (Decimal("0"),) * 3
    )
    assert {receipt.current_debt_status for receipt in receipts if receipt} == {
        DebtStatus.PAID
    }

    past_due = _seed_read_graph(
        m2_test_database,
        discounted="1000",
        due_date=date(2026, 8, 9),
    )
    with factory() as session:
        debt = session.get(Debt, past_due.debt_id)
        assert debt is not None
        past_progress = list_payment_progress_for_debts(
            session, debts=(debt,), server_now=PAYMENT_TIME
        )[debt.id]
    assert past_progress.is_payable is False

    second_shop = _add_second_shop_for_customer(
        m2_test_database, customer_id=seed.customer_id
    )
    second_payment = _record(
        m2_test_database,
        second_shop,
        amount="100",
        revision=2,
        key=uuid4(),
    )
    with factory.begin() as session:
        shop = session.get(Shop, seed.shop_id)
        assert shop is not None
        shop.status = ShopStatus.SUSPENDED.value
        shop.updated_at = PAYMENT_TIME
        session.add(
            Debt(
                shop_customer_id=seed.relation_id,
                created_by_user_id=seed.actor_id,
                original_amount_uzs=Decimal("250"),
                discount_basis_points=0,
                discounted_amount_uzs=Decimal("250"),
                due_date=date(2026, 8, 20),
                pending_expires_at=NOW + timedelta(hours=72),
                status=DebtStatus.ACTIVE.value,
                revision=2,
                accepted_at=NOW + timedelta(hours=1),
                created_at=NOW,
                updated_at=NOW + timedelta(hours=2),
            )
        )

    write_statements: list[str] = []
    select_statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many) -> None:
        upper = statement.lstrip().upper()
        if upper.startswith("SELECT"):
            select_statements.append(statement)
        elif upper.startswith(("INSERT", "UPDATE", "DELETE")):
            write_statements.append(statement)

    event.listen(m2_test_database, "before_cursor_execute", capture)
    try:
        with factory() as session:
            suspended_history = list_tenant_payment_history(
                session, actor=read_actor, debt_id=DebtId(seed.debt_id)
            )
            customer_user = session.get(User, seed.customer_user_id)
            assert customer_user is not None
            own_first = list_own_customer_payment_history(
                session,
                authenticated_user=customer_user,
                debt_id=DebtId(seed.debt_id),
            )
            own_second = list_own_customer_payment_history(
                session,
                authenticated_user=customer_user,
                debt_id=DebtId(second_shop.debt_id),
            )
            own_second_receipt = get_own_customer_payment_receipt(
                session,
                authenticated_user=customer_user,
                payment_id=second_payment.payment_id,
            )
        assert suspended_history.error is None
        assert len(suspended_history.history) == 3
        assert len(own_first.history) == 3
        assert len(own_second.history) == 1
        assert own_second_receipt.error is None
        assert write_statements == []

        select_statements.clear()
        with factory() as session:
            tenant_projection = list_tenant_customer_debts_with_payment_progress(
                session,
                shop_id=ShopId(seed.shop_id),
                shop_customer_id=ShopCustomerId(seed.relation_id),
                server_now=PAYMENT_TIME,
            )
        assert len(tenant_projection) == 2
        assert len(select_statements) == 3

        select_statements.clear()
        with factory() as session:
            customer = session.get(Customer, seed.customer_id)
            customer_user = session.get(User, seed.customer_user_id)
            assert customer is not None and customer_user is not None
            authority = CustomerDebtAuthority(
                user_id=customer_user.id,
                customer_id=customer.id,
            )
            before_adapter_queries = len(select_statements)
            customer_projection = list_customer_debts_with_payment_progress(
                session,
                authority=authority,
                server_now=PAYMENT_TIME,
            )
            adapter_queries = len(select_statements) - before_adapter_queries
        assert len(customer_projection) >= 2
        # Two batched debt/shop reads, one grouped Payment sum, then the same
        # two batched presentation reads: constant for one or many shops.
        assert adapter_queries == 5
    finally:
        event.remove(m2_test_database, "before_cursor_execute", capture)
