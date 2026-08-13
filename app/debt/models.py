"""PostgreSQL metadata for the bounded M13 pending-debt state.

The due-date/expiry business-date relation remains in ``app.debt.business_time``:
the database session timezone must not define Asia/Tashkent product semantics.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    desc,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.models import utc_now
from app.db import Base
from app.debt.enums import DebtStatus


class Debt(Base):
    """Database invariants; Asia/Tashkent due-date comparison stays in app code."""

    __tablename__ = "debts"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "shop_customer_id",
            name="uq_debts_id_shop_customer_id",
        ),
        CheckConstraint(
            "original_amount_uzs BETWEEN 1 AND 1000000000000",
            name="ck_debts_original_amount_uzs_bounds",
        ),
        CheckConstraint(
            "discount_basis_points BETWEEN 0 AND 10000",
            name="ck_debts_discount_basis_points_bounds",
        ),
        CheckConstraint(
            "discounted_amount_uzs BETWEEN 1 AND original_amount_uzs",
            name="ck_debts_discounted_amount_uzs_bounds",
        ),
        CheckConstraint(
            (
                "status IN "
                f"('{DebtStatus.PENDING.value}', '{DebtStatus.ACTIVE.value}', "
                f"'{DebtStatus.REJECTED.value}', '{DebtStatus.CANCELLED.value}', "
                f"'{DebtStatus.EXPIRED.value}', '{DebtStatus.PAID.value}', "
                f"'{DebtStatus.OVERDUE.value}', "
                f"'{DebtStatus.WRITTEN_OFF.value}', "
                f"'{DebtStatus.WRITTEN_OFF_SETTLED.value}')"
            ),
            name="ck_debts_status_allowed",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_debts_revision_positive",
        ),
        CheckConstraint(
            "(overdue_at IS NULL) = (overdue_revision IS NULL)",
            name="ck_debts_overdue_metadata_pair",
        ),
        CheckConstraint(
            "overdue_revision IS NULL OR overdue_revision > 0",
            name="ck_debts_overdue_revision_positive",
        ),
        CheckConstraint(
            "overdue_revision IS NULL OR overdue_revision <= revision",
            name="ck_debts_overdue_revision_not_after_revision",
        ),
        CheckConstraint(
            "(written_off_at IS NULL AND written_off_revision IS NULL "
            "AND written_off_reason IS NULL "
            "AND written_off_actor_user_id IS NULL) OR "
            "(written_off_at IS NOT NULL AND written_off_revision IS NOT NULL "
            "AND written_off_reason IS NOT NULL "
            "AND written_off_actor_user_id IS NOT NULL)",
            name="ck_debts_written_off_metadata_complete",
        ),
        CheckConstraint(
            "written_off_reason IS NULL OR written_off_reason IN "
            "('collection_exhausted','customer_unreachable',"
            "'insolvency_or_deceased','legal_or_compliance','fraud_or_abuse')",
            name="ck_debts_written_off_reason_allowed",
        ),
        CheckConstraint(
            "written_off_revision IS NULL OR written_off_revision > 0",
            name="ck_debts_written_off_revision_positive",
        ),
        CheckConstraint(
            "written_off_revision IS NULL OR written_off_revision <= revision",
            name="ck_debts_written_off_revision_not_after_revision",
        ),
        CheckConstraint(
            "(written_off_settled_at IS NULL) = (written_off_settled_revision IS NULL)",
            name="ck_debts_written_off_settled_metadata_pair",
        ),
        CheckConstraint(
            "written_off_settled_revision IS NULL OR written_off_settled_revision > 0",
            name="ck_debts_written_off_settled_revision_positive",
        ),
        CheckConstraint(
            "written_off_settled_revision IS NULL "
            "OR written_off_settled_revision <= revision",
            name="ck_debts_written_off_settled_revision_not_after_revision",
        ),
        CheckConstraint(
            "written_off_revision IS NULL OR (overdue_revision IS NOT NULL "
            "AND overdue_revision < written_off_revision)",
            name="ck_debts_written_off_revision_chain",
        ),
        CheckConstraint(
            "written_off_settled_revision IS NULL OR "
            "(written_off_revision IS NOT NULL AND "
            "written_off_revision < written_off_settled_revision)",
            name="ck_debts_written_off_settled_revision_chain",
        ),
        CheckConstraint(
            (
                "rejection_reason IS NULL "
                "OR (char_length(rejection_reason) BETWEEN 1 AND 500 "
                "AND rejection_reason = btrim(rejection_reason) "
                "AND rejection_reason !~ '[[:cntrl:]]')"
            ),
            name="ck_debts_rejection_reason_normalized",
        ),
        CheckConstraint(
            (
                "cancellation_reason IS NULL "
                "OR (char_length(cancellation_reason) BETWEEN 1 AND 500 "
                "AND cancellation_reason = btrim(cancellation_reason) "
                "AND cancellation_reason !~ '[[:cntrl:]]')"
            ),
            name="ck_debts_cancellation_reason_normalized",
        ),
        CheckConstraint(
            (
                f"(status = '{DebtStatus.PENDING.value}' "
                "AND accepted_at IS NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NULL AND expired_at IS NULL "
                "AND paid_at IS NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NULL AND overdue_at IS NULL "
                "AND overdue_revision IS NULL AND written_off_at IS NULL "
                "AND written_off_revision IS NULL AND written_off_reason IS NULL "
                "AND written_off_actor_user_id IS NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.ACTIVE.value}' "
                "AND accepted_at IS NOT NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NULL AND expired_at IS NULL "
                "AND paid_at IS NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NULL AND overdue_at IS NULL "
                "AND overdue_revision IS NULL AND written_off_at IS NULL "
                "AND written_off_revision IS NULL AND written_off_reason IS NULL "
                "AND written_off_actor_user_id IS NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.REJECTED.value}' "
                "AND accepted_at IS NULL AND rejected_at IS NOT NULL "
                "AND cancelled_at IS NULL AND expired_at IS NULL "
                "AND paid_at IS NULL AND cancellation_reason IS NULL "
                "AND overdue_at IS NULL AND overdue_revision IS NULL "
                "AND written_off_at IS NULL AND written_off_revision IS NULL "
                "AND written_off_reason IS NULL "
                "AND written_off_actor_user_id IS NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.CANCELLED.value}' "
                "AND accepted_at IS NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NOT NULL AND expired_at IS NULL "
                "AND paid_at IS NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NOT NULL AND overdue_at IS NULL "
                "AND overdue_revision IS NULL AND written_off_at IS NULL "
                "AND written_off_revision IS NULL AND written_off_reason IS NULL "
                "AND written_off_actor_user_id IS NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.EXPIRED.value}' "
                "AND accepted_at IS NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NULL AND expired_at IS NOT NULL "
                "AND paid_at IS NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NULL AND overdue_at IS NULL "
                "AND overdue_revision IS NULL AND written_off_at IS NULL "
                "AND written_off_revision IS NULL AND written_off_reason IS NULL "
                "AND written_off_actor_user_id IS NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.OVERDUE.value}' "
                "AND accepted_at IS NOT NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NULL AND expired_at IS NULL "
                "AND paid_at IS NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NULL AND overdue_at IS NOT NULL "
                "AND overdue_revision IS NOT NULL AND written_off_at IS NULL "
                "AND written_off_revision IS NULL AND written_off_reason IS NULL "
                "AND written_off_actor_user_id IS NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.PAID.value}' "
                "AND accepted_at IS NOT NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NULL AND expired_at IS NULL "
                "AND paid_at IS NOT NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NULL AND "
                "((overdue_at IS NULL AND overdue_revision IS NULL) OR "
                "(overdue_at IS NOT NULL AND overdue_revision IS NOT NULL "
                "AND overdue_revision < revision)) AND written_off_at IS NULL "
                "AND written_off_revision IS NULL AND written_off_reason IS NULL "
                "AND written_off_actor_user_id IS NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.WRITTEN_OFF.value}' "
                "AND accepted_at IS NOT NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NULL AND expired_at IS NULL "
                "AND paid_at IS NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NULL AND overdue_at IS NOT NULL "
                "AND overdue_revision IS NOT NULL AND written_off_at IS NOT NULL "
                "AND written_off_revision IS NOT NULL "
                "AND written_off_reason IS NOT NULL "
                "AND written_off_actor_user_id IS NOT NULL "
                "AND written_off_settled_at IS NULL "
                "AND written_off_settled_revision IS NULL) "
                f"OR (status = '{DebtStatus.WRITTEN_OFF_SETTLED.value}' "
                "AND accepted_at IS NOT NULL AND rejected_at IS NULL "
                "AND cancelled_at IS NULL AND expired_at IS NULL "
                "AND paid_at IS NULL AND rejection_reason IS NULL "
                "AND cancellation_reason IS NULL AND overdue_at IS NOT NULL "
                "AND overdue_revision IS NOT NULL AND written_off_at IS NOT NULL "
                "AND written_off_revision IS NOT NULL "
                "AND written_off_reason IS NOT NULL "
                "AND written_off_actor_user_id IS NOT NULL "
                "AND written_off_settled_at IS NOT NULL "
                "AND written_off_settled_revision = revision)"
            ),
            name="ck_debts_status_metadata_matches_status",
        ),
        CheckConstraint(
            "pending_expires_at = created_at + INTERVAL '72 hours'",
            name="ck_debts_pending_expires_at_exact",
        ),
        CheckConstraint(
            (
                "updated_at >= created_at "
                "AND (accepted_at IS NULL OR accepted_at >= created_at) "
                "AND (rejected_at IS NULL OR rejected_at >= created_at) "
                "AND (cancelled_at IS NULL OR cancelled_at >= created_at) "
                "AND (expired_at IS NULL OR expired_at >= created_at) "
                "AND (paid_at IS NULL OR (accepted_at IS NOT NULL "
                "AND paid_at >= accepted_at AND updated_at >= paid_at)) "
                "AND (overdue_at IS NULL OR (accepted_at IS NOT NULL "
                "AND overdue_at >= accepted_at AND updated_at >= overdue_at)) "
                "AND (paid_at IS NULL OR overdue_at IS NULL "
                "OR paid_at >= overdue_at) "
                "AND (written_off_at IS NULL OR (overdue_at IS NOT NULL "
                "AND written_off_at >= overdue_at "
                "AND updated_at >= written_off_at)) "
                "AND (written_off_settled_at IS NULL OR "
                "(written_off_at IS NOT NULL "
                "AND written_off_settled_at >= written_off_at "
                "AND updated_at >= written_off_settled_at))"
            ),
            name="ck_debts_timestamp_order",
        ),
        Index(
            "ix_debts_shop_customer_id_created_at_id",
            "shop_customer_id",
            desc("created_at"),
            "id",
        ),
        Index(
            "ix_debts_shop_customer_id_status_due_date_id",
            "shop_customer_id",
            "status",
            "due_date",
            "id",
        ),
        Index(
            "ix_debts_status_pending_expires_at_id",
            "status",
            "pending_expires_at",
            "id",
        ),
        Index(
            "ix_debts_status_due_date_id",
            "status",
            "due_date",
            "id",
        ),
        Index(
            "ix_debts_status_overdue_at_id",
            "status",
            "overdue_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    shop_customer_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "shop_customers.id",
            name="fk_debts_shop_customer_id_shop_customers_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_debts_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    original_amount_uzs: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    discount_basis_points: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    discounted_amount_uzs: Mapped[Decimal] = mapped_column(
        Numeric(18, 0), nullable=False
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    pending_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=DebtStatus.PENDING.value,
        server_default=sqlalchemy_text("'pending'"),
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=sqlalchemy_text("1"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overdue_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overdue_revision: Mapped[int | None] = mapped_column(Integer)
    written_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    written_off_revision: Mapped[int | None] = mapped_column(Integer)
    written_off_reason: Mapped[str | None] = mapped_column(Text)
    written_off_actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_debts_written_off_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
    )
    written_off_settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    written_off_settled_revision: Mapped[int | None] = mapped_column(Integer)
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
            "Debt(id=<redacted>, shop_customer_id=<redacted>, "
            "created_by_user_id=<redacted>, original_amount_uzs=<redacted>, "
            "discount_basis_points=<redacted>, discounted_amount_uzs=<redacted>, "
            "due_date=<redacted>, pending_expires_at=<redacted>, status=<redacted>, "
            "revision=<redacted>, rejection_reason=<redacted>, "
            "cancellation_reason=<redacted>, accepted_at=<redacted>, "
            "rejected_at=<redacted>, cancelled_at=<redacted>, expired_at=<redacted>, "
            "paid_at=<redacted>, overdue_at=<redacted>, "
            "overdue_revision=<redacted>, written_off_at=<redacted>, "
            "written_off_revision=<redacted>, written_off_reason=<redacted>, "
            "written_off_actor_user_id=<redacted>, "
            "written_off_settled_at=<redacted>, "
            "written_off_settled_revision=<redacted>, created_at=<redacted>, "
            "updated_at=<redacted>)"
        )
