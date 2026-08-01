from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M8_REVISION = "f8a9b0c1d2e3"
M9_REVISION = "a9b0c1d2e3f4"
M8_TABLES = {
    "alembic_version",
    "auth_rate_limits",
    "customers",
    "object_files",
    "otp_challenge_events",
    "otp_challenges",
    "otp_dispatcher_state",
    "otp_dispatches",
    "sessions",
    "shop_staff",
    "shop_staff_events",
    "shop_status_events",
    "shops",
    "telegram_link_events",
    "telegram_link_tokens",
    "telegram_links",
    "telegram_polling_state",
    "telegram_update_failures",
    "users",
}
M9_TABLES = {
    "audit_log",
    "offer_acceptances",
    "offer_texts",
    "offer_versions",
}
NOW = datetime(2026, 7, 31, 14, 0, tzinfo=UTC)


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert isinstance(revision, str)
    return revision


@pytest.mark.integration
def test_empty_database_m8_m9_m8_m9_walk_has_one_linear_head(
    m2_test_database: Engine,
) -> None:
    config = _config()
    try:
        command.downgrade(config, "base")
        assert set(inspect(m2_test_database).get_table_names()) <= {"alembic_version"}

        command.upgrade(config, M9_REVISION)
        assert _current_revision(m2_test_database) == M9_REVISION
        assert set(inspect(m2_test_database).get_table_names()) == (
            M8_TABLES | M9_TABLES
        )

        command.downgrade(config, M8_REVISION)
        inspector = inspect(m2_test_database)
        assert _current_revision(m2_test_database) == M8_REVISION
        assert set(inspector.get_table_names()) == M8_TABLES
        assert "is_platform_admin" not in {
            column["name"] for column in inspector.get_columns("users")
        }

        command.upgrade(config, M9_REVISION)
        command.upgrade(config, M9_REVISION)
        command.upgrade(config, M9_REVISION)
        assert _current_revision(m2_test_database) == M9_REVISION
        assert set(inspect(m2_test_database).get_table_names()) == (
            M8_TABLES | M9_TABLES
        )
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_real_database_has_exact_m9_schema_objects(
    m2_test_database: Engine,
    request: pytest.FixtureRequest,
) -> None:
    request.addfinalizer(lambda: command.upgrade(_config(), "head"))
    command.downgrade(_config(), M9_REVISION)
    inspector = inspect(m2_test_database)

    assert _current_revision(m2_test_database) == M9_REVISION
    assert set(inspector.get_table_names()) == (M8_TABLES | M9_TABLES)
    assert {column["name"] for column in inspector.get_columns("offer_versions")} == {
        "id",
        "purpose",
        "version_number",
        "status",
        "created_by_user_id",
        "created_at",
        "legal_review_authority",
        "legal_reviewed_at",
        "legal_review_reference",
        "approved_by_user_id",
        "approved_at",
        "current_by_user_id",
        "current_at",
    }
    assert {column["name"] for column in inspector.get_columns("offer_texts")} == {
        "id",
        "offer_version_id",
        "language",
        "title",
        "body",
        "content_hash",
        "created_at",
        "updated_at",
    }
    assert {
        column["name"] for column in inspector.get_columns("offer_acceptances")
    } == {
        "id",
        "user_id",
        "offer_version_id",
        "offer_text_id",
        "purpose",
        "language",
        "version_number",
        "content_hash",
        "accepted_at",
        "user_agent",
    }
    assert {column["name"] for column in inspector.get_columns("audit_log")} == {
        "id",
        "occurred_at",
        "event_type",
        "actor_kind",
        "actor_user_id",
        "object_type",
        "object_id",
        "payload",
    }
    user_columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert set(user_columns) == {
        "id",
        "phone",
        "password_hash",
        "is_active",
        "is_platform_admin",
        "created_at",
        "updated_at",
    }
    assert user_columns["is_platform_admin"]["nullable"] is False
    assert str(user_columns["is_platform_admin"]["default"]).casefold() == "false"

    expected_unique_constraints = {
        "offer_versions": {
            "uq_offer_versions_purpose_version_number": (
                "purpose",
                "version_number",
            ),
        },
        "offer_texts": {
            "uq_offer_texts_offer_version_id_language": (
                "offer_version_id",
                "language",
            ),
        },
        "offer_acceptances": {
            "uq_offer_acceptances_user_id_offer_text_id_purpose": (
                "user_id",
                "offer_text_id",
                "purpose",
            ),
        },
        "audit_log": {},
    }
    expected_foreign_keys = {
        "offer_versions": {
            "fk_offer_versions_created_by_user_id_users_id": (
                ("created_by_user_id",),
                "users",
            ),
            "fk_offer_versions_approved_by_user_id_users_id": (
                ("approved_by_user_id",),
                "users",
            ),
            "fk_offer_versions_current_by_user_id_users_id": (
                ("current_by_user_id",),
                "users",
            ),
        },
        "offer_texts": {
            "fk_offer_texts_offer_version_id_offer_versions_id": (
                ("offer_version_id",),
                "offer_versions",
            ),
        },
        "offer_acceptances": {
            "fk_offer_acceptances_user_id_users_id": (("user_id",), "users"),
            "fk_offer_acceptances_offer_version_id_offer_versions_id": (
                ("offer_version_id",),
                "offer_versions",
            ),
            "fk_offer_acceptances_offer_text_id_offer_texts_id": (
                ("offer_text_id",),
                "offer_texts",
            ),
        },
        "audit_log": {
            "fk_audit_log_actor_user_id_users_id": (
                ("actor_user_id",),
                "users",
            ),
        },
    }
    for table_name, expected in expected_unique_constraints.items():
        assert {
            unique["name"]: tuple(unique["column_names"])
            for unique in inspector.get_unique_constraints(table_name)
        } == expected
        assert inspector.get_pk_constraint(table_name)["name"] == f"pk_{table_name}"
    for table_name, expected in expected_foreign_keys.items():
        actual = {
            foreign_key["name"]: (
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        }
        assert actual == expected
        assert all(
            foreign_key["options"]["ondelete"] == "RESTRICT"
            for foreign_key in inspector.get_foreign_keys(table_name)
        )

    indexes = {
        index["name"]: index
        for index in inspector.get_indexes("offer_versions")
        if "duplicates_constraint" not in index
    }
    assert set(indexes) == {"uq_offer_versions_current_purpose"}
    current_index = indexes["uq_offer_versions_current_purpose"]
    assert current_index["unique"] is True
    assert current_index["column_names"] == ["purpose"]
    assert current_index["dialect_options"]["postgresql_where"] == (
        "((status)::text = 'CURRENT'::text)"
    )
    for table_name in ("offer_texts", "offer_acceptances", "audit_log"):
        assert all(
            "duplicates_constraint" in index
            for index in inspector.get_indexes(table_name)
        )

    expected_checks = {
        "offer_versions": {
            "ck_offer_versions_purpose_allowed",
            "ck_offer_versions_version_number_positive",
            "ck_offer_versions_status_allowed",
            "ck_offer_versions_approval_evidence_matches_status",
            "ck_offer_versions_current_metadata_matches_status",
            "ck_offer_versions_legal_review_authority_valid",
            "ck_offer_versions_legal_review_reference_valid",
            "ck_offer_versions_timestamp_order",
        },
        "offer_texts": {
            "ck_offer_texts_language_allowed",
            "ck_offer_texts_content_canonical",
            "ck_offer_texts_content_hash_sha256_hex",
            "ck_offer_texts_timestamp_order",
        },
        "offer_acceptances": {
            "ck_offer_acceptances_purpose_allowed",
            "ck_offer_acceptances_language_allowed",
            "ck_offer_acceptances_version_number_positive",
            "ck_offer_acceptances_content_hash_sha256_hex",
            "ck_offer_acceptances_user_agent_normalized",
        },
        "audit_log": {
            "ck_audit_log_event_type_allowed",
            "ck_audit_log_actor_kind_allowed",
            "ck_audit_log_object_type_allowed",
            "ck_audit_log_actor_matches_event",
            "ck_audit_log_object_matches_event",
            "ck_audit_log_payload_exact_shape",
        },
    }
    for table_name, names in expected_checks.items():
        assert {
            check["name"] for check in inspector.get_check_constraints(table_name)
        } == names


@pytest.mark.integration
def test_m8_m9_m8_m9_walk_preserves_inherited_schema_and_data(
    m2_test_database: Engine,
) -> None:
    config = _config()
    user_id = uuid4()
    object_id = uuid4()
    try:
        command.downgrade(config, M8_REVISION)
        with m2_test_database.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users ("
                    "id, phone, password_hash, is_active, created_at, updated_at"
                    ") VALUES ("
                    ":id, :phone, NULL, true, :now, :now"
                    ")"
                ),
                {
                    "id": user_id,
                    "phone": f"+998{user_id.int % 1_000_000_000:09d}",
                    "now": NOW,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO object_files ("
                    "id, bucket, object_key, content_type, size_bytes, "
                    "checksum_sha256, width_px, height_px, status, "
                    "created_by_user_id, failure_code, created_at, updated_at, "
                    "available_at, terminal_at, deleted_at"
                    ") VALUES ("
                    ":id, 'nasiya-private-test', :object_key, 'image/png', 128, "
                    ":checksum, 4, 3, 'PENDING_UPLOAD', "
                    ":user_id, NULL, :now, :now, NULL, NULL, NULL"
                    ")"
                ),
                {
                    "id": object_id,
                    "object_key": f"v1/objects/{object_id.hex}.png",
                    "checksum": "a" * 64,
                    "user_id": user_id,
                    "now": NOW,
                },
            )

        command.upgrade(config, M9_REVISION)
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT is_platform_admin FROM users WHERE id = :user_id"),
                    {"user_id": user_id},
                )
                is False
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM object_files WHERE id = :id"),
                    {"id": object_id},
                )
                == 1
            )

        command.downgrade(config, M8_REVISION)
        inspector = inspect(m2_test_database)
        assert set(inspector.get_table_names()) == M8_TABLES
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
                    text("SELECT count(*) FROM object_files WHERE id = :id"),
                    {"id": object_id},
                )
                == 1
            )

        command.upgrade(config, M9_REVISION)
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT is_platform_admin FROM users WHERE id = :user_id"),
                    {"user_id": user_id},
                )
                is False
            )
    finally:
        command.upgrade(config, "head")
        with m2_test_database.begin() as connection:
            connection.execute(
                text("DELETE FROM object_files WHERE id = :id"),
                {"id": object_id},
            )
            connection.execute(
                text("DELETE FROM users WHERE id = :id"),
                {"id": user_id},
            )
