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

from alembic import command
from tests.postgresql import cleanup_m2_tables
from tests.test_m17_migration_postgresql import (
    OVERDUE,
    _seed_m16_overdue,
)

ROOT = Path(__file__).resolve().parents[1]
M17 = "d8e9f0a1b2c3"
M18 = "e9f0a1b2c3d4"


def _config() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT version_num FROM alembic_version"))


@pytest.fixture
def m17_database(m2_test_database: Engine) -> Generator[Engine, None, None]:
    cleanup_m2_tables(m2_test_database)
    command.downgrade(_config(), M17)
    try:
        yield m2_test_database
    finally:
        cleanup_m2_tables(m2_test_database)
        if _revision(m2_test_database) == M17:
            command.upgrade(_config(), M18)


def _seed_coherent_overdue_source(engine: Engine, *, duplicate_audit: bool = False):
    ids = _seed_m16_overdue(engine)
    payloads = (
        (
            "debt.overdue",
            '{"source":"batch","from_status":"active","to_status":"overdue","overdue_revision":3,"business_date":"2026-08-06"}',
        ),
        (
            "debt.clawback_applied",
            '{"source":"batch","from_basis":"discounted","to_basis":"original","balance_increase_uzs":10000,"overdue_revision":3}',
        ),
    )
    event_id = uuid4()
    with engine.begin() as connection:
        for event_type, payload in payloads:
            repeats = 2 if duplicate_audit and event_type == "debt.overdue" else 1
            for _ in range(repeats):
                connection.execute(
                    text(
                        "INSERT INTO audit_log (id,occurred_at,event_type,actor_kind,actor_user_id,object_type,object_id,payload) VALUES (:id,:at,:event,'SYSTEM',NULL,'debt',:debt,CAST(:payload AS jsonb))"
                    ),
                    {
                        "id": uuid4(),
                        "at": OVERDUE,
                        "event": event_type,
                        "debt": ids["debt_id"],
                        "payload": payload,
                    },
                )
        connection.execute(
            text(
                "INSERT INTO rating_events (id,shop_customer_id,debt_id,event_type,delta,occurred_at,business_date,recording_source) VALUES (:id,:relation,:debt,'overdue',-15,:at,:day,'live')"
            ),
            {
                "id": event_id,
                "relation": ids["relation_id"],
                "debt": ids["debt_id"],
                "at": OVERDUE,
                "day": date(2026, 8, 6),
            },
        )
    return ids, event_id


@pytest.mark.integration
def test_fresh_upgrade_empty_downgrade_and_reupgrade(
    m17_database: Engine,
) -> None:
    assert _revision(m17_database) == M17
    command.upgrade(_config(), M18)
    schema = inspect(m17_database)
    assert "payment_voids" in schema.get_table_names()
    assert "source_revision" in {
        column["name"] for column in schema.get_columns("rating_events")
    }
    command.downgrade(_config(), M17)
    assert "payment_voids" not in inspect(m17_database).get_table_names()
    command.upgrade(_config(), M18)
    assert _revision(m17_database) == M18


@pytest.mark.integration
def test_m17_fixture_backfill_preserves_all_old_business_values(
    m17_database: Engine,
) -> None:
    _ids, event_id = _seed_coherent_overdue_source(m17_database)
    with m17_database.connect() as connection:
        before = connection.execute(
            text(
                "SELECT id,shop_customer_id,debt_id,event_type,delta,occurred_at,business_date,recording_source FROM rating_events WHERE id=:id"
            ),
            {"id": event_id},
        ).one()
        source_counts_before = tuple(
            connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("debts", "payments", "audit_log", "idempotency_keys")
        )
    command.upgrade(_config(), M18)
    with m17_database.connect() as connection:
        after = connection.execute(
            text(
                "SELECT id,shop_customer_id,debt_id,event_type,delta,occurred_at,business_date,recording_source,source_revision FROM rating_events WHERE id=:id"
            ),
            {"id": event_id},
        ).one()
        source_counts_after = tuple(
            connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("debts", "payments", "audit_log", "idempotency_keys")
        )
        assert connection.scalar(text("SELECT count(*) FROM payment_voids")) == 0
    assert tuple(after[:-1]) == tuple(before)
    assert after.source_revision == 3
    assert source_counts_after == source_counts_before


@pytest.mark.integration
def test_missing_or_multiple_source_audit_aborts_upgrade(
    m17_database: Engine,
) -> None:
    _seed_coherent_overdue_source(m17_database, duplicate_audit=True)
    with pytest.raises(
        RuntimeError,
        match="M18 source revision backfill blocked: missing or ambiguous source",
    ):
        command.upgrade(_config(), M18)
    assert _revision(m17_database) == M17
    assert "source_revision" not in {
        column["name"] for column in inspect(m17_database).get_columns("rating_events")
    }


@pytest.mark.integration
def test_compensation_fact_independently_denies_downgrade(
    m2_test_database: Engine,
) -> None:
    ids = _seed_m16_overdue(m2_test_database)
    with m2_test_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO rating_events (id,shop_customer_id,debt_id,event_type,delta,occurred_at,business_date,recording_source,source_revision) VALUES (:id,:relation,:debt,'on_time_paid_voided',-5,:at,:day,'live',3)"
            ),
            {
                "id": uuid4(),
                "relation": ids["relation_id"],
                "debt": ids["debt_id"],
                "at": OVERDUE,
                "day": date(2026, 8, 6),
            },
        )
    with pytest.raises(
        RuntimeError, match="M18 downgrade blocked: compensation rating history exists"
    ):
        command.downgrade(_config(), M17)
    assert _revision(m2_test_database) == M18


