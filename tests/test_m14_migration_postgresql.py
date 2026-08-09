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
from app.idempotency.models import IdempotencyKey
from app.payment.models import Payment
from app.shop.models import Shop
from app.shop_customer.models import ShopCustomer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M13_REVISION = "f4a5b6c7d8e"
M14_REVISION = "a5b6c7d8e9f0"
M15_REVISION = "b6c7d8e9f0a1"
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


def _active_debt(session: Session) -> tuple[User, Debt]:
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
        name=f"M14 migration {uuid4().hex[:8]}",
        phone=_phone(),
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all((customer, shop))
    session.flush()
    shop_customer = ShopCustomer(
        shop_id=shop.id,
        customer_id=customer.id,
        credit_limit_uzs=Decimal("1000000"),
        max_open_debts=2,
        list_status="normal",
        revision=1,
        created_by_user_id=actor.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(shop_customer)
    session.flush()
    debt = Debt(
        shop_customer_id=shop_customer.id,
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
    return actor, debt


def _check_names(engine: Engine, table_name: str) -> set[str]:
    return {
        item["name"]
        for item in inspect(engine).get_check_constraints(table_name)
        if item["name"] is not None
    }


def _assert_m14_schema_is_intact(engine: Engine) -> None:
    inspector = inspect(engine)
    assert "payments" in inspector.get_table_names()
    assert "paid_at" in {column["name"] for column in inspector.get_columns("debts")}
    assert "ck_idempotency_keys_endpoint_result_pair_allowed" in _check_names(
        engine, "idempotency_keys"
    )


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


@pytest.mark.integration
def test_fresh_database_upgrades_directly_to_m14_head(
    m2_test_database: Engine,
) -> None:
    config = _config()
    try:
        command.downgrade(config, "base")
        assert set(inspect(m2_test_database).get_table_names()) <= {"alembic_version"}
        command.upgrade(config, M14_REVISION)
        assert _current_revision(m2_test_database) == M14_REVISION
        _assert_m14_schema_is_intact(m2_test_database)
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_m13_database_upgrades_to_m14_head(m2_test_database: Engine) -> None:
    config = _config()
    try:
        command.downgrade(config, M13_REVISION)
        assert _current_revision(m2_test_database) == M13_REVISION
        assert "payments" not in inspect(m2_test_database).get_table_names()
        command.upgrade(config, M14_REVISION)
        assert _current_revision(m2_test_database) == M14_REVISION
        _assert_m14_schema_is_intact(m2_test_database)
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_every_revision_walks_from_base_to_the_single_current_head(
    m2_test_database: Engine,
) -> None:
    config = _config()
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [M15_REVISION]
    revisions = tuple(
        reversed(tuple(script.walk_revisions(base="base", head=M14_REVISION)))
    )
    try:
        command.downgrade(config, "base")
        for revision in revisions:
            command.upgrade(config, revision.revision)
            assert _current_revision(m2_test_database) == revision.revision
        assert _current_revision(m2_test_database) == M14_REVISION
        _assert_m14_schema_is_intact(m2_test_database)
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_m14_live_schema_matches_payment_debt_idempotency_and_audit_metadata(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)
    columns = inspector.get_columns("payments")

    assert [column["name"] for column in columns] == [
        "id",
        "debt_id",
        "recorded_by_user_id",
        "amount_uzs",
        "method",
        "debt_revision_after",
        "created_at",
    ]
    assert all(column["nullable"] is False for column in columns)
    assert all(column["default"] is None for column in columns)
    assert _check_names(m2_test_database, "payments") == {
        "ck_payments_amount_uzs_bounds",
        "ck_payments_method_allowed",
        "ck_payments_debt_revision_after_positive",
    }
    assert {item["name"] for item in inspector.get_unique_constraints("payments")} == {
        "uq_payments_debt_id_debt_revision_after"
    }
    assert inspector.get_indexes("payments") == [
        {
            "name": "uq_payments_debt_id_debt_revision_after",
            "unique": True,
            "column_names": ["debt_id", "debt_revision_after"],
            "include_columns": [],
            "duplicates_constraint": "uq_payments_debt_id_debt_revision_after",
            "dialect_options": {"postgresql_include": []},
        }
    ]
    assert "paid_at" in {column["name"] for column in inspector.get_columns("debts")}
    assert "ck_idempotency_keys_endpoint_result_pair_allowed" in _check_names(
        m2_test_database, "idempotency_keys"
    )
    assert {
        "ck_idempotency_keys_endpoint_allowed",
        "ck_idempotency_keys_result_object_type_allowed",
    }.isdisjoint(_check_names(m2_test_database, "idempotency_keys"))
    audit_checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints("audit_log")
    }
    assert "payment.recorded" in audit_checks["ck_audit_log_event_type_allowed"]
    assert "debt.paid" in audit_checks["ck_audit_log_payload_exact_shape"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    (
        ({"amount_uzs": Decimal("0")}, "ck_payments_amount_uzs_bounds"),
        ({"amount_uzs": Decimal("1000000000001")}, "ck_payments_amount_uzs_bounds"),
        ({"method": "cheque"}, "ck_payments_method_allowed"),
        ({"debt_revision_after": 0}, "ck_payments_debt_revision_after_positive"),
    ),
)
def test_payment_checks_are_enforced_by_postgresql(
    db_session: Session,
    overrides: dict[str, object],
    constraint_name: str,
) -> None:
    actor, debt = _active_debt(db_session)
    values: dict[str, object] = {
        "id": uuid4(),
        "debt_id": debt.id,
        "recorded_by_user_id": actor.id,
        "amount_uzs": Decimal("100"),
        "method": "cash",
        "debt_revision_after": 3,
        "created_at": NOW + timedelta(hours=2),
    }
    values.update(overrides)

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(Payment(**values))
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == constraint_name


@pytest.mark.integration
def test_payment_revision_is_unique_per_debt(db_session: Session) -> None:
    actor, debt = _active_debt(db_session)
    db_session.add(
        Payment(
            id=uuid4(),
            debt_id=debt.id,
            recorded_by_user_id=actor.id,
            amount_uzs=Decimal("100"),
            method="cash",
            debt_revision_after=3,
            created_at=NOW + timedelta(hours=2),
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(
                Payment(
                    id=uuid4(),
                    debt_id=debt.id,
                    recorded_by_user_id=actor.id,
                    amount_uzs=Decimal("100"),
                    method="card",
                    debt_revision_after=3,
                    created_at=NOW + timedelta(hours=3),
                )
            )
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == (
        "uq_payments_debt_id_debt_revision_after"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("foreign_key", "constraint_name"),
    (
        ("debt", "fk_payments_debt_id_debts_id"),
        ("actor", "fk_payments_recorded_by_user_id_users_id"),
    ),
)
def test_payment_foreign_keys_are_restrictive_and_live(
    db_session: Session, foreign_key: str, constraint_name: str
) -> None:
    actor, debt = _active_debt(db_session)
    values = {
        "id": uuid4(),
        "debt_id": uuid4() if foreign_key == "debt" else debt.id,
        "recorded_by_user_id": uuid4() if foreign_key == "actor" else actor.id,
        "amount_uzs": Decimal("100"),
        "method": "transfer",
        "debt_revision_after": 3,
        "created_at": NOW + timedelta(hours=2),
    }

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(Payment(**values))
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == constraint_name


@pytest.mark.integration
def test_all_six_debt_status_metadata_shapes_are_accepted(
    db_session: Session,
) -> None:
    actor, seed = _active_debt(db_session)
    relation_id = seed.shop_customer_id
    rows: list[Debt] = []
    for status in ("pending", "active", "rejected", "cancelled", "expired", "paid"):
        accepted_at = NOW + timedelta(hours=1) if status in {"active", "paid"} else None
        terminal_at = NOW + timedelta(hours=2)
        rows.append(
            Debt(
                shop_customer_id=relation_id,
                created_by_user_id=actor.id,
                original_amount_uzs=Decimal("100"),
                discount_basis_points=0,
                discounted_amount_uzs=Decimal("100"),
                due_date=date(2026, 8, 20),
                pending_expires_at=NOW + timedelta(hours=72),
                status=status,
                revision=3 if status == "paid" else 1,
                rejection_reason="declined" if status == "rejected" else None,
                cancellation_reason="cancelled" if status == "cancelled" else None,
                accepted_at=accepted_at,
                rejected_at=terminal_at if status == "rejected" else None,
                cancelled_at=terminal_at if status == "cancelled" else None,
                expired_at=terminal_at if status == "expired" else None,
                paid_at=terminal_at if status == "paid" else None,
                created_at=NOW,
                updated_at=terminal_at
                if status in {"rejected", "cancelled", "expired", "paid"}
                else accepted_at or NOW,
            )
        )
    db_session.add_all(rows)
    db_session.flush()

    assert {row.status for row in rows} == {
        "pending",
        "active",
        "rejected",
        "cancelled",
        "expired",
        "paid",
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mutations", "constraint_name"),
    (
        (
            {"status": "paid", "revision": 3},
            "ck_debts_status_metadata_matches_status",
        ),
        (
            {"paid_at": NOW + timedelta(hours=2)},
            "ck_debts_status_metadata_matches_status",
        ),
        (
            {
                "status": "paid",
                "revision": 3,
                "paid_at": NOW + timedelta(minutes=30),
                "updated_at": NOW + timedelta(hours=2),
            },
            "ck_debts_timestamp_order",
        ),
    ),
)
def test_debt_paid_metadata_and_timestamp_checks_are_live(
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
@pytest.mark.parametrize(
    ("endpoint", "result_type"),
    (
        ("shop.debts.create", "payment"),
        ("shop.debt_payments.create", "debt"),
        ("shop.payments.create", "payment"),
        ("shop.debts.create", "unknown"),
    ),
)
def test_idempotency_crossed_and_unknown_pairs_are_rejected_by_postgresql(
    db_session: Session,
    endpoint: str,
    result_type: str,
) -> None:
    actor = User(phone=_phone(), is_active=True)
    db_session.add(actor)
    db_session.flush()

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(
                IdempotencyKey(
                    actor_user_id=actor.id,
                    endpoint=endpoint,
                    key_digest="a" * 64,
                    request_hash="b" * 64,
                    result_object_type=result_type,
                    result_object_id=uuid4(),
                    created_at=NOW,
                )
            )
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == (
        "ck_idempotency_keys_endpoint_result_pair_allowed"
    )


@pytest.mark.integration
def test_both_lawful_idempotency_pairs_are_accepted_by_postgresql(
    db_session: Session,
) -> None:
    actor = User(phone=_phone(), is_active=True)
    db_session.add(actor)
    db_session.flush()
    for digest, endpoint, result_type in (
        ("a" * 64, "shop.debts.create", "debt"),
        ("b" * 64, "shop.debt_payments.create", "payment"),
    ):
        db_session.add(
            IdempotencyKey(
                actor_user_id=actor.id,
                endpoint=endpoint,
                key_digest=digest,
                request_hash="c" * 64,
                result_object_type=result_type,
                result_object_id=uuid4(),
                created_at=NOW,
            )
        )
    db_session.flush()


@pytest.mark.integration
def test_payment_audit_payload_shape_is_enforced_by_postgresql(
    db_session: Session,
) -> None:
    actor = User(phone=_phone(), is_active=True)
    db_session.add(actor)
    db_session.flush()
    valid_payload = {
        "amount_uzs": 100,
        "method": "cash",
        "from_status": "active",
        "to_status": "paid",
        "debt_revision_after": 3,
    }
    db_session.add(
        AuditLog(
            occurred_at=NOW,
            event_type="payment.recorded",
            actor_kind="USER",
            actor_user_id=actor.id,
            object_type="payment",
            object_id=uuid4(),
            payload=valid_payload,
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(
                AuditLog(
                    occurred_at=NOW,
                    event_type="payment.recorded",
                    actor_kind="USER",
                    actor_user_id=actor.id,
                    object_type="payment",
                    object_id=uuid4(),
                    payload=valid_payload | {"payment_id": str(uuid4())},
                )
            )
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == (
        "ck_audit_log_payload_exact_shape"
    )


@pytest.mark.integration
def test_debt_paid_audit_payload_shape_is_enforced_by_postgresql(
    db_session: Session,
) -> None:
    actor = User(phone=_phone(), is_active=True)
    db_session.add(actor)
    db_session.flush()
    valid_payload = {"source": "payment", "debt_revision_after": 3}
    db_session.add(
        AuditLog(
            occurred_at=NOW,
            event_type="debt.paid",
            actor_kind="USER",
            actor_user_id=actor.id,
            object_type="debt",
            object_id=uuid4(),
            payload=valid_payload,
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError) as caught:
        with db_session.begin_nested():
            db_session.add(
                AuditLog(
                    occurred_at=NOW,
                    event_type="debt.paid",
                    actor_kind="USER",
                    actor_user_id=actor.id,
                    object_type="debt",
                    object_id=uuid4(),
                    payload=valid_payload | {"payment_id": str(uuid4())},
                )
            )
            db_session.flush()

    assert caught.value.orig.diag.constraint_name == (
        "ck_audit_log_payload_exact_shape"
    )


@pytest.mark.integration
def test_empty_m14_downgrade_restores_exact_m13_and_reupgrades(
    m2_test_database: Engine,
) -> None:
    config = _config()
    try:
        command.downgrade(config, M13_REVISION)
        inspector = inspect(m2_test_database)
        assert "payments" not in inspector.get_table_names()
        assert "paid_at" not in {
            column["name"] for column in inspector.get_columns("debts")
        }
        assert {
            "ck_idempotency_keys_endpoint_allowed",
            "ck_idempotency_keys_result_object_type_allowed",
        } <= _check_names(m2_test_database, "idempotency_keys")
        audit_checks = {
            item["name"]: item["sqltext"]
            for item in inspector.get_check_constraints("audit_log")
        }
        assert "payment.recorded" not in audit_checks["ck_audit_log_event_type_allowed"]
        with m2_test_database.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            assert revision == M13_REVISION
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_payment_state_blocks_downgrade_before_any_schema_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    actor, debt = _active_debt(db_session)
    db_session.add(
        Payment(
            id=uuid4(),
            debt_id=debt.id,
            recorded_by_user_id=actor.id,
            amount_uzs=Decimal("100"),
            method="cash",
            debt_revision_after=3,
            created_at=NOW + timedelta(hours=2),
        )
    )
    db_session.commit()

    with pytest.raises(RuntimeError, match="payment state exists"):
        command.downgrade(_config(), M13_REVISION)

    _assert_m14_schema_is_intact(m2_test_database)


@pytest.mark.integration
def test_payment_idempotency_state_blocks_downgrade_before_schema_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    actor = User(phone=_phone(), is_active=True)
    db_session.add(actor)
    db_session.flush()
    db_session.add(
        IdempotencyKey(
            actor_user_id=actor.id,
            endpoint="shop.debt_payments.create",
            key_digest="a" * 64,
            request_hash="b" * 64,
            result_object_type="payment",
            result_object_id=uuid4(),
            created_at=NOW,
        )
    )
    db_session.commit()

    with pytest.raises(RuntimeError, match="payment idempotency state exists"):
        command.downgrade(_config(), M13_REVISION)

    _assert_m14_schema_is_intact(m2_test_database)


@pytest.mark.integration
def test_paid_debt_state_blocks_downgrade_before_schema_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    _actor, debt = _active_debt(db_session)
    debt.status = "paid"
    debt.revision = 3
    debt.paid_at = NOW + timedelta(hours=2)
    debt.updated_at = debt.paid_at
    db_session.commit()

    with pytest.raises(RuntimeError, match="paid debt state exists"):
        command.downgrade(_config(), M13_REVISION)

    _assert_m14_schema_is_intact(m2_test_database)


@pytest.mark.integration
def test_m14_audit_state_blocks_downgrade_before_schema_mutation(
    m2_test_database: Engine,
    db_session: Session,
) -> None:
    actor = User(phone=_phone(), is_active=True)
    db_session.add(actor)
    db_session.flush()
    db_session.add(
        AuditLog(
            occurred_at=NOW,
            event_type="payment.recorded",
            actor_kind="USER",
            actor_user_id=actor.id,
            object_type="payment",
            object_id=uuid4(),
            payload={
                "amount_uzs": 100,
                "method": "cash",
                "from_status": "active",
                "to_status": "active",
                "debt_revision_after": 3,
            },
        )
    )
    db_session.commit()

    with pytest.raises(RuntimeError, match="M14 audit history exists"):
        command.downgrade(_config(), M13_REVISION)

    _assert_m14_schema_is_intact(m2_test_database)
