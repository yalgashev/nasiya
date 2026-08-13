# ruff: noqa: E501
from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic import command
from app.audit.models import AuditLog
from app.debt.models import Debt
from app.idempotency.models import IdempotencyKey
from app.rating.models import RatingEvent
from tests.postgresql import cleanup_m2_tables

ROOT = Path(__file__).resolve().parents[1]
M16 = "c7d8e9f0a1b2"
M17 = "d8e9f0a1b2c3"
CREATED = datetime(2026, 8, 1, tzinfo=UTC)
ACCEPTED = datetime(2026, 8, 2, tzinfo=UTC)
OVERDUE = datetime(2026, 8, 6, tzinfo=UTC)


def _config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


@pytest.fixture
def m16_database(m2_test_database: Engine) -> Generator[Engine, None, None]:
    command.downgrade(_config(), M16)
    try:
        yield m2_test_database
    finally:
        if _revision(m2_test_database) == M17:
            cleanup_m2_tables(m2_test_database)
            command.downgrade(_config(), M16)
        cleanup_m2_tables(m2_test_database)
        command.upgrade(_config(), M17)


def _seed_m16_overdue(engine: Engine) -> dict[str, object]:
    actor_id = uuid4()
    customer_user_id = uuid4()
    customer_id = uuid4()
    shop_id = uuid4()
    relation_id = uuid4()
    debt_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id,phone,is_active,is_platform_admin,created_at,updated_at) VALUES (:actor,:actor_phone,true,true,:created,:created),(:customer_user,:customer_phone,true,false,:created,:created)"
            ),
            {
                "actor": actor_id,
                "actor_phone": f"+998{actor_id.int % 1_000_000_000:09d}",
                "customer_user": customer_user_id,
                "customer_phone": f"+998{customer_user_id.int % 1_000_000_000:09d}",
                "created": CREATED,
            },
        )
        connection.execute(
            text(
                "INSERT INTO customers (id,user_id,onboarding_status,created_at,updated_at,activated_at) VALUES (:id,:user,'active',:created,:created,:created)"
            ),
            {"id": customer_id, "user": customer_user_id, "created": CREATED},
        )
        connection.execute(
            text(
                "INSERT INTO shops (id,name,phone,status,default_credit_limit_uzs,default_max_open_debts,created_at,updated_at) VALUES (:id,'M17 migration','+998900000001','active',1000000,2,:created,:created)"
            ),
            {"id": shop_id, "created": CREATED},
        )
        connection.execute(
            text(
                "INSERT INTO shop_customers (id,shop_id,customer_id,credit_limit_uzs,max_open_debts,list_status,revision,created_by_user_id,created_at,updated_at) VALUES (:id,:shop,:customer,1000000,2,'normal',1,:actor,:created,:created)"
            ),
            {
                "id": relation_id,
                "shop": shop_id,
                "customer": customer_id,
                "actor": actor_id,
                "created": CREATED,
            },
        )
        connection.execute(
            text(
                "INSERT INTO debts (id,shop_customer_id,created_by_user_id,original_amount_uzs,discount_basis_points,discounted_amount_uzs,due_date,pending_expires_at,status,revision,accepted_at,overdue_at,overdue_revision,created_at,updated_at) VALUES (:id,:relation,:actor,100000,1000,90000,:due,:expiry,'overdue',3,:accepted,:overdue,3,:created,:overdue)"
            ),
            {
                "id": debt_id,
                "relation": relation_id,
                "actor": actor_id,
                "due": date(2026, 8, 4),
                "expiry": CREATED + timedelta(hours=72),
                "accepted": ACCEPTED,
                "overdue": OVERDUE,
                "created": CREATED,
            },
        )
    return {
        "actor_id": actor_id,
        "relation_id": relation_id,
        "debt_id": debt_id,
    }


@pytest.mark.integration
def test_fresh_upgrade_empty_downgrade_and_reupgrade(
    m16_database: Engine,
) -> None:
    assert _revision(m16_database) == M16
    assert "written_off_at" not in {
        item["name"] for item in inspect(m16_database).get_columns("debts")
    }
    command.upgrade(_config(), M17)
    assert _revision(m16_database) == M17
    assert "written_off_at" in {
        item["name"] for item in inspect(m16_database).get_columns("debts")
    }
    command.downgrade(_config(), M16)
    assert _revision(m16_database) == M16
    command.upgrade(_config(), M17)
    assert _revision(m16_database) == M17


@pytest.mark.integration
def test_mixed_m16_upgrade_preserves_predecessor_columns_and_source_rows(
    m16_database: Engine,
) -> None:
    ids = _seed_m16_overdue(m16_database)
    with m16_database.connect() as connection:
        before = connection.execute(
            text(
                "SELECT id,shop_customer_id,created_by_user_id,original_amount_uzs,discount_basis_points,discounted_amount_uzs,due_date,pending_expires_at,status,revision,rejection_reason,cancellation_reason,accepted_at,rejected_at,cancelled_at,expired_at,paid_at,overdue_at,overdue_revision,created_at,updated_at FROM debts WHERE id=:id"
            ),
            {"id": ids["debt_id"]},
        ).one()
        counts_before = tuple(
            connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("payments", "rating_events", "audit_log", "idempotency_keys")
        )
    command.upgrade(_config(), M17)
    with m16_database.connect() as connection:
        after = connection.execute(
            text(
                "SELECT id,shop_customer_id,created_by_user_id,original_amount_uzs,discount_basis_points,discounted_amount_uzs,due_date,pending_expires_at,status,revision,rejection_reason,cancellation_reason,accepted_at,rejected_at,cancelled_at,expired_at,paid_at,overdue_at,overdue_revision,created_at,updated_at FROM debts WHERE id=:id"
            ),
            {"id": ids["debt_id"]},
        ).one()
        counts_after = tuple(
            connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("payments", "rating_events", "audit_log", "idempotency_keys")
        )
    assert tuple(after) == tuple(before)
    assert counts_after == counts_before


