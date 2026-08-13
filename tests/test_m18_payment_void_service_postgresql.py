from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

import app.payment.service as payment_service_module
import app.payment.void_service as void_service_module
from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.debt.values import DebtId
from app.idempotency.models import IdempotencyKey
from app.payment.commands import VoidPaymentRawForm, assemble_void_payment_command
from app.payment.enums import PaymentVoidOutcome
from app.payment.models import Payment, PaymentVoid
from app.payment.read_service import compose_payment_receipt
from app.payment.repository import get_tenant_payment, posted_payment_total
from app.payment.service import PaymentMutationRejected
from app.payment.values import PaymentId
from app.payment.void_service import void_payment
from app.rating.adapters import SqlAlchemyLockedRatingAppendAdapter
from app.rating.models import RatingEvent
from app.shop.enums import ShopRole
from app.shop.models import ShopStaff
from app.shop.values import ShopId
from tests.rating_support import record_debt_payment
from tests.test_m16_rating_append_services_postgresql import _make_eligible_debts
from tests.test_m17_recovery_service_postgresql import _seed_written_off_source
from tests.test_payment_service_postgresql import _command
from tests.test_payment_targeting_postgresql import _seed_one

VOIDED_AT = datetime(2026, 8, 11, 12, tzinfo=UTC)


def _void_command(
    *,
    actor_id: UUID,
    shop_id: UUID,
    payment: Payment,
    key: UUID,
    reason: str = "incorrect_amount",
):
    payment_id = PaymentId(payment.id)
    assembled = assemble_void_payment_command(
        actor_user_id=actor_id,
        current_shop_id=shop_id,
        payment_id=payment_id,
        server_resolved_debt_id=DebtId(payment.debt_id),
        raw=VoidPaymentRawForm(
            expected_revision=str(payment.debt_revision_after),
            reason=reason,
            idempotency_key=str(key),
            confirmed="yes",
        ),
    )
    assert assembled.command is not None
    assert assembled.command.payment_id == payment_id
    return assembled.command


def _counts(factory):
    with factory() as session:
        return tuple(
            int(session.scalar(select(func.count()).select_from(model)))
            for model in (Payment, PaymentVoid, RatingEvent, AuditLog, IdempotencyKey)
        )


def _promote_owner(factory, *, shop_id: UUID) -> None:
    with factory.begin() as session:
        staff = session.scalar(select(ShopStaff).where(ShopStaff.shop_id == shop_id))
        assert staff is not None
        staff.role = ShopRole.OWNER.value


