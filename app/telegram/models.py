from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
        Index(
            "uq_telegram_link_tokens_one_outstanding_per_user",
            "user_id",
            unique=True,
            postgresql_where=sqlalchemy_text(
                "consumed_at IS NULL AND invalidated_at IS NULL"
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
