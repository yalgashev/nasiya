from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.customer_document.contracts import CustomerDocumentStatus
from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class CustomerDocument(Base):
    __tablename__ = "customer_documents"
    __table_args__ = (
        UniqueConstraint(
            "object_file_id",
            name="uq_customer_documents_object_file_id",
        ),
        UniqueConstraint(
            "customer_id",
            "submission_id",
            name="uq_customer_documents_customer_id_submission_id",
        ),
        CheckConstraint(
            "status IN ('CURRENT', 'SUPERSEDED')",
            name="ck_customer_documents_status_allowed",
        ),
        CheckConstraint(
            "(status = 'CURRENT' "
            "AND superseded_by_document_id IS NULL "
            "AND superseded_at IS NULL) "
            "OR (status = 'SUPERSEDED' "
            "AND superseded_by_document_id IS NOT NULL "
            "AND superseded_at IS NOT NULL)",
            name="ck_customer_documents_supersede_metadata_matches_status",
        ),
        CheckConstraint(
            "superseded_by_document_id IS NULL OR superseded_by_document_id <> id",
            name="ck_customer_documents_no_self_replacement",
        ),
        CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= attached_at",
            name="ck_customer_documents_timestamp_order",
        ),
        Index(
            "uq_customer_documents_current_customer_id",
            "customer_id",
            unique=True,
            postgresql_where=sqlalchemy_text("status = 'CURRENT'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "customers.id",
            name="fk_customer_documents_customer_id_customers_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    object_file_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "object_files.id",
            name="fk_customer_documents_object_file_id_object_files_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    submission_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CustomerDocumentStatus.CURRENT.value,
    )
    attached_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_customer_documents_attached_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    attached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )
    superseded_by_document_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "customer_documents.id",
            name="fk_customer_documents_superseded_by_id_customer_documents",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            "CustomerDocument("
            "id=<redacted>, customer_id=<redacted>, object_file_id=<redacted>, "
            "submission_id=<redacted>, attached_by_user_id=<redacted>, "
            f"status={self.status!r}, attached_at={self.attached_at!r}, "
            "superseded_by_document_id=<redacted>, "
            f"superseded_at={self.superseded_at!r})"
        )
