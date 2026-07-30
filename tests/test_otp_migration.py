import ast
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M7_REVISION = "e7f8a9b0c1d2"
M6_REVISION = "d4e5f6a7b8c9"
MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "e7f8a9b0c1d2_create_m7_otp_persistence_tables.py"
)
M7_TABLES = {
    "otp_challenges",
    "otp_dispatches",
    "otp_challenge_events",
    "otp_dispatcher_state",
}
M1_M6_TABLES = {
    "users",
    "sessions",
    "auth_rate_limits",
    "shops",
    "shop_staff",
    "shop_status_events",
    "shop_staff_events",
    "customers",
    "telegram_links",
    "telegram_link_tokens",
    "telegram_link_events",
    "telegram_polling_state",
    "telegram_update_failures",
}


def test_otp_migration_is_single_linear_child_of_verified_m6_head() -> None:
    script = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    revision = script.get_revision(M7_REVISION)

    assert revision is not None
    assert revision.down_revision == M6_REVISION


def test_otp_migration_imports_and_has_only_upgrade_downgrade_entrypoints() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    function_names = {
        node.name for node in module.body if isinstance(node, ast.FunctionDef)
    }

    assert function_names == {"upgrade", "downgrade"}
    assert "create_all" not in source
    assert "DROP DATABASE" not in source
    assert "down -v" not in source
    assert "telegram_bot_token" not in source


@pytest.mark.integration
def test_otp_migration_upgrades_from_m6_without_mutating_existing_schema(
    test_database_engine: Engine,
) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    command.downgrade(config, M6_REVISION)
    inspector = inspect(test_database_engine)
    m6_tables = set(inspector.get_table_names())
    m6_columns = {
        table_name: [column["name"] for column in inspector.get_columns(table_name)]
        for table_name in M1_M6_TABLES
        if table_name in m6_tables
    }
    assert M7_TABLES.isdisjoint(m6_tables)

    command.upgrade(config, M7_REVISION)
    inspector = inspect(test_database_engine)
    m7_tables = set(inspector.get_table_names())

    assert M7_TABLES.issubset(m7_tables)
    assert {name for name in m7_tables if name.startswith("otp_")} == M7_TABLES
    for table_name, columns in m6_columns.items():
        assert [
            column["name"] for column in inspector.get_columns(table_name)
        ] == columns


@pytest.mark.integration
def test_otp_migration_downgrade_and_empty_database_upgrade_real_postgresql(
    test_database_engine: Engine,
) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))

    try:
        command.downgrade(config, M6_REVISION)
        assert M7_TABLES.isdisjoint(inspect(test_database_engine).get_table_names())

        command.upgrade(config, M7_REVISION)
        assert M7_TABLES.issubset(inspect(test_database_engine).get_table_names())

        command.downgrade(config, "base")
        non_alembic_tables = {
            table_name
            for table_name in inspect(test_database_engine).get_table_names()
            if table_name != "alembic_version"
        }
        assert not non_alembic_tables

        command.upgrade(config, M7_REVISION)
        tables_after_empty_upgrade = set(
            inspect(test_database_engine).get_table_names()
        )
        assert M1_M6_TABLES.issubset(tables_after_empty_upgrade)
        assert M7_TABLES.issubset(tables_after_empty_upgrade)
    finally:
        command.upgrade(config, M7_REVISION)