@pytest.mark.integration
def test_void_exact_payoff_appends_compensation_audits_and_zero_write_replay(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, relation_id, debt_ids = _make_eligible_debts(
        m2_test_database, count=1
    )
    factory = create_database_session_factory(m2_test_database)
    _promote_owner(factory, shop_id=shop_id)
    actor, payment_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=payment_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    with factory() as session:
        payment = session.get_one(Payment, created.payment_id.as_uuid())
        void_key = uuid4()
        command = _void_command(
            actor_id=actor_id, shop_id=shop_id, payment=payment, key=void_key
        )
        changed_command = _void_command(
            actor_id=actor_id,
            shop_id=shop_id,
            payment=payment,
            key=void_key,
            reason="wrong_debt",
        )

    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return VOIDED_AT

    with factory.begin() as session:
        result = void_payment(
            session,
            actor=actor,
            command=command,
            rating_port=adapter,
            payment_void_clock=clock,
        )
    assert result.outcome is PaymentVoidOutcome.NEW
    assert calls == 1
    before_replay = _counts(factory)
    with factory.begin() as session:
        replay = void_payment(
            session,
            actor=actor,
            command=command,
            rating_port=adapter,
            payment_void_clock=lambda: pytest.fail("replay captured clock"),
        )
    assert replay.outcome is PaymentVoidOutcome.REPLAY
    assert _counts(factory) == before_replay
    with pytest.raises(PaymentMutationRejected) as changed:
        with factory.begin() as session:
            void_payment(
                session,
                actor=actor,
                command=changed_command,
                rating_port=adapter,
                payment_void_clock=lambda: pytest.fail("conflict captured clock"),
            )
    assert changed.value.error is ErrorCode.IDEMPOTENCY_CONFLICT
    assert _counts(factory) == before_replay

    with factory() as session:
        debt = session.get_one(Debt, debt_ids[0])
        void = session.scalar(select(PaymentVoid))
        events = tuple(
            session.scalars(select(RatingEvent).order_by(RatingEvent.event_type))
        )
        audits = tuple(session.scalars(select(AuditLog).order_by(AuditLog.event_type)))
        assert debt.status == "active" and debt.revision == 4
        assert debt.paid_at is None and debt.updated_at == VOIDED_AT
        assert void is not None and void.shop_customer_id == relation_id
        assert [(event.event_type, event.delta) for event in events] == [
            ("on_time_paid", 5),
            ("on_time_paid_voided", -5),
        ]
        assert [row.event_type for row in audits] == [
            "debt.paid",
            "debt.reopened_after_payment_void",
            "payment.recorded",
            "payment.voided",
        ]
        immutable = session.get_one(Payment, created.payment_id.as_uuid())
        assert immutable.amount_uzs == Decimal("100000")
        scoped = get_tenant_payment(
            session,
            shop_id=ShopId(shop_id),
            payment_id=created.payment_id,
        )
        assert scoped is not None
        assert scoped.voided_at == VOIDED_AT
        assert scoped.void_reason is not None
        receipt = compose_payment_receipt(session, row=scoped, server_now=VOIDED_AT)
        assert receipt.historical_balance_after.value == 0
        assert receipt.current_balance.value == Decimal("100000")
        assert posted_payment_total(
            session, debt_id=DebtId(debt_ids[0]), through_revision=3
        ).value == Decimal("100000")
        assert posted_payment_total(session, debt_id=DebtId(debt_ids[0])).value == 0


@pytest.mark.integration
def test_partial_void_preserves_status_and_has_no_rating_or_reopen_audit(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(
        m2_test_database, role=ShopRole.OWNER
    )
    factory = create_database_session_factory(m2_test_database)
    actor, payment_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=payment_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    with factory() as session:
        command = _void_command(
            actor_id=actor_id,
            shop_id=shop_id,
            payment=session.get_one(Payment, created.payment_id.as_uuid()),
            key=uuid4(),
        )
    with factory.begin() as session:
        void_payment(
            session,
            actor=actor,
            command=command,
            rating_port=adapter,
            payment_void_clock=lambda: VOIDED_AT,
        )

    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "active" and debt.revision == 4
        assert session.scalar(select(func.count()).select_from(RatingEvent)) == 0
        assert tuple(
            session.scalars(select(AuditLog.event_type).order_by(AuditLog.event_type))
        ) == ("payment.recorded", "payment.voided")


@pytest.mark.integration
def test_paid_after_due_void_appends_exact_overdue_effect_family(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _relation_id, debt_ids = _make_eligible_debts(
        m2_test_database, count=1
    )
    factory = create_database_session_factory(m2_test_database)
    _promote_owner(factory, shop_id=shop_id)
    actor, payment_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=payment_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    with factory() as session:
        command = _void_command(
            actor_id=actor_id,
            shop_id=shop_id,
            payment=session.get_one(Payment, created.payment_id.as_uuid()),
            key=uuid4(),
        )
    late_void = datetime(2026, 8, 21, 19, tzinfo=UTC)
    with factory.begin() as session:
        void_payment(
            session,
            actor=actor,
            command=command,
            rating_port=adapter,
            payment_void_clock=lambda: late_void,
        )

    with factory() as session:
        debt = session.get_one(Debt, debt_ids[0])
        assert debt.status == "overdue"
        assert debt.overdue_revision == debt.revision == 4
        events = tuple(
            session.execute(
                select(RatingEvent.event_type, RatingEvent.delta).order_by(
                    RatingEvent.event_type
                )
            )
        )
        assert events == (
            ("on_time_paid", 5),
            ("on_time_paid_voided", -5),
            ("overdue", -15),
        )
        overdue_audit = session.scalar(
            select(AuditLog).where(AuditLog.event_type == "debt.overdue")
        )
        clawback = session.scalar(
            select(AuditLog).where(AuditLog.event_type == "debt.clawback_applied")
        )
        assert overdue_audit is not None
        assert overdue_audit.payload["source"] == "payment_void"
        assert overdue_audit.payload["from_status"] == "paid"
        assert clawback is not None and clawback.payload["source"] == "payment_void"


@pytest.mark.integration
def test_cashier_and_double_void_fail_without_partial_evidence(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(
        m2_test_database, role=ShopRole.CASHIER
    )
    factory = create_database_session_factory(m2_test_database)
    actor, payment_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=payment_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    with factory() as session:
        command = _void_command(
            actor_id=actor_id,
            shop_id=shop_id,
            payment=session.get_one(Payment, created.payment_id.as_uuid()),
            key=uuid4(),
        )
    before = _counts(factory)
    with pytest.raises(PaymentMutationRejected) as denied:
        with factory.begin() as session:
            void_payment(session, actor=actor, command=command, rating_port=adapter)
    assert denied.value.error is ErrorCode.FORBIDDEN
    assert _counts(factory) == before

    _promote_owner(factory, shop_id=shop_id)
    with factory.begin() as session:
        void_payment(
            session,
            actor=actor,
            command=command,
            rating_port=adapter,
            payment_void_clock=lambda: VOIDED_AT,
        )
    after = _counts(factory)
    second = _void_command(
        actor_id=actor_id,
        shop_id=shop_id,
        payment=_detached_payment(factory, created.payment_id.as_uuid()),
        key=uuid4(),
    )
    with pytest.raises(PaymentMutationRejected) as duplicate:
        with factory.begin() as session:
            void_payment(session, actor=actor, command=second, rating_port=adapter)
    assert duplicate.value.error is ErrorCode.PAYMENT_NOT_VOIDABLE
    assert _counts(factory) == after


def _detached_payment(factory, payment_id: UUID) -> Payment:
    with factory() as session:
        row = session.get_one(Payment, payment_id)
        session.expunge(row)
        return row


def _void_existing_payment(
    factory,
    *,
    actor,
    actor_id: UUID,
    shop_id: UUID,
    payment_id: UUID,
    key: UUID,
    voided_at: datetime,
    adapter,
):
    command = _void_command(
        actor_id=actor_id,
        shop_id=shop_id,
        payment=_detached_payment(factory, payment_id),
        key=key,
    )
    with factory.begin() as session:
        return void_payment(
            session,
            actor=actor,
            command=command,
            rating_port=adapter,
            payment_void_clock=lambda: voided_at,
        )


@pytest.mark.integration
@pytest.mark.parametrize("same_key", (True, False))
def test_two_voids_serialize_to_one_complete_append_family(
    m2_test_database: Engine,
    same_key: bool,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(
        m2_test_database, role=ShopRole.OWNER
    )
    factory = create_database_session_factory(m2_test_database)
    actor, payment_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=payment_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    first_key = uuid4()
    first = _void_command(
        actor_id=actor_id,
        shop_id=shop_id,
        payment=_detached_payment(factory, created.payment_id.as_uuid()),
        key=first_key,
    )
    second = _void_command(
        actor_id=actor_id,
        shop_id=shop_id,
        payment=_detached_payment(factory, created.payment_id.as_uuid()),
        key=first_key if same_key else uuid4(),
    )
    start = Barrier(2)

    def worker(command):
        start.wait()
        try:
            with factory.begin() as session:
                return void_payment(
                    session,
                    actor=actor,
                    command=command,
                    rating_port=adapter,
                    payment_void_clock=lambda: VOIDED_AT,
                ).outcome.value
        except PaymentMutationRejected as exc:
            return exc.error.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(worker, (first, second)))

    assert sorted(outcomes) == (
        ["new", "replay"] if same_key else [ErrorCode.PAYMENT_NOT_VOIDABLE.value, "new"]
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentVoid)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "payment.voided")
            )
            == 1
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "fault_stage", ("before_void", "after_void", "after_compensation", "audit")
)
def test_each_void_stage_fault_rolls_back_key_debt_void_rating_and_audits(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fault_stage: str,
) -> None:
    actor_id, shop_id, _relation_id, debt_ids = _make_eligible_debts(
        m2_test_database, count=1
    )
    factory = create_database_session_factory(m2_test_database)
    _promote_owner(factory, shop_id=shop_id)
    actor, payment_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_ids[0],
        amount="100000",
        revision=2,
        key=uuid4(),
    )
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=payment_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    command = _void_command(
        actor_id=actor_id,
        shop_id=shop_id,
        payment=_detached_payment(factory, created.payment_id.as_uuid()),
        key=uuid4(),
    )
    before = _counts(factory)

    original_insert = void_service_module.insert_payment_void
    original_compensate = adapter.append_source_compensation
    original_audit = void_service_module.append_audit_event

    if fault_stage in {"before_void", "after_void"}:

        def fail_void(*args, **kwargs):
            if fault_stage == "after_void":
                original_insert(*args, **kwargs)
            raise RuntimeError("injected void stage fault")

        monkeypatch.setattr(void_service_module, "insert_payment_void", fail_void)
    elif fault_stage == "after_compensation":

        def fail_compensation(*args, **kwargs):
            original_compensate(*args, **kwargs)
            raise RuntimeError("injected compensation stage fault")

        monkeypatch.setattr(adapter, "append_source_compensation", fail_compensation)
    else:

        def fail_audit(*args, **kwargs):
            original_audit(*args, **kwargs)
            raise RuntimeError("injected audit stage fault")

        monkeypatch.setattr(void_service_module, "append_audit_event", fail_audit)

    with pytest.raises(RuntimeError, match="injected"):
        with factory.begin() as session:
            void_payment(
                session,
                actor=actor,
                command=command,
                rating_port=adapter,
                payment_void_clock=lambda: VOIDED_AT,
            )

    assert _counts(factory) == before
    with factory() as session:
        debt = session.get_one(Debt, debt_ids[0])
        assert debt.status == "paid" and debt.revision == 3
        assert debt.paid_at == datetime(2026, 8, 10, 12, tzinfo=UTC)


