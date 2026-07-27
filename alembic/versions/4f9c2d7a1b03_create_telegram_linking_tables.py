"""create telegram linking tables

Revision ID: 4f9c2d7a1b03
Revises: b1f3a7c9d2e4
Create Date: 2026-07-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4f9c2d7a1b03"
down_revision: str | Sequence[str] | None = "b1f3a7c9d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "telegram_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "("
            "telegram_chat_id IS NOT NULL "
            "AND unlinked_at IS NULL"
            ") OR ("
            "telegram_chat_id IS NULL "
            "AND unlinked_at IS NOT NULL"
            ")",
            name="ck_telegram_links_state_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_links_user_id_users_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_links")),
        sa.UniqueConstraint("user_id", name="uq_telegram_links_user_id"),
    )
    op.create_index(
        "uq_telegram_links_active_chat_id",
        "telegram_links",
        ["telegram_chat_id"],
        unique=True,
        postgresql_where=sa.text(
            "telegram_chat_id IS NOT NULL AND unlinked_at IS NULL"
        ),
    )

    op.create_table(
        "telegram_link_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_telegram_link_tokens_token_hash_sha256_hex",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_telegram_link_tokens_expires_after_created",
        ),
        sa.CheckConstraint(
            "NOT (consumed_at IS NOT NULL AND invalidated_at IS NOT NULL)",
            name="ck_telegram_link_tokens_terminal_state_exclusive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_link_tokens_user_id_users_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_link_tokens")),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_telegram_link_tokens_token_hash",
        ),
    )
    op.create_index(
        "uq_telegram_link_tokens_one_outstanding_per_user",
        "telegram_link_tokens",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND invalidated_at IS NULL"),
    )

    op.create_table(
        "telegram_link_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('linked', 'unlinked', 'relinked')",
            name="ck_telegram_link_events_action_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_link_events_user_id_users_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_link_events")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("telegram_link_events")
    op.drop_index(
        "uq_telegram_link_tokens_one_outstanding_per_user",
        table_name="telegram_link_tokens",
        postgresql_where=sa.text("consumed_at IS NULL AND invalidated_at IS NULL"),
    )
    op.drop_table("telegram_link_tokens")
    op.drop_index(
        "uq_telegram_links_active_chat_id",
        table_name="telegram_links",
        postgresql_where=sa.text(
            "telegram_chat_id IS NOT NULL AND unlinked_at IS NULL"
        ),
    )
    op.drop_table("telegram_links")
