"""PostgreSQL metadata for M16 immutable rating and disclosure facts."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RatingEvent(Base):
    """Append-only source event; ORM instances stay inside repositories."""

    __tablename__ = "rating_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('on_time_paid','overdue')",
            name="ck_rating_events_event_type_allowed",
        ),
        CheckConstraint(
            "(event_type = 'on_time_paid' AND delta = 5) OR "
            "(event_type = 'overdue' AND delta = -15)",
            name="ck_rating_events_delta_matches_event",
        ),
        CheckConstraint(
            "recording_source IN ('live','historical_reconciliation')",
            name="ck_rating_events_recording_source_allowed",
        ),
        CheckConstraint(
            "business_date = (occurred_at AT TIME ZONE 'Asia/Tashkent')::date",
            name="ck_rating_events_business_date_matches_occurred_at",
        ),
        ForeignKeyConstraint(
            ("debt_id", "shop_customer_id"),
            ("debts.id", "debts.shop_customer_id"),
            name="fk_rating_events_debt_shop_customer",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("id", name="pk_rating_events"),
        UniqueConstraint(
            "debt_id",
            "event_type",
            name="uq_rating_events_debt_id_event_type",
        ),
        Index(
            "ux_rating_events_positive_shop_customer_business_date",
            "shop_customer_id",
            "business_date",
            unique=True,
            postgresql_where=text("event_type = 'on_time_paid'"),
        ),
        Index(
            "ix_rating_events_shop_customer_occurred_debt_event",
            "shop_customer_id",
            "occurred_at",
            "debt_id",
            "event_type",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    shop_customer_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), nullable=False
    )
    debt_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    delta: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    recording_source: Mapped[str] = mapped_column(String(32), nullable=False)

    def __repr__(self) -> str:
        return "RatingEvent(<redacted>)"


class DisclosureViewLog(Base):
    """Tenant-bound immutable band-only disclosure snapshot."""

    __tablename__ = "disclosure_view_logs"
    __table_args__ = (
        CheckConstraint(
            "purpose IN "
            "('debt_proposal_review','credit_limit_review','existing_debt_review')",
            name="ck_disclosure_view_logs_purpose_allowed",
        ),
        CheckConstraint(
            "band IN ('new','green','yellow','red','blocked')",
            name="ck_disclosure_view_logs_band_allowed",
        ),
        ForeignKeyConstraint(
            ("shop_customer_id", "shop_id"),
            ("shop_customers.id", "shop_customers.shop_id"),
            name="fk_disclosure_logs_shop_customer_shop",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("id", name="pk_disclosure_view_logs"),
        Index("ix_disclosure_view_logs_shop_id_id", "shop_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_disclosure_view_logs_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    shop_id: Mapped[UUID] = mapped_column(PostgresUUID(as_uuid=True), nullable=False)
    shop_customer_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    def __repr__(self) -> str:
        return "DisclosureViewLog(<redacted>)"
