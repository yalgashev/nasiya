from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from alembic import command
from tests import test_customer_document_concurrency_postgresql as concurrency_tests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M9_REVISION = "a9b0c1d2e3f4"
M10_REVISION = "b0c1d2e3f4a5"
NOW = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)
RACE_PHONE = "+998900001202"


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert isinstance(revision, str)
    return revision


def _remove_compensation_race_rows(engine: Engine) -> None:
    with engine.begin() as connection:
        user_id = connection.scalar(
            text("SELECT id FROM users WHERE phone = :phone"),
            {"phone": RACE_PHONE},
        )
        assert user_id is not None
        connection.execute(
            text("DELETE FROM customer_documents WHERE attached_by_user_id = :id"),
            {"id": user_id},
        )
        connection.execute(
            text("DELETE FROM object_files WHERE created_by_user_id = :id"),
            {"id": user_id},
        )
        connection.execute(
            text("DELETE FROM customers WHERE user_id = :id"),
            {"id": user_id},
        )
        connection.execute(
            text("DELETE FROM users WHERE id = :id"),
            {"id": user_id},
        )


def test_m10_revision_is_single_linear_child_of_m9_head() -> None:
    scripts = ScriptDirectory.from_config(_config())
    revision = scripts.get_revision(M10_REVISION)
    assert revision is not None
    assert revision.down_revision == M9_REVISION
    assert M10_REVISION in {
        migration.revision for migration in scripts.walk_revisions()
    }


@pytest.mark.integration
def test_m10_schema_has_exact_two_tables_columns_constraints_and_indexes(
    m2_test_database: Engine,
) -> None:
    config = _config()
    try:
        command.downgrade(config, M10_REVISION)
        inspector = inspect(m2_test_database)

        assert _current_revision(m2_test_database) == M10_REVISION
        assert {"customer_identities", "customer_documents"} <= set(
            inspector.get_table_names()
        )
        assert {column["name"] for column in inspector.get_columns("customers")} == {
            "id",
            "user_id",
            "onboarding_status",
            "created_at",
            "updated_at",
        }
        assert {
            column["name"] for column in inspector.get_columns("customer_identities")
        } == {
            "customer_id",
            "ciphertext",
            "nonce",
            "key_id",
            "schema_version",
            "jshshir_blind_index",
            "revision",
            "created_at",
            "updated_at",
        }
        assert {
            column["name"] for column in inspector.get_columns("customer_documents")
        } == {
            "id",
            "customer_id",
            "object_file_id",
            "submission_id",
            "status",
            "attached_by_user_id",
            "attached_at",
            "superseded_by_document_id",
            "superseded_at",
        }

        identity_checks = {
            check["name"]
            for check in inspector.get_check_constraints("customer_identities")
        }
        assert identity_checks == {
            "ck_customer_identities_ciphertext_minimum_length",
            "ck_customer_identities_nonce_length",
            "ck_customer_identities_key_id_format",
            "ck_customer_identities_schema_version_supported",
            "ck_customer_identities_blind_index_length",
            "ck_customer_identities_revision_positive",
            "ck_customer_identities_timestamp_order",
        }
        document_checks = {
            check["name"]
            for check in inspector.get_check_constraints("customer_documents")
        }
        assert document_checks == {
            "ck_customer_documents_status_allowed",
            "ck_customer_documents_supersede_metadata_matches_status",
            "ck_customer_documents_no_self_replacement",
            "ck_customer_documents_timestamp_order",
        }
        current_index = {
            index["name"]: index
            for index in inspector.get_indexes("customer_documents")
            if "duplicates_constraint" not in index
        }["uq_customer_documents_current_customer_id"]
        assert current_index["unique"] is True
        assert current_index["column_names"] == ["customer_id"]
        assert current_index["dialect_options"]["postgresql_where"] == (
            "((status)::text = 'CURRENT'::text)"
        )
        for table_name in ("customer_identities", "customer_documents"):
            assert all(
                foreign_key["options"]["ondelete"] == "RESTRICT"
                for foreign_key in inspector.get_foreign_keys(table_name)
            )

        audit_checks = {
            check["name"]: check["sqltext"]
            for check in inspector.get_check_constraints("audit_log")
        }
        assert set(audit_checks) == {
            "ck_audit_log_event_type_allowed",
            "ck_audit_log_actor_kind_allowed",
            "ck_audit_log_object_type_allowed",
            "ck_audit_log_actor_matches_event",
            "ck_audit_log_object_matches_event",
            "ck_audit_log_payload_exact_shape",
        }
        assert (
            "customer.identity_saved" in audit_checks["ck_audit_log_event_type_allowed"]
        )
        assert "customer_document" in audit_checks["ck_audit_log_object_type_allowed"]
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_m9_m10_m9_m10_walk_preserves_inherited_data(
    m2_test_database: Engine,
) -> None:
    config = _config()
    user_id = uuid4()
    customer_id = uuid4()
    try:
        command.downgrade(config, M9_REVISION)
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, phone, password_hash, is_active, is_platform_admin, "
                    "created_at, updated_at) "
                    "VALUES (:id, :phone, NULL, true, false, :now, :now)"
                ),
                {"id": user_id, "phone": "+998900001203", "now": NOW},
            )
            connection.execute(
                text(
                    "INSERT INTO customers "
                    "(id, user_id, onboarding_status, created_at, updated_at) "
                    "VALUES (:id, :user_id, 'draft', :now, :now)"
                ),
                {
                    "id": customer_id,
                    "user_id": user_id,
                    "now": NOW,
                },
            )

        command.upgrade(config, M10_REVISION)
        assert _current_revision(m2_test_database) == M10_REVISION
        command.upgrade(config, "head")
        concurrency_tests.test_attach_winner_serializes_compensation_to_noop(
            m2_test_database
        )
        _remove_compensation_race_rows(m2_test_database)
        command.downgrade(config, M9_REVISION)
        downgraded_inspector = inspect(m2_test_database)
        assert "customer_identities" not in downgraded_inspector.get_table_names()
        assert "customer_documents" not in downgraded_inspector.get_table_names()
        downgraded_audit_checks = " ".join(
            check["sqltext"]
            for check in downgraded_inspector.get_check_constraints("audit_log")
        )
        assert "customer.identity_saved" not in downgraded_audit_checks
        assert "customer.document_attached" not in downgraded_audit_checks
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT phone FROM users WHERE id = :id"),
                    {"id": user_id},
                )
                == "+998900001203"
            )
            assert (
                connection.scalar(
                    text("SELECT onboarding_status FROM customers WHERE id = :id"),
                    {"id": customer_id},
                )
                == "draft"
            )
        command.upgrade(config, M10_REVISION)
        assert _current_revision(m2_test_database) == M10_REVISION
        command.upgrade(config, "head")
        concurrency_tests.test_compensation_winner_blocks_attachment_with_zero_write(
            m2_test_database
        )
        _remove_compensation_race_rows(m2_test_database)
        assert _current_revision(m2_test_database) == "f4a5b6c7d8e"
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM users WHERE id = :id"),
                    {"id": user_id},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM customers WHERE id = :id"),
                    {"id": customer_id},
                )
                == 1
            )
    finally:
        command.upgrade(config, "head")