@pytest.mark.integration
def test_compensated_daily_slot_remains_consumed_and_later_day_reearns(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _relation_id, debt_ids = _make_eligible_debts(
        m2_test_database, count=1
    )
    debt_id = debt_ids[0]
    factory = create_database_session_factory(m2_test_database)
    _promote_owner(factory, shop_id=shop_id)
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    actor, first_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100000",
        revision=2,
        key=uuid4(),
    )
    with factory.begin() as session:
        first = record_debt_payment(
            session,
            actor=actor,
            command=first_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    _void_existing_payment(
        factory,
        actor=actor,
        actor_id=actor_id,
        shop_id=shop_id,
        payment_id=first.payment_id.as_uuid(),
        key=uuid4(),
        voided_at=datetime(2026, 8, 10, 13, tzinfo=UTC),
        adapter=adapter,
    )

    actor, same_day_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100000",
        revision=4,
        key=uuid4(),
    )
    with factory.begin() as session:
        same_day = record_debt_payment(
            session,
            actor=actor,
            command=same_day_command,
            payment_clock=lambda: datetime(2026, 8, 10, 14, tzinfo=UTC),
        )
    _void_existing_payment(
        factory,
        actor=actor,
        actor_id=actor_id,
        shop_id=shop_id,
        payment_id=same_day.payment_id.as_uuid(),
        key=uuid4(),
        voided_at=datetime(2026, 8, 10, 15, tzinfo=UTC),
        adapter=adapter,
    )

    actor, later_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100000",
        revision=6,
        key=uuid4(),
    )
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=actor,
            command=later_command,
            payment_clock=lambda: datetime(2026, 8, 11, 12, tzinfo=UTC),
        )

    with factory() as session:
        events = tuple(
            session.execute(
                select(
                    RatingEvent.event_type,
                    RatingEvent.source_revision,
                    RatingEvent.delta,
                ).order_by(
                    RatingEvent.occurred_at,
                    RatingEvent.debt_id,
                    RatingEvent.event_type,
                    RatingEvent.source_revision,
                )
            )
        )
        assert events == (
            ("on_time_paid", 3, 5),
            ("on_time_paid_voided", 3, -5),
            ("on_time_paid", 7, 5),
        )


