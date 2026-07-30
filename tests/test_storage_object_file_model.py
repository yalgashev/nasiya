import inspect
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.inspection import inspect as sqlalchemy_inspect

import app.storage.models as storage_models
from app.storage.models import ObjectFile, ObjectFileStatus

EXPECTED_COLUMNS = {
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
EXPECTED_CHECKS = {
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
EXPECTED_INDEXES = {
    "ix_object_files_status_updated_at": ("status", "updated_at"),
    "ix_object_files_created_by_user_id_created_at": (
        "created_by_user_id",
        "created_at",
    ),
}
FORBIDDEN_COLUMNS = {
    "original_filename",
    "filename",
    "claimed_mime",
    "public_url",
    "presigned_url",
    "raw_bytes",
    "sanitized_bytes",
    "exif",
    "xmp",
    "icc",
    "owner_type",
    "owner_id",
    "domain_parent_id",
    "acl",
    "etag",
    "access_key",
    "secret_key",
    "metadata_json",
    "provider_response",
    "provider_error_body",
    "gps",
    "comment",
    "iptc",
    "client_ip",
    "session_id",
    "user_ip",
}


def test_object_file_has_exact_columns_types_lengths_and_nullability() -> None:
    table = ObjectFile.__table__

    assert table.name == "object_files"
    assert set(table.c.keys()) == EXPECTED_COLUMNS
    assert len(table.c) == 16
    assert isinstance(table.c.id.type, PostgresUUID)
    assert table.c.id.type.as_uuid is True
    assert table.c.id.primary_key is True
    assert table.c.id.nullable is False
    assert table.c.id.default is not None
    assert table.c.id.server_default is None

    string_lengths = {
        "bucket": 63,
        "object_key": 255,
        "content_type": 32,
        "checksum_sha256": 64,
        "status": 32,
        "failure_code": 64,
    }
    for column_name, length in string_lengths.items():
        assert isinstance(table.c[column_name].type, String)
        assert table.c[column_name].type.length == length

    assert isinstance(table.c.size_bytes.type, BigInteger)
    assert isinstance(table.c.width_px.type, Integer)
    assert isinstance(table.c.height_px.type, Integer)
    assert isinstance(table.c.created_by_user_id.type, PostgresUUID)

    nullable_columns = {
        "failure_code",
        "available_at",
        "terminal_at",
        "deleted_at",
    }
    for column in table.c:
        assert column.nullable is (column.name in nullable_columns)


def test_timestamps_are_timezone_aware_with_exact_defaults() -> None:
    table = ObjectFile.__table__
    timestamp_columns = {
        "created_at",
        "updated_at",
        "available_at",
        "terminal_at",
        "deleted_at",
    }
    for column_name in timestamp_columns:
        column = table.c[column_name]
        assert isinstance(column.type, DateTime)
        assert column.type.timezone is True

    for column_name in {"created_at", "updated_at"}:
        column = table.c[column_name]
        assert column.default is not None
        assert column.server_default is not None
        assert str(column.server_default.arg) == "CURRENT_TIMESTAMP"
        assert column.onupdate is None

    assert table.c.status.default is None
    assert table.c.status.server_default is None


def test_exact_named_checks_unique_fk_and_indexes() -> None:
    table = ObjectFile.__table__
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert set(checks) == EXPECTED_CHECKS
    assert (
        "width_px::bigint * height_px::bigint <= 40000000"
        in checks["ck_object_files_dimensions"]
    )
    assert "UPLOAD_OUTCOME_UNKNOWN" in checks["ck_object_files_state_consistent"]
    assert "OBJECT_METADATA_MISMATCH" in checks["ck_object_files_state_consistent"]
    assert "DELETE_OUTCOME_UNKNOWN" in checks["ck_object_files_state_consistent"]

    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    assert len(unique_constraints) == 1
    assert unique_constraints[0].name == "uq_object_files_bucket_object_key"
    assert tuple(column.name for column in unique_constraints[0].columns) == (
        "bucket",
        "object_key",
    )

    foreign_keys = list(table.foreign_key_constraints)
    assert len(foreign_keys) == 1
    foreign_key = foreign_keys[0]
    assert foreign_key.name == ("fk_object_files_created_by_user_id_users_id")
    assert tuple(foreign_key.column_keys) == ("created_by_user_id",)
    assert tuple(element.target_fullname for element in foreign_key.elements) == (
        "users.id",
    )
    assert foreign_key.ondelete == "RESTRICT"

    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
        if isinstance(index, Index)
    }
    assert indexes == EXPECTED_INDEXES
    assert all(index.unique is False for index in table.indexes)


def test_status_surface_is_string_plus_closed_application_enum() -> None:
    assert {status.value for status in ObjectFileStatus} == {
        "PENDING_UPLOAD",
        "AVAILABLE",
        "FAILED",
        "DELETE_PENDING",
        "DELETED",
    }
    assert isinstance(ObjectFile.__table__.c.status.type, String)
    assert "Enum" not in type(ObjectFile.__table__.c.status.type).__name__


def test_relationship_is_minimal_and_has_no_delete_cascade() -> None:
    mapper = sqlalchemy_inspect(ObjectFile)

    assert set(mapper.relationships.keys()) == {"created_by_user"}
    relationship = mapper.relationships["created_by_user"]
    assert relationship.mapper.class_.__name__ == "User"
    assert "delete" not in relationship.cascade
    assert "delete-orphan" not in relationship.cascade
    assert relationship.back_populates is None


def test_forbidden_fields_are_absent_from_metadata_and_model_source() -> None:
    column_names = set(ObjectFile.__table__.c.keys())
    assert column_names.isdisjoint(FORBIDDEN_COLUMNS)

    source = inspect.getsource(storage_models)
    assert "JSON" not in source
    assert "LargeBinary" not in source
    assert "commit(" not in source
    assert "rollback(" not in source
    assert "close(" not in source


def test_object_file_repr_redacts_storage_identity_and_actor() -> None:
    sensitive_bucket = "private-customer-559-bucket"
    sensitive_key = "v1/objects/0123456789abcdef0123456789abcdef.png"
    sensitive_checksum = "a" * 64
    actor_id = UUID("123e4567-e89b-42d3-a456-426614174000")
    now = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)
    object_file = ObjectFile(
        id=UUID("ffeeddcc-bbaa-4988-8776-554433221100"),
        bucket=sensitive_bucket,
        object_key=sensitive_key,
        content_type="image/png",
        size_bytes=123,
        checksum_sha256=sensitive_checksum,
        width_px=3,
        height_px=2,
        status=ObjectFileStatus.PENDING_UPLOAD.value,
        created_by_user_id=actor_id,
        failure_code=None,
        created_at=now,
        updated_at=now,
        available_at=None,
        terminal_at=None,
        deleted_at=None,
    )

    rendered = f"{object_file!s} {object_file!r}"

    assert sensitive_bucket not in rendered
    assert sensitive_key not in rendered
    assert sensitive_checksum not in rendered
    assert str(actor_id) not in rendered
    assert "bucket=<redacted>" in rendered
    assert "object_key=<redacted>" in rendered
    assert "checksum_sha256=<redacted>" in rendered
