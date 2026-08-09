from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.idempotency.contracts import IdempotencyOutcome
from app.idempotency.models import IdempotencyKey
from app.payment.commands import CreatePaymentRawForm, assemble_create_payment_command
from app.payment.models import Payment
from app.payment.service import PaymentMutationRejected, record_debt_payment
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from tests.test_payment_targeting_postgresql import _context, _seed_one

PAYMENT_TIME = datetime(2026, 8, 10, 12, tzinfo=UTC)


def _command(
    *,
    actor_id: UUID,
    shop_id: UUID,
    debt_id: UUID,
    amount: str,
    revision: int,
    key: UUID,
):
    actor = _context(actor_id, shop_id)
    assembled = assemble_create_payment_command(
        actor=actor,
        form=CreatePaymentRawForm(
            debt_id=str(debt_id),
            amount_uzs=amount,
            method="cash",
            idempotency_key=str(key),
            expected_revision=str(revision),
        ),
        header_idempotency_key=str(key),
    )
    assert assembled.error is None and assembled.command is not None
    return actor, assembled.command


def _counts(factory) -> tuple[int, int, int]:
    with factory() as session:
        return (
            int(session.scalar(select(func.count()).select_from(Payment))),
            int(session.scalar(select(func.count()).select_from(IdempotencyKey))),
            int(session.scalar(select(func.count()).select_from(AuditLog))),
        )


@pytest.mark.integration
@pytest.mark.parametrize("role", tuple(ShopRole))
def test_each_live_role_records_partial_and_replay_is_zero_write(
    m2_test_database: Engine, role: ShopRole
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(
        m2_test_database, role=role
    )
    factory = create_database_session_factory(m2_test_database)
    key = uuid4()
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=key,
    )

    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return PAYMENT_TIME

    with factory.begin() as session:
        created = record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=clock,
        )
    assert created.outcome is IdempotencyOutcome.NEW
    assert calls == 1
    before_replay = _counts(factory)

    with factory.begin() as session:
        replay = record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: pytest.fail("replay must not capture mutation time"),
        )

    assert replay.outcome is IdempotencyOutcome.REPLAY
    assert replay.payment_id == created.payment_id
    assert _counts(factory) == before_replay == (1, 1, 1)
    with factory() as session:
        debt = session.get(Debt, debt_id)
        payment = session.get(Payment, created.payment_id.as_uuid())
        assert debt is not None and payment is not None
        assert debt.status == "active" and debt.revision == 3
        assert debt.paid_at is None and debt.updated_at == PAYMENT_TIME
        assert payment.amount_uzs == Decimal("400")
        assert payment.debt_revision_after == 3


@pytest.mark.integration
def test_exact_remaining_is_one_atomic_paid_transition_and_new_key_is_denied(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1000",
        revision=2,
        key=uuid4(),
    )
    with factory.begin() as session:
        result = record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: PAYMENT_TIME,
        )

    with factory() as session:
        debt = session.get(Debt, debt_id)
        payment = session.get(Payment, result.payment_id.as_uuid())
        events = tuple(session.scalars(select(AuditLog).order_by(AuditLog.event_type)))
        assert debt is not None and payment is not None
        assert debt.status == "paid" and debt.revision == 3
        assert debt.paid_at == debt.updated_at == payment.created_at == PAYMENT_TIME
        assert [event.event_type for event in events] == [
            "debt.paid",
            "payment.recorded",
        ]

    replay_counts = _counts(factory)
    with factory.begin() as session:
        replay = record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: pytest.fail("paid replay must resolve first"),
        )
    assert replay.payment_id == result.payment_id
    assert _counts(factory) == replay_counts == (1, 1, 2)

    _actor, new_command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1",
        revision=3,
        key=uuid4(),
    )
    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=new_command,
                payment_clock=lambda: PAYMENT_TIME,
            )
    assert captured.value.error is ErrorCode.DEBT_NOT_PAYABLE
    assert _counts(factory) == replay_counts


@pytest.mark.integration
def test_two_sequential_payments_use_recomputed_locked_sum_without_overpayment(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    _actor, second = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="600",
        revision=3,
        key=uuid4(),
    )
    with factory.begin() as session:
        first_result = record_debt_payment(
            session,
            actor=actor,
            command=first,
            payment_clock=lambda: PAYMENT_TIME,
        )
    with factory.begin() as session:
        second_result = record_debt_payment(
            session,
            actor=actor,
            command=second,
            payment_clock=lambda: PAYMENT_TIME,
        )

    assert first_result.payment_id != second_result.payment_id
    with factory() as session:
        amounts = tuple(
            session.scalars(
                select(Payment.amount_uzs).order_by(Payment.debt_revision_after)
            )
        )
        debt = session.get(Debt, debt_id)
        assert amounts == (Decimal("400"), Decimal("600"))
        assert sum(amounts, Decimal("0")) == Decimal("1000")
        assert debt is not None and debt.status == "paid" and debt.revision == 4