@pytest.mark.integration
def test_settlement_cycle_compensation_cannot_farm_bonus(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, debt_id = _seed_written_off_source(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    _promote_owner(factory, shop_id=shop_id)
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    actor, first_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1000",
        revision=4,
        key=uuid4(),
        basis="original",
    )
    with factory.begin() as session:
        first = record_debt_payment(
            session,
            actor=actor,
            command=first_command,
            payment_clock=lambda: datetime(2026, 8, 15, 12, tzinfo=UTC),
        )
    _void_existing_payment(
        factory,
        actor=actor,
        actor_id=actor_id,
        shop_id=shop_id,
        payment_id=first.payment_id.as_uuid(),
        key=uuid4(),
        voided_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        adapter=adapter,
    )
    with factory() as session:
        reopened = session.get_one(Debt, debt_id)
        assert reopened.status == "written_off" and reopened.revision == 6
        assert reopened.written_off_settled_at is None

    actor, second_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1000",
        revision=6,
        key=uuid4(),
        basis="original",
    )
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=actor,
            command=second_command,
            payment_clock=lambda: datetime(2026, 8, 17, 12, tzinfo=UTC),
        )

    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "written_off_settled" and debt.revision == 7
        events = tuple(
            session.execute(
                select(
                    RatingEvent.event_type,
                    RatingEvent.source_revision,
                    RatingEvent.delta,
                )
                .where(
                    RatingEvent.event_type.in_(
                        ("written_off_settled", "written_off_settled_voided")
                    )
                )
                .order_by(
                    RatingEvent.occurred_at,
                    RatingEvent.event_type,
                    RatingEvent.source_revision,
                )
            )
        )
        assert events == (
            ("written_off_settled", 5, 10),
            ("written_off_settled_voided", 5, -10),
            ("written_off_settled", 7, 10),
        )


