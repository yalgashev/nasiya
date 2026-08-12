from __future__ import annotations

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.idempotency.contracts import CanonicalIdempotencyKey, IdempotencyOutcome
from app.idempotency.models import IdempotencyKey
from app.rating.disclosure import (
    RiskBandDisclosureCommand,
    create_risk_band_disclosure_request_hash_v1,
)
from app.rating.disclosure_service import (
    DisclosureMutationRejected,
    DisclosurePersistenceError,
    read_risk_band_disclosure_snapshot,
    record_risk_band_disclosure,
)
from app.rating.enums import RiskBand, RiskBandDisclosurePurpose
from app.rating.models import DisclosureViewLog, RatingEvent
from app.rating.targeting import DetachedDisclosureActorContext
from app.rating.values import DisclosureViewId
from app.shop.enums import ShopRole
from app.shop.models import Shop, ShopStaff
from app.shop_customer.models import ShopCustomer
from app.shop_customer.values import ShopCustomerId
from tests.test_m15_migration_postgresql import NOW
from tests.test_m16_rating_repository_postgresql import _debt


@dataclass(frozen=True)
class Seed:
    actor_id: UUID
    shop_id: UUID
    shop_customer_id: UUID


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session = create_database_session_factory(m2_test_database)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _phone() -> str:
    return f"+998{uuid4().int % 1_000_000_000:09d}"


def _seed(
    session: Session,
    *,
    list_status: str = "normal",
    role: ShopRole = ShopRole.CASHIER,
) -> Seed:
    actor = User(phone=_phone(), is_active=True, is_platform_admin=True)
    target_user = User(phone=_phone(), is_active=True)
    session.add_all((actor, target_user))
    session.flush()
    customer = Customer(
        user_id=target_user.id,
        onboarding_status="active",
        activated_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    shop = Shop(
        name=f"Disclosure {uuid4().hex[:8]}",
        phone=_phone(),
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all((customer, shop))
    session.flush()
    staff = ShopStaff(
        shop_id=shop.id,
        user_id=actor.id,
        role=role.value,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=10,
        list_status=list_status,
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all((staff, relation))
    session.flush()
    return Seed(actor.id, shop.id, relation.id)


def _actor(seed: Seed) -> DetachedDisclosureActorContext:
    return DetachedDisclosureActorContext(
        actor_user_id=seed.actor_id,
        current_shop_id=seed.shop_id,
        role_hint=ShopRole.OWNER,
    )


def _command(seed: Seed, *, key: UUID | None = None) -> RiskBandDisclosureCommand:
    actor = _actor(seed)
    purpose = RiskBandDisclosurePurpose.DEBT_PROPOSAL_REVIEW
    relation_id = ShopCustomerId(seed.shop_customer_id)
    request_hash = create_risk_band_disclosure_request_hash_v1(
        actor_user_id=actor.actor_user_id,
        current_shop_id=actor.current_shop_id,
        shop_customer_id=relation_id,
        purpose=purpose,
    )
    return RiskBandDisclosureCommand(
        actor_user_id=actor.actor_user_id,
        current_shop_id=actor.current_shop_id,
        shop_customer_id=relation_id,
        purpose=purpose,
        idempotency_key=CanonicalIdempotencyKey(key or uuid4()),
        request_hash=request_hash,
    )


def _command_for_purpose(
    seed: Seed, *, key: UUID, purpose: RiskBandDisclosurePurpose
) -> RiskBandDisclosureCommand:
    actor = _actor(seed)
    relation_id = ShopCustomerId(seed.shop_customer_id)
    return RiskBandDisclosureCommand(
        actor_user_id=actor.actor_user_id,
        current_shop_id=actor.current_shop_id,
        shop_customer_id=relation_id,
        purpose=purpose,
        idempotency_key=CanonicalIdempotencyKey(key),
        request_hash=create_risk_band_disclosure_request_hash_v1(
            actor_user_id=actor.actor_user_id,
            current_shop_id=actor.current_shop_id,
            shop_customer_id=relation_id,
            purpose=purpose,
        ),
    )


@pytest.mark.integration
@pytest.mark.parametrize("list_status", ["normal", "whitelisted", "blacklisted"])
def test_fresh_snapshot_is_atomic_safe_and_list_status_neutral(
    db_session: Session, list_status: str
) -> None:
    seed = _seed(db_session, list_status=list_status)
    command = _command(seed)
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return datetime(2026, 8, 12, 19, tzinfo=UTC)

    result = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=command,
        disclosure_clock=clock,
    )
    assert result.outcome is IdempotencyOutcome.NEW
    assert calls == 1

    stored = db_session.scalar(
        select(DisclosureViewLog).where(
            DisclosureViewLog.id == result.disclosure_view_id.as_uuid()
        )
    )
    assert stored is not None
    assert (stored.band, stored.purpose) == (
        RiskBand.NEW.value,
        command.purpose.value,
    )
    audit = db_session.scalar(select(AuditLog).where(AuditLog.object_id == stored.id))
    assert audit is not None
    assert audit.actor_user_id == seed.actor_id
    assert audit.payload == {"purpose": command.purpose.value, "band": "new"}
    assert repr(result).endswith("id=<redacted>)")


@pytest.mark.integration
@pytest.mark.parametrize("role", tuple(ShopRole))
def test_each_live_current_shop_role_may_create_a_private_snapshot(
    db_session: Session, role: ShopRole
) -> None:
    seed = _seed(db_session, role=role)
    result = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=_command(seed),
        disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
    )
    assert result.outcome is IdempotencyOutcome.NEW


@pytest.mark.integration
def test_completed_replay_uses_stored_snapshot_and_no_clock_or_duplicate(
    db_session: Session,
) -> None:
    seed = _seed(db_session)
    command = _command(seed)
    first = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=command,
        disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
    )

    def forbidden_clock() -> datetime:
        raise AssertionError("replay must not capture a disclosure clock")

    replay = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=command,
        disclosure_clock=forbidden_clock,
    )
    assert replay.outcome is IdempotencyOutcome.REPLAY
    assert replay.disclosure_view_id == first.disclosure_view_id
    assert db_session.scalar(select(func.count()).select_from(DisclosureViewLog)) == 1
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.event_type == "disclosure.risk_band_viewed")
        )
        == 1
    )

    snapshot = read_risk_band_disclosure_snapshot(
        db_session,
        actor=_actor(seed),
        disclosure_view_id=first.disclosure_view_id,
    )
    assert snapshot is not None
    assert snapshot.band is RiskBand.NEW
    assert not hasattr(snapshot, "score")
    assert not hasattr(snapshot, "shop_customer_id")


