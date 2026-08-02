from datetime import UTC, datetime
from typing import Final
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

CUSTOMER_ONBOARDING_STATUS_DRAFT: Final = "draft"
CUSTOMER_ONBOARDING_STATUS_ACTIVE: Final = "active"


def utc_now() -> datetime:
    return datetime.now(UTC)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_customers_user_id"),
        CheckConstraint(
            "onboarding_status IN ("
            f"'{CUSTOMER_ONBOARDING_STATUS_DRAFT}', "
            f"'{CUSTOMER_ONBOARDING_STATUS_ACTIVE}'"
            ")",
            name="ck_customers_onboarding_status_allowed",
        ),
        CheckConstraint(
            "("
            f"onboarding_status = '{CUSTOMER_ONBOARDING_STATUS_DRAFT}' "
            "AND activated_at IS NULL"
            ") OR ("
            f"onboarding_status = '{CUSTOMER_ONBOARDING_STATUS_ACTIVE}' "
            "AND activated_at IS NOT NULL"
            ")",
            name="ck_customers_activation_state_consistent",
        ),
        CheckConstraint(
            "updated_at >= created_at "
            "AND (activated_at IS NULL OR activated_at >= created_at) "
            "AND (activated_at IS NULL OR updated_at >= activated_at)",
            name="ck_customers_timestamp_order",
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
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            "Customer("
            f"id={'<set>' if self.id is not None else '<unset>'}, "
            "user_id=<redacted>, "
            f"onboarding_status={self.onboarding_status!r}, "
            f"activated_at={'<set>' if self.activated_at is not None else None})"
        )