@pytest.mark.integration
@pytest.mark.parametrize(
    ("fact_class", "expected_message"),
    (
        ("payment_void", "PaymentVoid history exists"),
        ("rating_cycle", "predecessor rating uniqueness cannot represent cycles"),
        ("void_audit", "payment void audit history exists"),
        ("void_key", "payment void idempotency history exists"),
        ("void_overdue_audit", "payment void overdue audit history exists"),
        ("source_revision", "source revision is not reconstructible"),
    ),
)
def test_each_remaining_m18_fact_class_independently_denies_downgrade(
    m2_test_database: Engine,
    fact_class: str,
    expected_message: str,
) -> None:
    ids = _seed_m16_overdue(m2_test_database)
    occurred_at = datetime(2026, 8, 7, 12, tzinfo=UTC)
    with m2_test_database.begin() as connection:
        if fact_class == "payment_void":
            payment_id = uuid4()
            connection.execute(
                text(
                    "INSERT INTO payments (id,debt_id,recorded_by_user_id,amount_uzs,method,debt_revision_after,created_at) VALUES (:id,:debt,:actor,1000,'cash',4,:at)"
                ),
                {
                    "id": payment_id,
                    "debt": ids["debt_id"],
                    "actor": ids["actor_id"],
                    "at": occurred_at,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO payment_voids (id,payment_id,debt_id,shop_customer_id,source_payment_revision,debt_revision_after,voided_by_user_id,reason,voided_at) VALUES (:id,:payment,:debt,:relation,4,5,:actor,'incorrect_amount',:at)"
                ),
                {
                    "id": uuid4(),
                    "payment": payment_id,
                    "debt": ids["debt_id"],
                    "relation": ids["relation_id"],
                    "actor": ids["actor_id"],
                    "at": occurred_at + timedelta(minutes=1),
                },
            )
        elif fact_class == "rating_cycle":
            for revision, minute in ((4, 0), (5, 1)):
                connection.execute(
                    text(
                        "INSERT INTO rating_events (id,shop_customer_id,debt_id,event_type,delta,occurred_at,business_date,recording_source,source_revision) VALUES (:id,:relation,:debt,'written_off_settled',10,:at,:day,'live',:revision)"
                    ),
                    {
                        "id": uuid4(),
                        "relation": ids["relation_id"],
                        "debt": ids["debt_id"],
                        "at": occurred_at + timedelta(minutes=minute),
                        "day": occurred_at.date(),
                        "revision": revision,
                    },
                )
        elif fact_class == "void_audit":
            connection.execute(
                text(
                    "INSERT INTO audit_log (id,occurred_at,event_type,actor_kind,actor_user_id,object_type,object_id,payload) VALUES (:id,:at,'payment.voided','USER',:actor,'payment',:object,CAST(:payload AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "at": occurred_at,
                    "actor": ids["actor_id"],
                    "object": uuid4(),
                    "payload": '{"reason":"incorrect_amount","from_status":"overdue","to_status":"overdue","debt_revision_after":4}',
                },
            )
        elif fact_class == "void_key":
            connection.execute(
                text(
                    "INSERT INTO idempotency_keys (id,actor_user_id,endpoint,key_digest,request_hash,result_object_type,result_object_id,created_at) VALUES (:id,:actor,'shop.payments.void',:digest,:request_hash,'payment',:result,:at)"
                ),
                {
                    "id": uuid4(),
                    "actor": ids["actor_id"],
                    "digest": "a" * 64,
                    "request_hash": "b" * 64,
                    "result": uuid4(),
                    "at": occurred_at,
                },
            )
        elif fact_class == "void_overdue_audit":
            connection.execute(
                text(
                    "INSERT INTO audit_log (id,occurred_at,event_type,actor_kind,actor_user_id,object_type,object_id,payload) VALUES (:id,:at,'debt.overdue','SYSTEM',NULL,'debt',:debt,CAST(:payload AS jsonb))"
                ),
                {
                    "id": uuid4(),
                    "at": occurred_at,
                    "debt": ids["debt_id"],
                    "payload": '{"source":"payment_void","from_status":"paid","to_status":"overdue","overdue_revision":4,"business_date":"2026-08-07"}',
                },
            )
        else:
            for event_type, payload in (
                (
                    "debt.overdue",
                    '{"source":"batch","from_status":"active","to_status":"overdue","overdue_revision":3,"business_date":"2026-08-06"}',
                ),
                (
                    "debt.clawback_applied",
                    '{"source":"batch","from_basis":"discounted","to_basis":"original","balance_increase_uzs":10000,"overdue_revision":3}',
                ),
            ):
                connection.execute(
                    text(
                        "INSERT INTO audit_log (id,occurred_at,event_type,actor_kind,actor_user_id,object_type,object_id,payload) VALUES (:id,:at,:event,'SYSTEM',NULL,'debt',:debt,CAST(:payload AS jsonb))"
                    ),
                    {
                        "id": uuid4(),
                        "at": OVERDUE,
                        "event": event_type,
                        "debt": ids["debt_id"],
                        "payload": payload,
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO rating_events (id,shop_customer_id,debt_id,event_type,delta,occurred_at,business_date,recording_source,source_revision) VALUES (:id,:relation,:debt,'overdue',-15,:at,:day,'live',99)"
                ),
                {
                    "id": uuid4(),
                    "relation": ids["relation_id"],
                    "debt": ids["debt_id"],
                    "at": OVERDUE,
                    "day": date(2026, 8, 6),
                },
            )

    with pytest.raises(RuntimeError, match=expected_message):
        command.downgrade(_config(), M17)
    assert _revision(m2_test_database) == M18
