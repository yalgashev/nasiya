"""create m6 telegram polling tables

Revision ID: d4e5f6a7b8c9
Revises: a6b4c2d8e9f1
Create Date: 2026-07-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "a6b4c2d8e9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "telegram_polling_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column(
            "next_offset",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "id = 1",
            name="ck_telegram_polling_state_singleton",
        ),
        sa.CheckConstraint(
            "next_offset >= 0",
            name="ck_telegram_polling_state_next_offset_nonnegative",
        ),
        sa.CheckConstraint(
            "ready_at IS NULL OR heartbeat_at IS NOT NULL",
            name="ck_telegram_polling_state_ready_requires_heartbeat",
        ),
        sa.CheckConstraint(
            "heartbeat_at IS NULL OR ready_at IS NULL OR heartbeat_at >= ready_at",
            name="ck_telegram_polling_state_heartbeat_not_before_ready",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_polling_state"),
    )

    op.create_table(
        "telegram_update_failures",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_count", sa.SmallInteger(), nullable=False),
        sa.Column("failure_code", sa.String(length=64), nullable=False),
        sa.Column(
            "first_failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_failed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "update_id >= 0",
            name="ck_telegram_update_failures_update_id_nonnegative",
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 1 AND 5",
            name="ck_telegram_update_failures_attempt_count",
        ),
        sa.CheckConstraint(
            "failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_telegram_update_failures_code_format",
        ),
        sa.CheckConstraint(
            "last_failed_at >= first_failed_at",
            name="ck_telegram_update_failures_time_order",
        ),
        sa.CheckConstraint(
            "(attempt_count < 5 AND quarantined_at IS NULL) "
            "OR (attempt_count = 5 AND quarantined_at IS NOT NULL)",
            name="ck_telegram_update_failures_quarantine_state",
        ),
        sa.CheckConstraint(
            "quarantined_at IS NULL OR quarantined_at >= last_failed_at",
            name="ck_telegram_update_failures_quarantine_time",
        ),
        sa.PrimaryKeyConstraint("update_id", name="pk_telegram_update_failures"),
    )
    op.create_index(
        "ix_telegram_update_failures_quarantined_at",
        "telegram_update_failures",
        ["quarantined_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_telegram_update_failures_quarantined_at",
        table_name="telegram_update_failures",
    )
    op.drop_table("telegram_update_failures")
    op.drop_table("telegram_polling_state")
