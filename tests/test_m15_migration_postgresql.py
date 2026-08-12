from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.audit.models import AuditLog
from app.auth.models import User
from app.customer.models import Customer
from app.db import create_database_session_factory
from app.debt.models import Debt
from app.payment.models import Payment
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M14_REVISION = "a5b6c7d8e9f0"
M15_REVISION = "b6c7d8e9f0a1"
M16_REVISION = "c7d8e9f0a1b2"
NOW = datetime(2026, 8, 9, 8, tzinfo=UTC)


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


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


def _seed_parents(session: Session) -> tuple[User, ShopCustomer]:
    actor = User(phone=_phone(), is_active=True)
    customer_user = User(phone=_phone(), is_active=True)
    session.add_all((actor, customer_user))
    session.flush()
    customer = Customer(
        user_id=customer_user.id,
        onboarding_status="active",
        activated_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    shop = Shop(
        name=f"M15 migration {uuid4().hex[:8]}",
        phone=_phone(),
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all((customer, shop))
    session.flush()
    relation = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=10,
        list_status="normal",
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(relation)
    session.flush()
    return actor, relation


def _active_debt(session: Session) -> tuple[User, Debt]:
    actor, relation = _seed_parents(session)
    debt = Debt(
        shop_customer_id=relation.id,
        created_by_user_id=actor.id,
        original_amount_uzs=Decimal("1000"),
        discount_basis_points=1000,
        discounted_amount_uzs=Decimal("900"),
        due_date=date(2026, 8, 12),
        pending_expires_at=NOW + timedelta(hours=72),
        status="active",
        revision=2,
        accepted_at=NOW + timedelta(hours=1),
        created_at=NOW,
        updated_at=NOW + timedelta(hours=1),
    )
    session.add(debt)
    session.flush()
    return actor, debt


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


def _check_names(engine: Engine, table_name: str) -> set[str]:
    return {
        item["name"]
        for item in inspect(engine).get_check_constraints(table_name)
        if item["name"] is not None
    }


def _payment_schema(engine: Engine) -> tuple[object, ...]:
    inspector = inspect(engine)
    columns = tuple(
        (item["name"], str(item["type"]), item["nullable"], item["default"])
        for item in inspector.get_columns("payments")
    )
    checks = tuple(
        sorted(
            (item["name"], item["sqltext"])
            for item in inspector.get_check_constraints("payments")
        )
    )
    unique = tuple(
        sorted(item["name"] for item in inspector.get_unique_constraints("payments"))
    )
    return columns, checks, unique


@pytest.mark.integration
def test_fresh_upgrade_and_every_revision_walk_reach_single_m15_head(
    m2_test_database: Engine,
) -> None:
    config = _config()
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [M16_REVISION]
    revisions = tuple(reversed(tuple(scripts.walk_revisions(base="base", head="head"))))
    try:
        command.downgrade(config, "base")
        for revision in revisions:
            command.upgrade(config, revision.revision)
            assert _current_revision(m2_test_database) == revision.revision
        assert _current_revision(m2_test_database) == M16_REVISION
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_m14_upgrade_preserves_existing_rows_without_backfill_or_payment_delta(
    m2_test_database: Engine,
) -> None:
    config = _config()
    debt_id = uuid4()
    try:
        command.downgrade(config, M14_REVISION)
        factory = create_database_session_factory(m2_test_database)
        with factory.begin() as session:
            actor, relation = _seed_parents(session)
            session.execute(
                text(
                    "INSERT INTO debts (id, shop_customer_id, created_by_user_id, "
                    "original_amount_uzs, discount_basis_points, "
                    "discounted_amount_uzs, due_date, pending_expires_at, status, "
                    "revision, accepted_at, created_at, updated_at) VALUES "
                    "(:id, :relation, :actor, 1000, 1000, 900, :due_date, "
                    ":pending_expires_at, 'active', 3, :accepted_at, :created_at, "
                    ":updated_at)"
                ),
                {
                    "id": debt_id,
                    "relation": relation.id,
                    "actor": actor.id,
                    "due_date": date(2026, 8, 8),
                    "pending_expires_at": NOW + timedelta(hours=72),
                    "accepted_at": NOW + timedelta(hours=1),
                    "created_at": NOW,
                    "updated_at": NOW + timedelta(hours=2),
                },
            )
            session.add(
                Payment(
                    id=uuid4(),
                    debt_id=debt_id,
                    recorded_by_user_id=actor.id,
                    amount_uzs=Decimal("100"),
                    method="cash",
                    debt_revision_after=3,
                    created_at=NOW + timedelta(hours=2),
                )
            )
        payment_before = _payment_schema(m2_test_database)

        command.upgrade(config, M15_REVISION)

        assert _payment_schema(m2_test_database) == payment_before
        with m2_test_database.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, revision, original_amount_uzs, "
                    "discounted_amount_uzs, due_date, overdue_at, overdue_revision "
                    "FROM debts WHERE id = :id"
                ),
                {"id": debt_id},
            ).one()
        assert tuple(row) == (
            "active",
            3,
            Decimal("1000"),
            Decimal("900"),
            date(2026, 8, 8),
            None,
            None,
        )
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_empty_downgrade_and_reupgrade_restore_exact_m14_shape(
    m2_test_database: Engine,
) -> None:
    config = _config()
    command.downgrade(config, M14_REVISION)
    try:
        columns = {
            item["name"] for item in inspect(m2_test_database).get_columns("debts")
        }
        assert {"overdue_at", "overdue_revision"}.isdisjoint(columns)
        assert "ix_debts_status_due_date_id" not in {
            item["name"] for item in inspect(m2_test_database).get_indexes("debts")
        }
        assert _current_revision(m2_test_database) == M14_REVISION
        command.upgrade(config, M15_REVISION)
        assert _current_revision(m2_test_database) == M15_REVISION
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
@pytest.mark.parametrize("late_paid", (False, True))
def test_overdue_and_late_paid_rows_block_downgrade_before_schema_mutation(
    m2_test_database: Engine,
    db_session: Session,
    late_paid: bool,
) -> None:
    _actor, debt = _active_debt(db_session)
    overdue_at = NOW + timedelta(hours=2)
    debt.status = "paid" if late_paid else "overdue"
    debt.revision = 4 if late_paid else 3
    debt.overdue_at = overdue_at
    debt.overdue_revision = 3
    debt.paid_at = overdue_at + timedelta(hours=1) if late_paid else None
    debt.updated_at = debt.paid_at or overdue_at
    db_session.commit()

    with pytest.raises(RuntimeError, match="M15 downgrade blocked"):
        command.downgrade(_config(), M14_REVISION)

    assert _current_revision(m2_test_database) == M16_REVISION
    assert "overdue_at" in {
        item["name"] for item in inspect(m2_test_database).get_columns("debts")
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    ("event_type", "object_type", "payload"),
    (
        (
            "debt.overdue",
            "debt",
            {
                "source": "batch",
                "from_status": "active",
                "to_status": "overdue",
                "overdue_revision": 3,
                "business_date": "2026-08-10",
            },
        ),
        (
            "debt.clawback_applied",
            "debt",
            {
                "source": "batch",
                "from_basis": "discounted",
                "to_basis": "original",
                "balance_increase_uzs": 100,
                "overdue_revision": 3,
            },
        ),
    ),
)
def test_m15_system_audit_history_blocks_downgrade_without_debt_row(
    m2_test_database: Engine,
    db_session: Session,
    event_type: str,
    object_type: str,
    payload: dict[str, object],
) -> None:
    db_session.add(
        AuditLog(
            event_type=event_type,
            actor_kind="SYSTEM",
            actor_user_id=None,
            object_type=object_type,
            object_id=uuid4(),
            payload=payload,
            occurred_at=NOW,
        )
    )
    db_session.commit()

    with pytest.raises(RuntimeError, match="M15 downgrade blocked"):
        command.downgrade(_config(), M14_REVISION)

    assert _current_revision(m2_test_database) == M16_REVISION


