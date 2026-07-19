from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

CUSTOMER_ONBOARDING_STATUS_DRAFT: Final = "draft"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_customers_user_id"),
        CheckConstraint(
            f"onboarding_status = '{CUSTOMER_ONBOARDING_STATUS_DRAFT}'",
            name="ck_customers_onboarding_status_draft_only",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    onboarding_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=CUSTOMER_ONBOARDING_STATUS_DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
