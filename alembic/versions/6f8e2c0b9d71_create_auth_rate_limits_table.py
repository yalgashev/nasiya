"""create auth rate limits table

Revision ID: 6f8e2c0b9d71
Revises: 352b864d3118
Create Date: 2026-07-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6f8e2c0b9d71"
down_revision: str | Sequence[str] | None = "352b864d3118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "auth_rate_limits",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_auth_rate_limits_key_hash_hmac_sha256_hex",
        ),
        sa.CheckConstraint(
            "attempt_count > 0",
            name="ck_auth_rate_limits_attempt_count_positive",
        ),
        sa.PrimaryKeyConstraint(
            "scope",
            "key_hash",
            name=op.f("pk_auth_rate_limits"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("auth_rate_limits")