@pytest.mark.integration
def test_overdue_payment_audit_shape_is_accepted_and_blocks_m14_downgrade(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    actor, _relation = _seed_parents(db_session)
    db_session.add(
        AuditLog(
            event_type="payment.recorded",
            actor_kind="USER",
            actor_user_id=actor.id,
            object_type="payment",
            object_id=uuid4(),
            payload={
                "amount_uzs": 100,
                "method": "cash",
                "from_status": "overdue",
                "to_status": "paid",
                "debt_revision_after": 4,
            },
            occurred_at=NOW,
        )
    )
    db_session.commit()

    with pytest.raises(RuntimeError, match="M15 downgrade blocked"):
        command.downgrade(_config(), M14_REVISION)

    assert _current_revision(m2_test_database) == M16_REVISION


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mutations", "constraint_name"),
    (
        ({"status": "unknown"}, "ck_debts_status_allowed"),
        (
            {"overdue_at": NOW + timedelta(hours=2)},
            "ck_debts_overdue_metadata_pair",
        ),
        (
            {"overdue_at": NOW + timedelta(hours=2), "overdue_revision": 0},
            "ck_debts_overdue_revision_positive",
        ),
        (
            {"overdue_at": NOW + timedelta(hours=2), "overdue_revision": 4},
            "ck_debts_overdue_revision_not_after_revision",
        ),
        (
            {"status": "overdue", "revision": 3},
            "ck_debts_status_metadata_matches_status",
        ),
        (
            {
                "status": "overdue",
                "revision": 3,
                "overdue_at": NOW + timedelta(minutes=30),
                "overdue_revision": 3,
                "updated_at": NOW + timedelta(hours=2),
            },
            "ck_debts_timestamp_order",
        ),
    ),
)
def test_invalid_m15_debt_shapes_are_rejected_by_named_postgresql_checks(
    db_session: Session,
    mutations: dict[str, object],
    constraint_name: str,
) -> None:
    _actor, debt = _active_debt(db_session)
    for name, value in mutations.items():
        setattr(debt, name, value)

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == constraint_name