@pytest.mark.integration
def test_otp_migration_database_constraints_indexes_and_forbidden_columns(
    test_database_engine: Engine,
) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(config, M7_REVISION)
    inspector = inspect(test_database_engine)

    assert {
        table_name
        for table_name in inspector.get_table_names()
        if table_name.startswith("otp_")
    } == M7_TABLES

    challenge_fks = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("otp_challenges")
    }
    assert challenge_fks["fk_otp_challenges_user_id_users_id"]["referred_table"] == (
        "users"
    )
    assert (
        challenge_fks["fk_otp_challenges_user_id_users_id"]["options"]["ondelete"]
        == "RESTRICT"
    )
    assert (
        challenge_fks["fk_otp_challenges_telegram_link_id_telegram_links_id"][
            "referred_table"
        ]
        == "telegram_links"
    )
    assert (
        challenge_fks["fk_otp_challenges_telegram_link_id_telegram_links_id"][
            "options"
        ]["ondelete"]
        == "RESTRICT"
    )

    dispatch_fks = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("otp_dispatches")
    }
    assert (
        dispatch_fks["fk_otp_dispatches_challenge_id_otp_challenges_id"][
            "referred_table"
        ]
        == "otp_challenges"
    )
    assert (
        dispatch_fks["fk_otp_dispatches_challenge_id_otp_challenges_id"]["options"][
            "ondelete"
        ]
        == "RESTRICT"
    )

    event_fks = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("otp_challenge_events")
    }
    assert set(event_fks) == {"fk_otp_challenge_events_user_id_users_id"}
    assert event_fks["fk_otp_challenge_events_user_id_users_id"]["referred_table"] == (
        "users"
    )
    assert (
        event_fks["fk_otp_challenge_events_user_id_users_id"]["options"]["ondelete"]
        == "RESTRICT"
    )

    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("otp_challenges")
    } == {
        "ck_otp_challenges_purpose_login",
        "ck_otp_challenges_browser_binding_digest_hmac_sha256_hex",
        "ck_otp_challenges_code_mac_hmac_sha256_hex",
        "ck_otp_challenges_status_allowed",
        "ck_otp_challenges_failed_attempts_cap",
        "ck_otp_challenges_real_identity_consistent",
        "ck_otp_challenges_pending_dispatch_state",
        "ck_otp_challenges_active_state",
        "ck_otp_challenges_terminal_state",
        "ck_otp_challenges_timestamp_order",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("otp_dispatches")
    } == {
        "ck_otp_dispatches_status_allowed",
        "ck_otp_dispatches_locale_allowed",
        "ck_otp_dispatches_failure_code_format",
        "ck_otp_dispatches_state_consistent",
        "ck_otp_dispatches_timestamp_order",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("otp_challenge_events")
    } == {
        "ck_otp_challenge_events_action_allowed",
        "ck_otp_challenge_events_safe_code_format",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("otp_dispatcher_state")
    } == {
        "ck_otp_dispatcher_state_singleton",
        "ck_otp_dispatcher_state_ready_requires_heartbeat",
        "ck_otp_dispatcher_state_heartbeat_not_before_ready",
    }

    assert {index["name"] for index in inspector.get_indexes("otp_challenges")} >= {
        "uq_otp_challenges_one_outstanding_per_user_purpose",
        "uq_otp_challenges_one_outstanding_per_browser_purpose",
        "ix_otp_challenges_terminal_at",
    }
    assert {index["name"] for index in inspector.get_indexes("otp_dispatches")} >= {
        "ix_otp_dispatches_status_created_at",
        "ix_otp_dispatches_terminal_at",
    }
    assert {
        index["name"] for index in inspector.get_indexes("otp_challenge_events")
    } >= {
        "ix_otp_challenge_events_challenge_id_occurred_at",
        "ix_otp_challenge_events_occurred_at",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("otp_dispatches")
    } == {"uq_otp_dispatches_challenge_id"}

    forbidden_columns = {
        "raw_otp",
        "otp",
        "code",
        "raw_code",
        "phone",
        "ip",
        "client_ip",
        "telegram_chat_id",
        "chat_id",
        "message",
        "message_text",
        "payload",
        "session_cookie",
        "cookie",
        "bot_token",
        "secret",
        "token",
        "metadata",
        "json",
        "outbox_id",
        "job_id",
        "scheduler_id",
        "worker_id",
        "deleted_at",
    }
    for table_name in M7_TABLES:
        assert forbidden_columns.isdisjoint(
            {column["name"] for column in inspector.get_columns(table_name)}
        )
