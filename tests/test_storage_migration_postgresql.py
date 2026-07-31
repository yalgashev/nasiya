from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M7_REVISION = "e7f8a9b0c1d2"
M8_REVISION = "f8a9b0c1d2e3"
INHERITED_TABLES = {
    "alembic_version",
    "auth_rate_limits",
    "customers",
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
EXPECTED_CHECKS = {
    "ck_object_files_bucket_format",
    "ck_object_files_checksum_sha256",
    "ck_object_files_content_type_allowed",
    "ck_object_files_dimensions",
    "ck_object_files_failure_code_format",
    "ck_object_files_object_key_format",
    "ck_object_files_size_bytes",
    "ck_object_files_state_consistent",
    "ck_object_files_status_allowed",
    "ck_object_files_timestamp_order",
}
NOW = datetime(2026, 7, 30, 16, 0, tzinfo=UTC)

INSERT_USER = text(
    """
    INSERT INTO users (
        id, phone, password_hash, is_active, created_at, updated_at
    ) VALUES (
        :id, :phone, NULL, true, :created_at, :updated_at
    )
    """
)
INSERT_OBJECT_FILE = text(
    """
    INSERT INTO object_files (
        id,
        bucket,
        object_key,
        content_type,
        size_bytes,
        checksum_sha256,
        width_px,
        height_px,
        status,
        created_by_user_id,
        failure_code,
        created_at,
        updated_at,
        available_at,
        terminal_at,
        deleted_at
    ) VALUES (
        :id,
        :bucket,
        :object_key,
        :content_type,
        :size_bytes,
        :checksum_sha256,
        :width_px,
        :height_px,
        :status,
        :created_by_user_id,
        :failure_code,
        :created_at,
        :updated_at,
        :available_at,
        :terminal_at,
        :deleted_at
    )
    """
)


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert isinstance(revision, str)
    return revision


def _insert_user(connection: Connection) -> UUID:
    user_id = uuid4()
    connection.execute(
        INSERT_USER,
        {
            "id": user_id,
            "phone": f"+998{user_id.int % 1_000_000_000:09d}",
            "created_at": NOW,
            "updated_at": NOW,
        },
    )
    return user_id


def _object_values(
    user_id: UUID,
    **overrides: object,
) -> dict[str, object]:
    object_id = uuid4()
    values: dict[str, object] = {
        "id": object_id,
        "bucket": "nasiya-private-test",
        "object_key": f"v1/objects/{object_id.hex}.png",
        "content_type": "image/png",
        "size_bytes": 128,
        "checksum_sha256": "a" * 64,
        "width_px": 4,
        "height_px": 3,
        "status": "PENDING_UPLOAD",
        "created_by_user_id": user_id,
        "failure_code": None,
        "created_at": NOW,
        "updated_at": NOW,
        "available_at": None,
        "terminal_at": None,
        "deleted_at": None,
    }
    values.update(overrides)
    return values


def _insert_object(
    connection: Connection,
    user_id: UUID,
    **overrides: object,
) -> dict[str, object]:
    values = _object_values(user_id, **overrides)
    connection.execute(INSERT_OBJECT_FILE, values)
    return values


@pytest.mark.integration
def test_empty_to_m8_parent_walk_and_repeat_upgrade_preserve_inherited_tables(
    m2_test_database: Engine,
) -> None:
    config = _config()
    try:
        command.downgrade(config, "base")
        empty_tables = set(inspect(m2_test_database).get_table_names())
        assert empty_tables <= {"alembic_version"}

        command.upgrade(config, M8_REVISION)
        assert _current_revision(m2_test_database) == M8_REVISION
        assert set(inspect(m2_test_database).get_table_names()) == (
            INHERITED_TABLES | {"object_files"}
        )

        command.downgrade(config, M7_REVISION)
        parent_tables = set(inspect(m2_test_database).get_table_names())
        assert "object_files" not in parent_tables
        assert parent_tables == INHERITED_TABLES
        assert len(parent_tables) == 18

        command.upgrade(config, M8_REVISION)
        command.upgrade(config, M8_REVISION)
        assert _current_revision(m2_test_database) == M8_REVISION
        assert set(inspect(m2_test_database).get_table_names()) == (
            INHERITED_TABLES | {"object_files"}
        )
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_real_database_has_exact_object_file_schema_objects(
    m2_test_database: Engine,
) -> None:
    config = _config()
    command.downgrade(config, M8_REVISION)
    try:
        inspector = inspect(m2_test_database)

        assert set(inspector.get_table_names()) == (INHERITED_TABLES | {"object_files"})
        assert {column["name"] for column in inspector.get_columns("object_files")} == {
            "id",
            "bucket",
            "object_key",
            "content_type",
            "size_bytes",
            "checksum_sha256",
            "width_px",
            "height_px",
            "status",
            "created_by_user_id",
            "failure_code",
            "created_at",
            "updated_at",
            "available_at",
            "terminal_at",
            "deleted_at",
        }
        assert {
            check["name"] for check in inspector.get_check_constraints("object_files")
        } == EXPECTED_CHECKS
        assert inspector.get_pk_constraint("object_files")["name"] == (
            "pk_object_files"
        )
        assert inspector.get_unique_constraints("object_files") == [
            {
                "column_names": ["bucket", "object_key"],
                "name": "uq_object_files_bucket_object_key",
                "comment": None,
                "dialect_options": {
                    "postgresql_include": [],
                    "postgresql_nulls_not_distinct": False,
                },
            }
        ]

        foreign_keys = inspector.get_foreign_keys("object_files")
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["name"] == (
            "fk_object_files_created_by_user_id_users_id"
        )
        assert foreign_keys[0]["constrained_columns"] == ["created_by_user_id"]
        assert foreign_keys[0]["referred_table"] == "users"
        assert foreign_keys[0]["referred_columns"] == ["id"]
        assert foreign_keys[0]["options"]["ondelete"] == "RESTRICT"

        non_unique_indexes = {
            index["name"]: tuple(index["column_names"])
            for index in inspector.get_indexes("object_files")
            if not index["unique"]
        }
        assert non_unique_indexes == {
            "ix_object_files_status_updated_at": ("status", "updated_at"),
            "ix_object_files_created_by_user_id_created_at": (
                "created_by_user_id",
                "created_at",
            ),
        }
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_each_object_file_lifecycle_state_has_a_valid_database_shape(
    m2_test_database: Engine,
) -> None:
    command.upgrade(_config(), "head")
    with m2_test_database.begin() as connection:
        user_id = _insert_user(connection)
        _insert_object(connection, user_id)
        _insert_object(
            connection,
            user_id,
            status="AVAILABLE",
            available_at=NOW,
        )
        _insert_object(
            connection,
            user_id,
            status="FAILED",
            terminal_at=NOW,
            failure_code="STORAGE_PROVIDER_UNAVAILABLE",
        )
        _insert_object(
            connection,
            user_id,
            status="DELETE_PENDING",
            available_at=NOW,
            failure_code="DELETE_OUTCOME_UNKNOWN",
        )
        _insert_object(
            connection,
            user_id,
            status="DELETED",
            available_at=NOW,
            terminal_at=NOW + timedelta(seconds=1),
            deleted_at=NOW + timedelta(seconds=2),
        )

        rows = connection.execute(
            text("SELECT status, count(*) FROM object_files GROUP BY status")
        ).all()

    assert dict(rows) == {
        "PENDING_UPLOAD": 1,
        "AVAILABLE": 1,
        "FAILED": 1,
        "DELETE_PENDING": 1,
        "DELETED": 1,
    }


INVALID_CASES = [
    (
        {"bucket": "192.0.2.10"},
        "ck_object_files_bucket_format",
    ),
    (
        {"object_key": "v1/objects/ABCDEF.png"},
        "ck_object_files_object_key_format",
    ),
    (
        {"content_type": "image/gif"},
        "ck_object_files_content_type_allowed",
    ),
    (
        {"size_bytes": 0},
        "ck_object_files_size_bytes",
    ),
    (
        {"size_bytes": 10_485_761},
        "ck_object_files_size_bytes",
    ),
    (
        {"checksum_sha256": "A" * 64},
        "ck_object_files_checksum_sha256",
    ),
    (
        {"width_px": 16_385},
        "ck_object_files_dimensions",
    ),
    (
        {"width_px": 10_000, "height_px": 5_000},
        "ck_object_files_dimensions",
    ),
    (
        {"status": "PUBLIC"},
        "ck_object_files_state_consistent",
    ),
    (
        {
            "status": "FAILED",
            "terminal_at": NOW,
            "failure_code": "unsafe-code",
        },
        "ck_object_files_failure_code_format",
    ),
    (
        {"status": "AVAILABLE"},
        "ck_object_files_state_consistent",
    ),
    (
        {
            "failure_code": "OBJECT_METADATA_MISMATCH",
        },
        "ck_object_files_state_consistent",
    ),
    (
        {"updated_at": NOW - timedelta(seconds=1)},
        "ck_object_files_timestamp_order",
    ),
    (
        {
            "status": "AVAILABLE",
            "available_at": NOW - timedelta(seconds=1),
        },
        "ck_object_files_timestamp_order",
    ),
    (
        {
            "status": "DELETED",
            "available_at": NOW + timedelta(seconds=2),
            "terminal_at": NOW + timedelta(seconds=1),
            "deleted_at": NOW + timedelta(seconds=3),
        },
        "ck_object_files_timestamp_order",
    ),
    (
        {
            "status": "DELETED",
            "available_at": NOW,
            "terminal_at": NOW + timedelta(seconds=2),
            "deleted_at": NOW + timedelta(seconds=1),
        },
        "ck_object_files_timestamp_order",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "expected_constraint"),
    INVALID_CASES,
)
def test_database_rejects_invalid_object_file_values_and_connection_recovers(
    m2_test_database: Engine,
    overrides: dict[str, object],
    expected_constraint: str,
) -> None:
    command.upgrade(_config(), "head")
    with m2_test_database.begin() as connection:
        user_id = _insert_user(connection)

        with pytest.raises(IntegrityError) as exc_info:
            with connection.begin_nested():
                _insert_object(connection, user_id, **overrides)

        assert exc_info.value.orig.diag.constraint_name == (expected_constraint)
        assert connection.scalar(text("SELECT 1")) == 1
        _insert_object(connection, user_id)
        assert connection.scalar(text("SELECT count(*) FROM object_files")) == 1


@pytest.mark.integration
def test_user_fk_restrict_conflict_leaves_connection_usable(
    m2_test_database: Engine,
) -> None:
    command.upgrade(_config(), "head")
    with m2_test_database.begin() as connection:
        user_id = _insert_user(connection)
        values = _insert_object(connection, user_id)

        with pytest.raises(IntegrityError) as exc_info:
            with connection.begin_nested():
                connection.execute(
                    text("DELETE FROM users WHERE id = :user_id"),
                    {"user_id": user_id},
                )

        assert exc_info.value.orig.diag.constraint_name == (
            "fk_object_files_created_by_user_id_users_id"
        )
        assert connection.scalar(text("SELECT 1")) == 1
        assert (
            connection.scalar(
                text("SELECT count(*) FROM object_files WHERE id = :id"),
                {"id": values["id"]},
            )
            == 1
        )