@pytest.mark.integration
@pytest.mark.parametrize("posted", ("1000", "1001"))
def test_zero_or_incoherent_locked_balance_rolls_back_new_key_and_all_mutations(
    m2_test_database: Engine, posted: str
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as seed:
        seed.add(
            Payment(
                id=uuid4(),
                debt_id=debt_id,
                recorded_by_user_id=actor_id,
                amount_uzs=Decimal(posted),
                method="cash",
                debt_revision_after=2,
                created_at=PAYMENT_TIME,
            )
        )
    baseline = _counts(factory)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1",
        revision=2,
        key=uuid4(),
    )

    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                payment_clock=lambda: PAYMENT_TIME,
            )

    assert captured.value.error is ErrorCode.DEBT_NOT_PAYABLE
    assert _counts(factory) == baseline
    with factory() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None and debt.status == "active" and debt.revision == 2


@pytest.mark.integration
def test_overpayment_and_audit_fault_leave_zero_durable_key_or_mutation(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, over = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1001",
        revision=2,
        key=uuid4(),
    )
    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=over,
                payment_clock=lambda: PAYMENT_TIME,
            )
    assert captured.value.error is ErrorCode.PAYMENT_AMOUNT_EXCEEDS_BALANCE
    assert _counts(factory) == (0, 0, 0)

    _actor, partial = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    monkeypatch.setattr(
        "app.payment.service.append_payment_recorded_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit fault")),
    )
    with pytest.raises(RuntimeError, match="audit fault"):
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=partial,
                payment_clock=lambda: PAYMENT_TIME,
            )
    assert _counts(factory) == (0, 0, 0)
    with factory() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None and debt.status == "active" and debt.revision == 2


@pytest.mark.integration
def test_same_key_changed_payload_conflicts_before_debt_state_and_writes(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    key = uuid4()
    actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=key,
    )
    _actor, changed = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="401",
        revision=2,
        key=key,
    )
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=actor,
            command=first,
            payment_clock=lambda: PAYMENT_TIME,
        )
    baseline = _counts(factory)

    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=changed,
                payment_clock=lambda: pytest.fail("conflict resolves before clock"),
            )

    assert captured.value.error is ErrorCode.IDEMPOTENCY_CONFLICT
    assert _counts(factory) == baseline == (1, 1, 1)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("authority_change", "expected_error"),
    (
        ("revoked", ErrorCode.FORBIDDEN),
        ("suspended", ErrorCode.SHOP_SUSPENDED),
    ),
)
def test_completed_replay_rechecks_live_mutation_authority_before_disclosure(
    m2_test_database: Engine,
    authority_change: str,
    expected_error: ErrorCode,
) -> None:
    actor_id, shop_id, staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=actor,
            command=command,
            payment_clock=lambda: PAYMENT_TIME,
        )
    baseline = _counts(factory)
    with factory.begin() as session:
        if authority_change == "revoked":
            staff = session.get(ShopStaff, staff_id)
            assert staff is not None
            staff.is_active = False
            staff.revoked_at = PAYMENT_TIME
            staff.updated_at = PAYMENT_TIME
        else:
            shop = session.get(Shop, shop_id)
            assert shop is not None
            shop.status = "suspended"
            shop.updated_at = PAYMENT_TIME

    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                payment_clock=lambda: pytest.fail(
                    "denied replay must not capture mutation time"
                ),
            )

    assert captured.value.error is expected_error
    assert _counts(factory) == baseline == (1, 1, 1)


@pytest.mark.integration
def test_platform_admin_without_live_staff_has_no_payment_authority(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(
        m2_test_database,
        staff_active=False,
    )
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        actor_row = session.get(User, actor_id)
        assert actor_row is not None
        actor_row.is_platform_admin = True
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )

    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                payment_clock=lambda: pytest.fail("authority denial precedes clock"),
            )

    assert captured.value.error is ErrorCode.FORBIDDEN
    assert _counts(factory) == (0, 0, 0)


@pytest.mark.integration
def test_different_live_actor_cannot_use_stale_revision_after_first_payment(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        second_actor = User(
            phone=f"+998{uuid4().int % 1_000_000_000:09d}",
            is_active=True,
        )
        session.add(second_actor)
        session.flush()
        session.add(
            ShopStaff(
                shop_id=shop_id,
                user_id=second_actor.id,
                role=ShopRole.CASHIER.value,
                is_active=True,
                created_at=PAYMENT_TIME,
                updated_at=PAYMENT_TIME,
            )
        )
        second_actor_id = second_actor.id
    first_actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    second_context, second = _command(
        actor_id=second_actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="400",
        revision=2,
        key=uuid4(),
    )
    with factory.begin() as session:
        record_debt_payment(
            session,
            actor=first_actor,
            command=first,
            payment_clock=lambda: PAYMENT_TIME,
        )
    with pytest.raises(PaymentMutationRejected) as captured:
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=second_context,
                command=second,
                payment_clock=lambda: PAYMENT_TIME,
            )
    assert captured.value.error is ErrorCode.DEBT_CHANGED
    assert _counts(factory) == (1, 1, 1)