@pytest.mark.integration
def test_invalid_m15_status_insert_is_rejected_by_postgresql(
    db_session: Session,
) -> None:
    actor, relation = _seed_parents(db_session)
    db_session.add(
        Debt(
            shop_customer_id=relation.id,
            created_by_user_id=actor.id,
            original_amount_uzs=Decimal("1000"),
            discount_basis_points=1000,
            discounted_amount_uzs=Decimal("900"),
            due_date=date(2026, 8, 12),
            pending_expires_at=NOW + timedelta(hours=72),
            status="unknown",
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == "ck_debts_status_allowed"


@pytest.mark.integration
def test_m15_named_checks_index_and_audit_registry_are_live(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)
    assert {
        "ck_debts_overdue_metadata_pair",
        "ck_debts_overdue_revision_positive",
        "ck_debts_overdue_revision_not_after_revision",
        "ck_debts_status_allowed",
        "ck_debts_status_metadata_matches_status",
        "ck_debts_timestamp_order",
    } <= _check_names(m2_test_database, "debts")
    indexes = {
        item["name"]: item["column_names"] for item in inspector.get_indexes("debts")
    }
    assert indexes["ix_debts_status_due_date_id"] == ["status", "due_date", "id"]
    audit_checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints("audit_log")
    }
    assert "debt.overdue" in audit_checks["ck_audit_log_event_type_allowed"]
    assert "debt.clawback_applied" in audit_checks["ck_audit_log_payload_exact_shape"]
    assert "from_status" in audit_checks["ck_audit_log_payload_exact_shape"]
