import importlib.util
import inspect
from pathlib import Path

from sqlalchemy import CheckConstraint, Column, ForeignKeyConstraint

M7_REVISION = "e7f8a9b0c1d2"
M8_REVISION = "f8a9b0c1d2e3"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT / "alembic" / "versions" / "f8a9b0c1d2e3_create_object_files.py"
)


class MigrationRecorder:
    def __init__(self) -> None:
        self.created_tables: list[tuple[str, tuple[object, ...]]] = []
        self.created_indexes: list[tuple[str, str, tuple[str, ...], bool]] = []
        self.dropped_indexes: list[tuple[str, str]] = []
        self.dropped_tables: list[str] = []

    def create_table(self, name: str, *items: object) -> None:
        self.created_tables.append((name, items))

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        *,
        unique: bool,
    ) -> None:
        self.created_indexes.append((name, table_name, tuple(columns), unique))

    def drop_index(self, name: str, *, table_name: str) -> None:
        self.dropped_indexes.append((name, table_name))

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)


def _revision():
    spec = importlib.util.spec_from_file_location(
        "m8_storage_migration",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_storage_migration_is_single_linear_child_of_m7_head() -> None:
    revision = _revision()

    assert revision.revision == M8_REVISION
    assert revision.down_revision == M7_REVISION
    assert revision.branch_labels is None
    assert revision.depends_on is None


def test_upgrade_declares_exactly_one_table_and_two_indexes(
    monkeypatch,
) -> None:
    revision = _revision()
    recorder = MigrationRecorder()
    monkeypatch.setattr(revision, "op", recorder)

    revision.upgrade()

    assert len(recorder.created_tables) == 1
    table_name, items = recorder.created_tables[0]
    assert table_name == "object_files"
    assert recorder.created_indexes == [
        (
            "ix_object_files_status_updated_at",
            "object_files",
            ("status", "updated_at"),
            False,
        ),
        (
            "ix_object_files_created_by_user_id_created_at",
            "object_files",
            ("created_by_user_id", "created_at"),
            False,
        ),
    ]

    columns = {item.name: item for item in items if isinstance(item, Column)}
    assert set(columns) == {
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
    assert len(columns) == 16


def test_migration_constraints_match_exact_frozen_names(
    monkeypatch,
) -> None:
    revision = _revision()
    recorder = MigrationRecorder()
    monkeypatch.setattr(revision, "op", recorder)
    revision.upgrade()
    _, items = recorder.created_tables[0]

    checks = {
        item.name: str(item.sqltext)
        for item in items
        if isinstance(item, CheckConstraint)
    }
    assert set(checks) == {
        "ck_object_files_bucket_format",
        "ck_object_files_object_key_format",
        "ck_object_files_content_type_allowed",
        "ck_object_files_size_bytes",
        "ck_object_files_checksum_sha256",
        "ck_object_files_dimensions",
        "ck_object_files_status_allowed",
        "ck_object_files_failure_code_format",
        "ck_object_files_state_consistent",
        "ck_object_files_timestamp_order",
    }
    assert (
        "width_px::bigint * height_px::bigint <= 40000000"
        in checks["ck_object_files_dimensions"]
    )

    foreign_keys = [item for item in items if isinstance(item, ForeignKeyConstraint)]
    assert len(foreign_keys) == 1
    assert foreign_keys[0].name == ("fk_object_files_created_by_user_id_users_id")
    assert foreign_keys[0].ondelete == "RESTRICT"

    constraint_names = {
        getattr(item, "name", None) for item in items if not isinstance(item, Column)
    }
    assert "pk_object_files" in constraint_names
    assert "uq_object_files_bucket_object_key" in constraint_names


def test_downgrade_removes_only_m8_indexes_and_table(monkeypatch) -> None:
    revision = _revision()
    recorder = MigrationRecorder()
    monkeypatch.setattr(revision, "op", recorder)

    revision.downgrade()

    assert recorder.dropped_indexes == [
        (
            "ix_object_files_created_by_user_id_created_at",
            "object_files",
        ),
        ("ix_object_files_status_updated_at", "object_files"),
    ]
    assert recorder.dropped_tables == ["object_files"]
    assert recorder.created_tables == []
    assert recorder.created_indexes == []


def test_migration_has_no_data_read_second_table_or_unsafe_ddl() -> None:
    source = inspect.getsource(_revision())

    assert source.count("op.create_table(") == 1
    assert source.count("op.drop_table(") == 1
    assert "op.execute(" not in source
    assert "bulk_insert" not in source
    assert "create_all" not in source
    assert "sqlite" not in source.casefold()
    assert "SELECT " not in source
    assert "UPDATE " not in source
    assert "DELETE FROM " not in source