@pytest.mark.integration
def test_same_key_wait_has_one_fresh_winner_and_one_replay(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed(session)
    command = _command(seed)
    gate = Barrier(2)
    from app.rating import disclosure_service

    original = disclosure_service.lock_tenant_disclosure_target

    def synchronized_lock(*args, **kwargs):
        gate.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(
        disclosure_service,
        "lock_tenant_disclosure_target",
        synchronized_lock,
    )

    def run_once() -> IdempotencyOutcome:
        with factory.begin() as session:
            return record_risk_band_disclosure(
                session,
                actor=_actor(seed),
                command=command,
                disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
            ).outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_once) for _ in range(2)]
        outcomes = {future.result() for future in futures}
    assert outcomes == {IdempotencyOutcome.NEW, IdempotencyOutcome.REPLAY}
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DisclosureViewLog)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "disclosure.risk_band_viewed")
            )
            == 1
        )


@pytest.mark.integration
def test_two_fresh_keys_serialize_to_two_complete_snapshots(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed(session)
    commands = (_command(seed), _command(seed))
    gate = Barrier(2)
    from app.rating import disclosure_service

    original = disclosure_service.lock_tenant_disclosure_target

    def synchronized_lock(*args, **kwargs):
        gate.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(
        disclosure_service,
        "lock_tenant_disclosure_target",
        synchronized_lock,
    )

    def run_once(command: RiskBandDisclosureCommand):
        with factory.begin() as session:
            return record_risk_band_disclosure(
                session,
                actor=_actor(seed),
                command=command,
                disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(run_once, commands))
    assert tuple(result.outcome for result in results) == (
        IdempotencyOutcome.NEW,
        IdempotencyOutcome.NEW,
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DisclosureViewLog)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "disclosure.risk_band_viewed")
            )
            == 2
        )


