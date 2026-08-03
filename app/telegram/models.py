from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sqlalchemy_text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class TelegramLink(Base):
    __tablename__ = "telegram_links"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_telegram_links_user_id"),
        CheckConstraint(
            "("
            "telegram_chat_id IS NOT NULL "
            "AND unlinked_at IS NULL"
            ") OR ("
            "telegram_chat_id IS NULL "
            "AND unlinked_at IS NOT NULL"
            ")",
            name="ck_telegram_links_state_consistent",
        ),
        CheckConstraint(
            "phone_verified_at IS NULL OR ("
            "unlinked_at IS NULL AND phone_verified_at = linked_at"
            ")",
            name="ck_telegram_links_phone_verification_consistent",
        ),
        Index(
            "uq_telegram_links_active_chat_id",
            "telegram_chat_id",
            unique=True,
            postgresql_where=sqlalchemy_text(
                "telegram_chat_id IS NOT NULL AND unlinked_at IS NULL"
            ),
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
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    unlinked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class TelegramLinkToken(Base):
    __tablename__ = "telegram_link_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_telegram_link_tokens_token_hash"),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_telegram_link_tokens_token_hash_sha256_hex",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_telegram_link_tokens_expires_after_created",
        ),
        CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND invalidated_at IS NOT NULL)",
            name="ck_telegram_link_tokens_terminal_state_exclusive",
        ),
        CheckConstraint(
            "pending_contact_binding_mac IS NULL OR "
            "pending_contact_binding_mac ~ '^[0-9a-f]{64}$'",
            name="ck_telegram_link_tokens_pending_contact_binding_mac_format",
        ),
        CheckConstraint(
            "(pending_contact_binding_mac IS NULL) = "
            "(contact_requested_at IS NULL) AND ("
            "consumed_at IS NULL AND invalidated_at IS NULL "
            "OR pending_contact_binding_mac IS NULL"
            ")",
            name="ck_telegram_link_tokens_pending_contact_state_consistent",
        ),
        CheckConstraint(
            "contact_requested_at IS NULL OR contact_requested_at >= created_at",
            name="ck_telegram_link_tokens_pending_contact_timestamp_order",
        ),
        Index(
            "uq_telegram_link_tokens_one_outstanding_per_user",
            "user_id",
            unique=True,
            postgresql_where=sqlalchemy_text(
                "consumed_at IS NULL AND invalidated_at IS NULL"
            ),
        ),
        Index(
            "uq_telegram_link_tokens_pending_contact_binding_mac_outstanding",
            "pending_contact_binding_mac",
            unique=True,
            postgresql_where=sqlalchemy_text(
                "pending_contact_binding_mac IS NOT NULL "
                "AND consumed_at IS NULL AND invalidated_at IS NULL"
            ),
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
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    pending_contact_binding_mac: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    contact_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class TelegramLinkEvent(Base):
    __tablename__ = "telegram_link_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('linked', 'unlinked', 'relinked')",
            name="ck_telegram_link_events_action_allowed",
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
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )


class TelegramPollingState(Base):
    __tablename__ = "telegram_polling_state"
    __table_args__ = (
        CheckConstraint(
            "id = 1",
            name="ck_telegram_polling_state_singleton",
        ),
        CheckConstraint(
            "next_offset >= 0",
            name="ck_telegram_polling_state_next_offset_nonnegative",
        ),
        CheckConstraint(
            "ready_at IS NULL OR heartbeat_at IS NOT NULL",
            name="ck_telegram_polling_state_ready_requires_heartbeat",
        ),
        CheckConstraint(
            "heartbeat_at IS NULL OR ready_at IS NULL OR heartbeat_at >= ready_at",
            name="ck_telegram_polling_state_heartbeat_not_before_ready",
        ),
    )

    id: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        default=1,
    )
    next_offset: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=sqlalchemy_text("0"),
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=sqlalchemy_text("CURRENT_TIMESTAMP"),
    )


class TelegramUpdateFailure(Base):
    __tablename__ = "telegram_update_failures"
    __table_args__ = (
        CheckConstraint(
            "update_id >= 0",
            name="ck_telegram_update_failures_update_id_nonnegative",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 1 AND 5",
            name="ck_telegram_update_failures_attempt_count",
        ),
        CheckConstraint(
            "failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_telegram_update_failures_code_format",
        ),
        CheckConstraint(
            "last_failed_at >= first_failed_at",
            name="ck_telegram_update_failures_time_order",
        ),
        CheckConstraint(
            "(attempt_count < 5 AND quarantined_at IS NULL) "
            "OR (attempt_count = 5 AND quarantined_at IS NOT NULL)",
            name="ck_telegram_update_failures_quarantine_state",
        ),
        CheckConstraint(
            "quarantined_at IS NULL OR quarantined_at >= last_failed_at",
            name="ck_telegram_update_failures_quarantine_time",
        ),
        Index(
            "ix_telegram_update_failures_quarantined_at",
            "quarantined_at",
        ),
    )

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    failure_code: Mapped[str] = mapped_column(String(64), nullable=False)
    first_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    quarantined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
