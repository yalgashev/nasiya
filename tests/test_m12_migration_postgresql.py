import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M11_REVISION = "d2e3f4a5b6c7"
M12_REVISION = "e3f4a5b6c7d8"
M13_REVISION = "f4a5b6c7d8e"
M14_REVISION = "a5b6c7d8e9f0"
NOW = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert isinstance(revision, str)
    return revision


def _check_sql(engine: Engine, table_name: str) -> dict[str, str]:
    return {
        check["name"]: check["sqltext"]
        for check in inspect(engine).get_check_constraints(table_name)
    }


def test_m12_revision_is_the_single_linear_child_of_m11() -> None:
    scripts = ScriptDirectory.from_config(_config())
    revision = scripts.get_revision(M12_REVISION)

    assert scripts.get_heads() == [M14_REVISION]
    assert revision is not None
    assert revision.down_revision == M11_REVISION


@pytest.mark.integration
def test_m12_full_walk_backfills_defaults_and_cleanly_round_trips(
    m2_test_database: Engine,
) -> None:
    config = _config()
    shop_id = uuid4()
    try:
        command.downgrade(config, "base")
        assert set(inspect(m2_test_database).get_table_names()) <= {"alembic_version"}

        command.upgrade(config, M11_REVISION)
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO shops "
                    "(id, name, phone, address_text, status, created_at, updated_at) "
                    "VALUES (:id, 'M12 preexisting shop', '+998900001401', NULL, "
                    "'active', :now, :now)"
                ),
                {"id": shop_id, "now": NOW},
            )

        command.upgrade(config, M12_REVISION)
        inspector = inspect(m2_test_database)
        assert _current_revision(m2_test_database) == M12_REVISION
        assert "shop_customers" in set(inspector.get_table_names())
        assert {column["name"] for column in inspector.get_columns("shops")} == {
            "id",
            "name",
            "phone",
            "address_text",
            "status",
            "default_credit_limit_uzs",
            "default_max_open_debts",
            "created_at",
            "updated_at",
        }
        with m2_test_database.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT default_credit_limit_uzs, default_max_open_debts "
                    "FROM shops WHERE id = :id"
                ),
                {"id": shop_id},
            ).one() == (1_000_000, 2)
            assert connection.scalar(text("SELECT count(*) FROM shop_customers")) == 0

        audit_checks = _check_sql(m2_test_database, "audit_log")
        assert "shop_customer.linked" in audit_checks["ck_audit_log_event_type_allowed"]
        assert (
            "shop_customer.policy_updated"
            in audit_checks["ck_audit_log_payload_exact_shape"]
        )
        assert (
            "shop.customer_defaults_updated"
            in audit_checks["ck_audit_log_object_matches_event"]
        )

        command.downgrade(config, M11_REVISION)
        downgraded = inspect(m2_test_database)
        assert _current_revision(m2_test_database) == M11_REVISION
        assert "shop_customers" not in set(downgraded.get_table_names())
        assert {
            "default_credit_limit_uzs",
            "default_max_open_debts",
        }.isdisjoint({column["name"] for column in downgraded.get_columns("shops")})
        assert (
            "shop_customer.linked"
            not in _check_sql(m2_test_database, "audit_log")[
                "ck_audit_log_event_type_allowed"
            ]
        )

        command.upgrade(config, "head")
        assert _current_revision(m2_test_database) == M14_REVISION
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_m12_downgrade_guards_fail_before_destructive_ddl(
    m2_test_database: Engine,
) -> None:
    config = _config()
    user_id = uuid4()
    customer_id = uuid4()
    shop_id = uuid4()
    shop_customer_id = uuid4()
    audit_id = uuid4()
    try:
        command.downgrade(config, M12_REVISION)
        command.upgrade(config, M12_REVISION)
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, phone, password_hash, is_active, is_platform_admin, "
                    "created_at, updated_at) "
                    "VALUES (:id, '+998900001402', NULL, true, false, :now, :now)"
                ),
                {"id": user_id, "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO customers "
                    "(id, user_id, onboarding_status, created_at, updated_at, "
                    "activated_at) VALUES (:id, :user_id, 'active', :now, :now, :now)"
                ),
                {"id": customer_id, "user_id": user_id, "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO shops "
                    "(id, name, phone, address_text, status, created_at, updated_at) "
                    "VALUES (:id, 'M12 guard shop', '+998900001403', NULL, "
                    "'active', :now, :now)"
                ),
                {"id": shop_id, "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO shop_customers "
                    "(id, shop_id, customer_id, credit_limit_uzs, max_open_debts, "
                    "list_status, revision, created_by_user_id, created_at, "
                    "updated_at) "
                    "VALUES (:id, :shop_id, :customer_id, 1000000, 2, 'normal', "
                    "1, :user_id, :now, :now)"
                ),
                {
                    "id": shop_customer_id,
                    "shop_id": shop_id,
                    "customer_id": customer_id,
                    "user_id": user_id,
                    "now": NOW,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="M12 downgrade blocked: shop customer state exists",
        ):
            command.downgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M12_REVISION
        assert "shop_customers" in set(inspect(m2_test_database).get_table_names())

        with m2_test_database.begin() as connection:
            connection.execute(text("DELETE FROM shop_customers"))
            connection.execute(
                text(
                    "UPDATE shops SET default_credit_limit_uzs = 900000 WHERE id = :id"
                ),
                {"id": shop_id},
            )
        with pytest.raises(
            RuntimeError,
            match="M12 downgrade blocked: shop defaults changed",
        ):
            command.downgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M12_REVISION

        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "UPDATE shops SET default_credit_limit_uzs = 1000000 WHERE id = :id"
                ),
                {"id": shop_id},
            )
            connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, occurred_at, event_type, actor_kind, actor_user_id, "
                    "object_type, object_id, payload) VALUES "
                    "(:id, :now, 'shop.customer_defaults_updated', 'USER', "
                    ":user_id, 'shop', :shop_id, CAST(:payload AS jsonb))"
                ),
                {
                    "id": audit_id,
                    "now": NOW,
                    "user_id": user_id,
                    "shop_id": shop_id,
                    "payload": json.dumps(
                        {
                            "old_default_credit_limit_uzs": 1_000_000,
                            "new_default_credit_limit_uzs": 900_000,
                            "old_default_max_open_debts": 2,
                            "new_default_max_open_debts": 3,
                        }
                    ),
                },
            )
        with pytest.raises(
            RuntimeError,
            match="M12 downgrade blocked: M12 audit history exists",
        ):
            command.downgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M12_REVISION

        with m2_test_database.begin() as connection:
            connection.execute(
                text("DELETE FROM audit_log WHERE id = :id"),
                {"id": audit_id},
            )
        command.downgrade(config, M11_REVISION)
        assert _current_revision(m2_test_database) == M11_REVISION
        command.upgrade(config, M12_REVISION)
    finally:
        command.upgrade(config, "head")