@pytest.mark.integration
def test_db_rejects_invalid_debt_rating_audit_and_idempotency_rows(
    m2_test_database: Engine,
) -> None:
    ids = _seed_m16_overdue(m2_test_database)
    actor_id = ids["actor_id"]
    debt_id = ids["debt_id"]
    relation_id = ids["relation_id"]
    with Session(m2_test_database) as session:
        invalid_debt_updates = (
            {"status": "written_off"},
            {"written_off_at": OVERDUE},
            {"written_off_reason": "other"},
            {"written_off_revision": 3},
            {"written_off_settled_at": OVERDUE},
        )
        for values in invalid_debt_updates:
            with pytest.raises(IntegrityError), session.begin_nested():
                session.query(Debt).filter(Debt.id == debt_id).update(values)
                session.flush()
            session.rollback()

        for event_type, delta, source in (
            ("written_off", -15, "live"),
            ("written_off", -40, "historical_reconciliation"),
            ("written_off_settled", 10, "historical_reconciliation"),
        ):
            with pytest.raises(IntegrityError), session.begin_nested():
                session.add(
                    RatingEvent(
                        id=uuid4(),
                        shop_customer_id=relation_id,
                        debt_id=debt_id,
                        event_type=event_type,
                        delta=delta,
                        occurred_at=OVERDUE,
                        business_date=date(2026, 8, 6),
                        recording_source=source,
                    )
                )
                session.flush()
            session.rollback()

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                AuditLog(
                    occurred_at=OVERDUE,
                    event_type="debt.written_off",
                    actor_kind="SYSTEM",
                    actor_user_id=None,
                    object_type="debt",
                    object_id=debt_id,
                    payload={
                        "reason_provided": "true",
                        "from_status": "overdue",
                        "to_status": "written_off",
                        "written_off_revision": 4,
                    },
                )
            )
            session.flush()
        session.rollback()

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                IdempotencyKey(
                    actor_user_id=actor_id,
                    endpoint="admin.debts.write_off",
                    key_digest="a" * 64,
                    request_hash="b" * 64,
                    result_object_type="payment",
                    result_object_id=debt_id,
                    created_at=OVERDUE,
                )
            )
            session.flush()
        session.rollback()


@pytest.mark.integration
@pytest.mark.parametrize("loss_class", ["debt", "rating", "audit", "key"])
def test_each_independent_m17_loss_class_denies_downgrade(
    m2_test_database: Engine, loss_class: str
) -> None:
    ids = _seed_m16_overdue(m2_test_database)
    actor_id = ids["actor_id"]
    debt_id = ids["debt_id"]
    relation_id = ids["relation_id"]
    with m2_test_database.begin() as connection:
        if loss_class == "debt":
            connection.execute(
                text(
                    "UPDATE debts SET status='written_off',revision=4,updated_at=:at,written_off_at=:at,written_off_revision=4,written_off_reason='collection_exhausted',written_off_actor_user_id=:actor WHERE id=:debt"
                ),
                {"at": OVERDUE, "actor": actor_id, "debt": debt_id},
            )
        elif loss_class == "rating":
            connection.execute(
                text(
                    "INSERT INTO rating_events (id,shop_customer_id,debt_id,event_type,delta,occurred_at,business_date,recording_source) VALUES (:id,:relation,:debt,'written_off',-40,:at,:day,'live')"
                ),
                {
                    "id": uuid4(),
                    "relation": relation_id,
                    "debt": debt_id,
                    "at": OVERDUE,
                    "day": date(2026, 8, 6),
                },
            )
        elif loss_class == "audit":
            connection.execute(
                text(
                    "INSERT INTO audit_log (id,occurred_at,event_type,actor_kind,actor_user_id,object_type,object_id,payload) VALUES (:id,:at,'debt.written_off','USER',:actor,'debt',:debt,CAST(:payload AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "at": OVERDUE,
                    "actor": actor_id,
                    "debt": debt_id,
                    "payload": '{"reason_provided":true,"from_status":"overdue","to_status":"written_off","written_off_revision":4}',
                },
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO idempotency_keys (id,actor_user_id,endpoint,key_digest,request_hash,result_object_type,result_object_id,created_at) VALUES (:id,:actor,'admin.debts.write_off',:digest,:hash,'debt',:debt,:at)"
                ),
                {
                    "id": uuid4(),
                    "actor": actor_id,
                    "digest": uuid4().hex * 2,
                    "hash": uuid4().hex * 2,
                    "debt": debt_id,
                    "at": OVERDUE,
                },
            )
    with pytest.raises(Exception, match="M17 downgrade blocked"):
        command.downgrade(_config(), M16)
    assert _revision(m2_test_database) == M17
