from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

CUSTOMER_IDENTITY_BLIND_INDEX_CONSTRAINT: Final = (
    "uq_customer_identities_jshshir_blind_index"
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class CustomerIdentity(Base):
    __tablename__ = "customer_identities"
    __table_args__ = (
        UniqueConstraint(
            "jshshir_blind_index",
            name=CUSTOMER_IDENTITY_BLIND_INDEX_CONSTRAINT,
        ),
        CheckConstraint(
            "octet_length(ciphertext) >= 16",
            name="ck_customer_identities_ciphertext_minimum_length",
        ),
        CheckConstraint(
            "octet_length(nonce) = 12",
            name="ck_customer_identities_nonce_length",
        ),
        CheckConstraint(
            "key_id ~ '^[A-Za-z0-9._-]{1,64}$'",
            name="ck_customer_identities_key_id_format",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="ck_customer_identities_schema_version_supported",
        ),
        CheckConstraint(
            "octet_length(jshshir_blind_index) = 32",
            name="ck_customer_identities_blind_index_length",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_customer_identities_revision_positive",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_customer_identities_timestamp_order",
        ),
    )

    customer_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "customers.id",
            name="fk_customer_identities_customer_id_customers_id",
            ondelete="RESTRICT",
        ),
        primary_key=True,
    )
    ciphertext: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    nonce: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default=sqlalchemy_text("1"),
    )
    jshshir_blind_index: Mapped[bytes] = mapped_column(
        BYTEA,
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
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
        onupdate=utc_now,
    )

    def __repr__(self) -> str:
        return (
            "CustomerIdentity("
            "customer_id=<redacted>, ciphertext=<redacted>, nonce=<redacted>, "
            "key_id=<redacted>, jshshir_blind_index=<redacted>, "
            f"schema_version={self.schema_version!r}, "
            f"revision={self.revision!r}, created_at={self.created_at!r}, "
            f"updated_at={self.updated_at!r})"
        )