@pytest.mark.integration
def test_cross_shop_same_customer_disclosures_are_tenant_bound(
    m2_test_database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        first = _seed(session)
        first_relation = session.get_one(ShopCustomer, first.shop_customer_id)
        second_shop = Shop(
            name=f"Second {uuid4().hex[:8]}",
            phone=_phone(),
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(second_shop)
        session.flush()
        session.add(
            ShopStaff(
                shop_id=second_shop.id,
                user_id=first.actor_id,
                role=ShopRole.MANAGER.value,
                is_active=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        second_relation = ShopCustomer(
            shop_id=second_shop.id,
            customer_id=first_relation.customer_id,
            credit_limit_uzs=Decimal("1000000"),
            max_open_debts=10,
            list_status="normal",
            revision=1,
            created_by_user_id=first.actor_id,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(second_relation)
        session.flush()
        second = Seed(first.actor_id, second_shop.id, second_relation.id)
    seeds = (first, second)
    gate = Barrier(2)
    from app.rating import disclosure_service

    original = disclosure_service.lock_tenant_disclosure_target

    def synchronized_lock(*args, **kwargs):
        gate.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(
        disclosure_service,
        "lock_tenant_disclosure_target",
        synchronized_lock,
    )

    def run_once(seed: Seed):
        with factory.begin() as session:
            return record_risk_band_disclosure(
                session,
                actor=_actor(seed),
                command=_command(seed),
                disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(run_once, seeds))
    assert all(result.outcome is IdempotencyOutcome.NEW for result in results)
    with factory() as session:
        rows = tuple(
            session.scalars(
                select(DisclosureViewLog).order_by(DisclosureViewLog.shop_id)
            )
        )
        assert len(rows) == 2
        assert {row.shop_id for row in rows} == {first.shop_id, second.shop_id}
        assert all(row.band == RiskBand.NEW.value for row in rows)


@pytest.mark.integration
def test_suspended_denies_fresh_but_preserves_own_historical_read(
    db_session: Session,
) -> None:
    seed = _seed(db_session)
    result = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=_command(seed),
        disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
    )
    shop = db_session.get(Shop, seed.shop_id)
    assert shop is not None
    shop.status = "suspended"
    db_session.flush()

    with pytest.raises(DisclosureMutationRejected) as fresh_error:
        record_risk_band_disclosure(
            db_session,
            actor=_actor(seed),
            command=_command(seed),
            disclosure_clock=lambda: datetime(2026, 8, 12, 9, tzinfo=UTC),
        )
    assert fresh_error.value.error is ErrorCode.SHOP_SUSPENDED

    historical = read_risk_band_disclosure_snapshot(
        db_session,
        actor=_actor(seed),
        disclosure_view_id=DisclosureViewId(result.disclosure_view_id.as_uuid()),
    )
    assert historical is not None
    assert historical.band is RiskBand.NEW


@pytest.mark.integration
def test_missing_target_and_platform_admin_without_membership_do_not_bypass(
    db_session: Session,
) -> None:
    seed = _seed(db_session)
    missing = Seed(seed.actor_id, seed.shop_id, uuid4())
    with pytest.raises(DisclosureMutationRejected) as missing_error:
        record_risk_band_disclosure(
            db_session,
            actor=_actor(seed),
            command=_command(missing),
            disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
        )
    assert missing_error.value.error is ErrorCode.SHOP_CUSTOMER_UNAVAILABLE

    staff = db_session.scalar(
        select(ShopStaff).where(
            ShopStaff.shop_id == seed.shop_id,
            ShopStaff.user_id == seed.actor_id,
        )
    )
    assert staff is not None
    staff.is_active = False
    staff.revoked_at = NOW
    db_session.flush()
    with pytest.raises(DisclosureMutationRejected) as revoked_error:
        record_risk_band_disclosure(
            db_session,
            actor=_actor(seed),
            command=_command(seed),
            disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
        )
    assert revoked_error.value.error is ErrorCode.FORBIDDEN


@pytest.mark.integration
def test_historical_get_queries_no_rating_debt_or_domain_write(
    db_session: Session,
) -> None:
    seed = _seed(db_session)
    result = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=_command(seed),
        disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
    )
    statements: list[str] = []

    def capture(*args) -> None:
        statements.append(args[2].casefold())

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        projection = read_risk_band_disclosure_snapshot(
            db_session,
            actor=_actor(seed),
            disclosure_view_id=result.disclosure_view_id,
        )
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert projection is not None
    assert len(statements) == 2
    assert not any("rating_events" in statement for statement in statements)
    assert not any(" debts" in statement for statement in statements)
    assert not any(
        token in statement
        for statement in statements
        for token in ("insert ", "update ", "delete ")
    )


@pytest.mark.integration
def test_same_key_different_hash_is_zero_write_conflict(db_session: Session) -> None:
    seed = _seed(db_session)
    key = uuid4()
    first = _command_for_purpose(
        seed,
        key=key,
        purpose=RiskBandDisclosurePurpose.DEBT_PROPOSAL_REVIEW,
    )
    conflict = _command_for_purpose(
        seed,
        key=key,
        purpose=RiskBandDisclosurePurpose.CREDIT_LIMIT_REVIEW,
    )
    record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=first,
        disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
    )
    calls = 0

    def forbidden_clock() -> datetime:
        nonlocal calls
        calls += 1
        return datetime(2026, 8, 12, 9, tzinfo=UTC)

    with pytest.raises(DisclosureMutationRejected) as caught:
        record_risk_band_disclosure(
            db_session,
            actor=_actor(seed),
            command=conflict,
            disclosure_clock=forbidden_clock,
        )
    assert caught.value.error is ErrorCode.IDEMPOTENCY_CONFLICT
    assert calls == 0
    assert db_session.scalar(select(func.count()).select_from(DisclosureViewLog)) == 1
    assert db_session.scalar(select(func.count()).select_from(IdempotencyKey)) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "fault_target",
    ["insert_disclosure_view_locked", "append_risk_band_disclosure_audit"],
)
def test_persistence_fault_rolls_back_key_snapshot_and_audit(
    m2_test_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    fault_target: str,
) -> None:
    factory = create_database_session_factory(m2_test_database)
    with factory.begin() as session:
        seed = _seed(session)
    from app.rating import disclosure_service

    def fail_audit(*args, **kwargs) -> None:
        raise RuntimeError(f"unsafe {seed.actor_id}")

    monkeypatch.setattr(disclosure_service, fault_target, fail_audit)
    with pytest.raises(
        DisclosurePersistenceError,
        match="Risk-band disclosure persistence failed",
    ) as caught:
        with factory.begin() as session:
            record_risk_band_disclosure(
                session,
                actor=_actor(seed),
                command=_command(seed),
                disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
            )
    assert str(seed.actor_id) not in str(caught.value)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DisclosureViewLog)) == 0
        assert session.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_type == "disclosure.risk_band_viewed")
            )
            == 0
        )


