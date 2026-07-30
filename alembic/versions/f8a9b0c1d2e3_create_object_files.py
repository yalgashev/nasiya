"""create object files

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "e7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "object_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.String(length=63), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "terminal_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "bucket ~ '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$' "
            "AND bucket !~ '\\.\\.' "
            "AND bucket !~ '\\.-' "
            "AND bucket !~ '-\\.' "
            "AND bucket !~ '^[0-9]{1,3}(\\.[0-9]{1,3}){3}$'",
            name="ck_object_files_bucket_format",
        ),
        sa.CheckConstraint(
            "object_key ~ '^v1/objects/[0-9a-f]{32}\\.(jpg|png|webp)$'",
            name="ck_object_files_object_key_format",
        ),
        sa.CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_object_files_content_type_allowed",
        ),
        sa.CheckConstraint(
            "size_bytes BETWEEN 1 AND 10485760",
            name="ck_object_files_size_bytes",
        ),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_object_files_checksum_sha256",
        ),
        sa.CheckConstraint(
            "width_px BETWEEN 1 AND 16384 "
            "AND height_px BETWEEN 1 AND 16384 "
            "AND width_px::bigint * height_px::bigint <= 40000000",
            name="ck_object_files_dimensions",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'PENDING_UPLOAD', 'AVAILABLE', 'FAILED', "
            "'DELETE_PENDING', 'DELETED'"
            ")",
            name="ck_object_files_status_allowed",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_object_files_failure_code_format",
        ),
        sa.CheckConstraint(
            "("
            "status = 'PENDING_UPLOAD' "
            "AND available_at IS NULL "
            "AND terminal_at IS NULL "
            "AND deleted_at IS NULL "
            "AND ("
            "failure_code IS NULL "
            "OR failure_code = 'UPLOAD_OUTCOME_UNKNOWN'"
            ")"
            ") OR ("
            "status = 'AVAILABLE' "
            "AND available_at IS NOT NULL "
            "AND terminal_at IS NULL "
            "AND deleted_at IS NULL "
            "AND failure_code IS NULL"
            ") OR ("
            "status = 'FAILED' "
            "AND available_at IS NULL "
            "AND terminal_at IS NOT NULL "
            "AND deleted_at IS NULL "
            "AND failure_code IS NOT NULL"
            ") OR ("
            "status = 'DELETE_PENDING' "
            "AND terminal_at IS NULL "
            "AND deleted_at IS NULL "
            "AND ("
            "failure_code IS NULL "
            "OR failure_code IN ("
            "'OBJECT_METADATA_MISMATCH', 'DELETE_OUTCOME_UNKNOWN'"
            ")"
            ")"
            ") OR ("
            "status = 'DELETED' "
            "AND terminal_at IS NOT NULL "
            "AND deleted_at IS NOT NULL"
            ")",
            name="ck_object_files_state_consistent",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "AND (available_at IS NULL OR available_at >= created_at) "
            "AND (terminal_at IS NULL OR terminal_at >= created_at) "
            "AND ("
            "terminal_at IS NULL "
            "OR available_at IS NULL "
            "OR terminal_at >= available_at"
            ") "
            "AND ("
            "deleted_at IS NULL "
            "OR ("
            "terminal_at IS NOT NULL "
            "AND deleted_at >= terminal_at"
            ")"
            ")",
            name="ck_object_files_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_object_files_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_object_files"),
        sa.UniqueConstraint(
            "bucket",
            "object_key",
            name="uq_object_files_bucket_object_key",
        ),
    )
    op.create_index(
        "ix_object_files_status_updated_at",
        "object_files",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_object_files_created_by_user_id_created_at",
        "object_files",
        ["created_by_user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_object_files_created_by_user_id_created_at",
        table_name="object_files",
    )
    op.drop_index(
        "ix_object_files_status_updated_at",
        table_name="object_files",
    )
    op.drop_table("object_files")
