"""PostgreSQL metadata for the bounded, PII-free ShopCustomer relationship."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.values import (
    DEFAULT_CREDIT_LIMIT_UZS,
    DEFAULT_MAX_OPEN_DEBTS,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class ShopCustomer(Base):
    __tablename__ = "shop_customers"
    __table_args__ = (
        UniqueConstraint(
            "shop_id",
            "customer_id",
            name="uq_shop_customers_shop_id_customer_id",
        ),
        CheckConstraint(
            "credit_limit_uzs BETWEEN 0 AND 1000000000000",
            name="ck_shop_customers_credit_limit_uzs_bounds",
        ),
        CheckConstraint(
            "max_open_debts BETWEEN 1 AND 100",
            name="ck_shop_customers_max_open_debts_bounds",
        ),
        CheckConstraint(
            "list_status IN ('normal', 'whitelisted', 'blacklisted')",
            name="ck_shop_customers_list_status_allowed",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_shop_customers_revision_positive",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_shop_customers_timestamp_order",
        ),
        Index(
            "ix_shop_customers_shop_id_created_at_id",
            "shop_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_shop_customers_customer_id_created_at_id",
            "customer_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    shop_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "shops.id",
            name="fk_shop_customers_shop_id_shops_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "customers.id",
            name="fk_shop_customers_customer_id_customers_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    credit_limit_uzs: Mapped[Decimal] = mapped_column(
        Numeric(18, 0),
        nullable=False,
        default=DEFAULT_CREDIT_LIMIT_UZS.value,
    )
    max_open_debts: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=DEFAULT_MAX_OPEN_DEBTS.value,
    )
    list_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=ShopCustomerListStatus.NORMAL.value,
        server_default=sqlalchemy_text("'normal'"),
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=sqlalchemy_text("1"),
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_shop_customers_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
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
        onupdate=utc_now,
    )

    def __repr__(self) -> str:
        return (
            "ShopCustomer("
            "id=<redacted>, shop_id=<redacted>, customer_id=<redacted>, "
            "credit_limit_uzs=<redacted>, max_open_debts=<redacted>, "
            "list_status=<redacted>, revision=<redacted>, "
            "created_by_user_id=<redacted>, created_at=<redacted>, "
            "updated_at=<redacted>)"
        )
