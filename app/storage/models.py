from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.auth.models import User
from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class ObjectFileStatus(StrEnum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"
    DELETE_PENDING = "DELETE_PENDING"
    DELETED = "DELETED"


class ObjectFile(Base):
    __tablename__ = "object_files"
    __table_args__ = (
        UniqueConstraint(
            "bucket",
            "object_key",
            name="uq_object_files_bucket_object_key",
        ),
        CheckConstraint(
            "bucket ~ '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$' "
            "AND bucket !~ '\\.\\.' "
            "AND bucket !~ '\\.-' "
            "AND bucket !~ '-\\.' "
            "AND bucket !~ '^[0-9]{1,3}(\\.[0-9]{1,3}){3}$'",
            name="ck_object_files_bucket_format",
        ),
        CheckConstraint(
            "object_key ~ '^v1/objects/[0-9a-f]{32}\\.(jpg|png|webp)$'",
            name="ck_object_files_object_key_format",
        ),
        CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_object_files_content_type_allowed",
        ),
        CheckConstraint(
            "size_bytes BETWEEN 1 AND 10485760",
            name="ck_object_files_size_bytes",
        ),
        CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_object_files_checksum_sha256",
        ),
        CheckConstraint(
            "width_px BETWEEN 1 AND 16384 "
            "AND height_px BETWEEN 1 AND 16384 "
            "AND width_px::bigint * height_px::bigint <= 40000000",
            name="ck_object_files_dimensions",
        ),
        CheckConstraint(
            "status IN ("
            "'PENDING_UPLOAD', 'AVAILABLE', 'FAILED', "
            "'DELETE_PENDING', 'DELETED'"
            ")",
            name="ck_object_files_status_allowed",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_object_files_failure_code_format",
        ),
        CheckConstraint(
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
        CheckConstraint(
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
        Index(
            "ix_object_files_status_updated_at",
            "status",
            "updated_at",
        ),
        Index(
            "ix_object_files_created_by_user_id_created_at",
            "created_by_user_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_object_files_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    failure_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    terminal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by_user: Mapped[User] = relationship(
        User,
        lazy="raise",
    )

    def __repr__(self) -> str:
        return (
            "ObjectFile("
            f"id={'<set>' if self.id is not None else '<unset>'}, "
            f"status={self.status!r}, "
            f"content_type={self.content_type!r}, "
            f"size_bytes={self.size_bytes!r}, "
            f"width_px={self.width_px!r}, "
            f"height_px={self.height_px!r}, "
            "bucket=<redacted>, object_key=<redacted>, "
            "checksum_sha256=<redacted>, "
            "created_by_user_id=<redacted>, "
            f"failure_code={self.failure_code!r}"
            ")"
        )