@pytest.mark.integration
def test_cross_shop_hard_block_then_paid_negative_history_is_numeric(
    db_session: Session,
) -> None:
    seed = _seed(db_session)
    current_relation = db_session.get(ShopCustomer, seed.shop_customer_id)
    assert current_relation is not None
    other_shop = Shop(
        name=f"Other {uuid4().hex[:8]}",
        phone=_phone(),
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(other_shop)
    db_session.flush()
    other_relation = ShopCustomer(
        shop_id=other_shop.id,
        customer_id=current_relation.customer_id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=10,
        list_status="whitelisted",
        revision=1,
        created_by_user_id=seed.actor_id,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add(other_relation)
    db_session.flush()
    actor = db_session.get(User, seed.actor_id)
    assert actor is not None
    debt = _debt(db_session, relation=other_relation, actor=actor)
    debt.due_date = date(2026, 8, 11)

    blocked = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=_command(seed),
        disclosure_clock=lambda: datetime(2026, 8, 12, 8, tzinfo=UTC),
    )
    blocked_row = db_session.get(
        DisclosureViewLog, blocked.disclosure_view_id.as_uuid()
    )
    assert blocked_row is not None and blocked_row.band == RiskBand.BLOCKED.value

    overdue_at = datetime(2026, 8, 12, 7, tzinfo=UTC)
    debt.status = "paid"
    debt.revision = 4
    debt.overdue_at = overdue_at
    debt.overdue_revision = 3
    debt.paid_at = overdue_at + timedelta(hours=1)
    debt.updated_at = debt.paid_at
    db_session.add(
        RatingEvent(
            id=uuid4(),
            shop_customer_id=other_relation.id,
            debt_id=debt.id,
            event_type="overdue",
            delta=-15,
            occurred_at=overdue_at,
            business_date=date(2026, 8, 12),
            recording_source="live",
        )
    )
    db_session.flush()

    numeric = record_risk_band_disclosure(
        db_session,
        actor=_actor(seed),
        command=_command(seed),
        disclosure_clock=lambda: datetime(2026, 8, 12, 9, tzinfo=UTC),
    )
    numeric_row = db_session.get(
        DisclosureViewLog, numeric.disclosure_view_id.as_uuid()
    )
    assert numeric_row is not None and numeric_row.band == RiskBand.RED.value
    historical = read_risk_band_disclosure_snapshot(
        db_session,
        actor=_actor(seed),
        disclosure_view_id=blocked.disclosure_view_id,
    )
    assert historical is not None and historical.band is RiskBand.BLOCKED