@pytest.mark.integration
def test_void_vs_new_payment_preserves_one_linear_latest_stack(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(
        m2_test_database, role=ShopRole.OWNER
    )
    factory = create_database_session_factory(m2_test_database)
    adapter = SqlAlchemyLockedRatingAppendAdapter()
    actor, first_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    with factory.begin() as session:
        first = record_debt_payment(
            session,
            actor=actor,
            command=first_command,
            payment_clock=lambda: datetime(2026, 8, 10, 12, tzinfo=UTC),
        )
    void_command = _void_command(
        actor_id=actor_id,
        shop_id=shop_id,
        payment=_detached_payment(factory, first.payment_id.as_uuid()),
        key=uuid4(),
    )
    actor, competing = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="100",
        revision=3,
        key=uuid4(),
    )
    void_staged = Barrier(2)
    payment_attempted = Barrier(2)
    release_void = Barrier(2)
    original_update = void_service_module.update_locked_debt
    original_payment_locks = payment_service_module.lock_tenant_payment_predecessors

    def pause_void(*args, **kwargs):
        result = original_update(*args, **kwargs)
        void_staged.wait()
        release_void.wait()
        return result

    def observe_payment_attempt(*args, **kwargs):
        payment_attempted.wait()
        return original_payment_locks(*args, **kwargs)

    monkeypatch.setattr(void_service_module, "update_locked_debt", pause_void)
    monkeypatch.setattr(
        payment_service_module,
        "lock_tenant_payment_predecessors",
        observe_payment_attempt,
    )

    def void_worker():
        with factory.begin() as session:
            return void_payment(
                session,
                actor=actor,
                command=void_command,
                rating_port=adapter,
                payment_void_clock=lambda: VOIDED_AT,
            ).outcome.value

    def payment_worker():
        try:
            with factory.begin() as session:
                record_debt_payment(
                    session,
                    actor=actor,
                    command=competing,
                    payment_clock=lambda: VOIDED_AT,
                )
        except PaymentMutationRejected as exc:
            return exc.error.value
        raise AssertionError("stale competing Payment unexpectedly committed")

    with ThreadPoolExecutor(max_workers=2) as executor:
        void_future = executor.submit(void_worker)
        void_staged.wait()
        payment_future = executor.submit(payment_worker)
        payment_attempted.wait()
        assert not payment_future.done()
        release_void.wait()
        assert void_future.result() == "new"
        assert payment_future.result() == ErrorCode.DEBT_CHANGED.value

    with factory() as session:
        debt = session.get_one(Debt, debt_id)
        assert debt.status == "active" and debt.revision == 4
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        assert session.scalar(select(func.count()).select_from(PaymentVoid)) == 1