@pytest.mark.integration
@pytest.mark.parametrize(
    "fault_symbol",
    (
        "insert_or_resolve_key",
        "insert_payment",
        "update_locked_debt",
        "append_payment_recorded_audit",
        "append_debt_paid_audit",
    ),
)
def test_each_full_payment_flush_fault_rolls_back_the_complete_unit(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fault_symbol: str,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="1000",
        revision=2,
        key=uuid4(),
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{fault_symbol} fault")

    monkeypatch.setattr(f"app.payment.service.{fault_symbol}", fail)
    with pytest.raises(RuntimeError, match=f"{fault_symbol} fault"):
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                payment_clock=lambda: PAYMENT_TIME,
            )

    assert _counts(factory) == (0, 0, 0)
    with factory() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None
        assert debt.status == "active" and debt.revision == 2
        assert debt.paid_at is None


@pytest.mark.integration
def test_payment_revision_unique_fault_rolls_back_new_key_and_debt_update(
    m2_test_database: Engine,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        session.add(
            Payment(
                id=uuid4(),
                debt_id=debt_id,
                recorded_by_user_id=actor_id,
                amount_uzs=Decimal("100"),
                method="cash",
                debt_revision_after=3,
                created_at=PAYMENT_TIME,
            )
        )
    baseline = _counts(factory)
    actor, command = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="200",
        revision=2,
        key=uuid4(),
    )

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            record_debt_payment(
                session,
                actor=actor,
                command=command,
                payment_clock=lambda: PAYMENT_TIME,
            )

    assert _counts(factory) == baseline == (1, 0, 0)
    with factory() as session:
        debt = session.get(Debt, debt_id)
        assert debt is not None and debt.revision == 2 and debt.status == "active"


@pytest.mark.integration
@pytest.mark.parametrize("same_key", (True, False))
def test_parallel_stale_displayed_balance_has_no_overpayment_and_one_mutation_path(
    m2_test_database: Engine, same_key: bool
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    first_key = uuid4()
    second_key = first_key if same_key else uuid4()
    actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="600",
        revision=2,
        key=first_key,
    )
    _actor, second = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount="600",
        revision=2,
        key=second_key,
    )
    start = Barrier(2)

    def run(command):
        start.wait()
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

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(run, (first, second)))

    if same_key:
        outcome_names = sorted(outcome.outcome.value for outcome in outcomes)
        assert outcome_names == ["new", "replay"]
        assert outcomes[0].payment_id == outcomes[1].payment_id
        assert _counts(factory) == (1, 1, 1)
    else:
        errors = [
            outcome.error
            for outcome in outcomes
            if isinstance(outcome, PaymentMutationRejected)
        ]
        assert errors == [ErrorCode.DEBT_CHANGED]
        assert _counts(factory) == (1, 1, 1)
    with factory() as session:
        posted = session.scalar(select(func.sum(Payment.amount_uzs)))
        assert posted == Decimal("600")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("first_amount", "second_amount"),
    (
        ("1000", "1000"),
        ("400", "1000"),
    ),
)
def test_parallel_partial_and_exact_full_have_one_paid_or_partial_winner(
    m2_test_database: Engine,
    first_amount: str,
    second_amount: str,
) -> None:
    actor_id, shop_id, _staff_id, _relation_id, debt_id = _seed_one(m2_test_database)
    factory = create_database_session_factory(m2_test_database)
    actor, first = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount=first_amount,
        revision=2,
        key=uuid4(),
    )
    _actor, second = _command(
        actor_id=actor_id,
        shop_id=shop_id,
        debt_id=debt_id,
        amount=second_amount,
        revision=2,
        key=uuid4(),
    )
    start = Barrier(2)

    def run(command):
        start.wait()
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

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(run, (first, second)))

    winners = [
        outcome
        for outcome in outcomes
        if not isinstance(outcome, PaymentMutationRejected)
    ]
    losers = [
        outcome for outcome in outcomes if isinstance(outcome, PaymentMutationRejected)
    ]
    assert len(winners) == len(losers) == 1
    assert losers[0].error in {ErrorCode.DEBT_CHANGED, ErrorCode.DEBT_NOT_PAYABLE}
    with factory() as session:
        payments = tuple(session.scalars(select(Payment)))
        debt = session.get(Debt, debt_id)
        assert len(payments) == 1
        assert debt is not None and debt.revision == 3
        assert payments[0].debt_revision_after == debt.revision
        assert payments[0].amount_uzs <= Decimal("1000")
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
        expected_audits = 2 if debt.status == "paid" else 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == (
            expected_audits
        )
        if debt.status == "paid":
            assert debt.paid_at == PAYMENT_TIME
        else:
            assert debt.status == "active" and debt.paid_at is None
