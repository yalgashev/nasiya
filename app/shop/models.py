from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction


def utc_now() -> datetime:
    return datetime.now(UTC)


class Shop(Base):
    __tablename__ = "shops"
    __table_args__ = (
        CheckConstraint(
            f"status IN ('{ShopStatus.ACTIVE.value}', '{ShopStatus.SUSPENDED.value}')",
            name="ck_shops_status_allowed",
        ),
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 2 AND 120",
            name="ck_shops_name_trimmed_length",
        ),
        CheckConstraint(
            "length(btrim(phone)) > 0",
            name="ck_shops_phone_not_blank",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    address_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=ShopStatus.ACTIVE.value,
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


class ShopStaff(Base):
    __tablename__ = "shop_staff"
    __table_args__ = (
        UniqueConstraint("shop_id", "user_id", name="uq_shop_staff_shop_id_user_id"),
        CheckConstraint(
            (
                f"role IN ('{ShopRole.OWNER.value}', "
                f"'{ShopRole.MANAGER.value}', "
                f"'{ShopRole.CASHIER.value}')"
            ),
            name="ck_shop_staff_role_allowed",
        ),
        CheckConstraint(
            (
                "(is_active = true AND revoked_at IS NULL) "
                "OR (is_active = false AND revoked_at IS NOT NULL)"
            ),
            name="ck_shop_staff_active_revoked_consistent",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    shop_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ShopStatusEvent(Base):
    __tablename__ = "shop_status_events"
    __table_args__ = (
        CheckConstraint(
            (
                f"action IN ('{ShopStatusAction.ACTIVATED.value}', "
                f"'{ShopStatusAction.SUSPENDED.value}', "
                f"'{ShopStatusAction.REACTIVATED.value}')"
            ),
            name="ck_shop_status_events_action_allowed",
        ),
        CheckConstraint(
            (
                f"(action = '{ShopStatusAction.ACTIVATED.value}' "
                "AND reason IS NULL) "
                f"OR (action IN ('{ShopStatusAction.SUSPENDED.value}', "
                f"'{ShopStatusAction.REACTIVATED.value}') "
                "AND reason IS NOT NULL "
                "AND length(btrim(reason)) > 0)"
            ),
            name="ck_shop_status_events_reason_matches_action",
        ),
        Index(
            "ix_shop_status_events_shop_id_created_at",
            "shop_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    shop_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class ShopStaffEvent(Base):
    __tablename__ = "shop_staff_events"
    __table_args__ = (
        CheckConstraint(
            (
                f"action IN ('{ShopStaffAction.ADDED.value}', "
                f"'{ShopStaffAction.ROLE_CHANGED.value}', "
                f"'{ShopStaffAction.REVOKED.value}')"
            ),
            name="ck_shop_staff_events_action_allowed",
        ),
        CheckConstraint(
            (
                "old_role IS NULL "
                f"OR old_role IN ('{ShopRole.OWNER.value}', "
                f"'{ShopRole.MANAGER.value}', "
                f"'{ShopRole.CASHIER.value}')"
            ),
            name="ck_shop_staff_events_old_role_allowed",
        ),
        CheckConstraint(
            (
                "new_role IS NULL "
                f"OR new_role IN ('{ShopRole.OWNER.value}', "
                f"'{ShopRole.MANAGER.value}', "
                f"'{ShopRole.CASHIER.value}')"
            ),
            name="ck_shop_staff_events_new_role_allowed",
        ),
        CheckConstraint(
            (
                f"(action = '{ShopStaffAction.ADDED.value}' "
                "AND old_role IS NULL "
                "AND new_role IS NOT NULL) "
                f"OR (action = '{ShopStaffAction.ROLE_CHANGED.value}' "
                "AND old_role IS NOT NULL "
                "AND new_role IS NOT NULL "
                "AND old_role <> new_role) "
                f"OR (action = '{ShopStaffAction.REVOKED.value}' "
                "AND old_role IS NOT NULL "
                "AND new_role IS NULL)"
            ),
            name="ck_shop_staff_events_role_transition_matches_action",
        ),
        Index(
            "ix_shop_staff_events_shop_id_created_at",
            "shop_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    shop_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject_user_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    old_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
