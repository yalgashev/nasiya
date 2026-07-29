"""create m7 otp persistence tables

Revision ID: e7f8a9b0c1d2
Revises: d4e5f6a7b8c9
Create Date: 2026-07-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "otp_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("telegram_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("telegram_linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "browser_binding_digest",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("code_mac", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "failed_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose = 'LOGIN'",
            name="ck_otp_challenges_purpose_login",
        ),
        sa.CheckConstraint(
            "browser_binding_digest ~ '^[0-9a-f]{64}$'",
            name="ck_otp_challenges_browser_binding_digest_hmac_sha256_hex",
        ),
        sa.CheckConstraint(
            "code_mac IS NULL OR code_mac ~ '^[0-9a-f]{64}$'",
            name="ck_otp_challenges_code_mac_hmac_sha256_hex",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'PENDING_DISPATCH', 'ACTIVE', 'CONSUMED', 'SUPERSEDED', "
            "'EXPIRED', 'BURNED', 'INVALIDATED'"
            ")",
            name="ck_otp_challenges_status_allowed",
        ),
        sa.CheckConstraint(
            "failed_attempts BETWEEN 0 AND 10",
            name="ck_otp_challenges_failed_attempts_cap",
        ),
        sa.CheckConstraint(
            "("
            "user_id IS NULL "
            "AND telegram_link_id IS NULL "
            "AND telegram_linked_at IS NULL"
            ") OR ("
            "user_id IS NOT NULL "
            "AND telegram_link_id IS NOT NULL "
            "AND telegram_linked_at IS NOT NULL"
            ")",
            name="ck_otp_challenges_real_identity_consistent",
        ),
        sa.CheckConstraint(
            "status != 'PENDING_DISPATCH' OR ("
            "code_mac IS NULL "
            "AND activated_at IS NULL "
            "AND expires_at IS NULL "
            "AND consumed_at IS NULL "
            "AND terminal_at IS NULL"
            ")",
            name="ck_otp_challenges_pending_dispatch_state",
        ),
        sa.CheckConstraint(
            "status != 'ACTIVE' OR ("
            "user_id IS NOT NULL "
            "AND telegram_link_id IS NOT NULL "
            "AND telegram_linked_at IS NOT NULL "
            "AND code_mac IS NOT NULL "
            "AND activated_at IS NOT NULL "
            "AND expires_at IS NOT NULL "
            "AND expires_at > activated_at "
            "AND consumed_at IS NULL "
            "AND terminal_at IS NULL"
            ")",
            name="ck_otp_challenges_active_state",
        ),
        sa.CheckConstraint(
            "("
            "status IN ('PENDING_DISPATCH', 'ACTIVE') "
            "AND terminal_at IS NULL "
            "AND consumed_at IS NULL"
            ") OR ("
            "status = 'CONSUMED' "
            "AND terminal_at IS NOT NULL "
            "AND consumed_at IS NOT NULL"
            ") OR ("
            "status IN ('SUPERSEDED', 'EXPIRED', 'BURNED', 'INVALIDATED') "
            "AND terminal_at IS NOT NULL "
            "AND consumed_at IS NULL"
            ")",
            name="ck_otp_challenges_terminal_state",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "AND (activated_at IS NULL OR activated_at >= created_at) "
            "AND (expires_at IS NULL OR activated_at IS NOT NULL) "
            "AND (consumed_at IS NULL OR activated_at IS NOT NULL) "
            "AND (terminal_at IS NULL OR terminal_at >= created_at)",
            name="ck_otp_challenges_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_link_id"],
            ["telegram_links.id"],
            name="fk_otp_challenges_telegram_link_id_telegram_links_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_otp_challenges_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_otp_challenges"),
    )
    op.create_index(
        "uq_otp_challenges_one_outstanding_per_user_purpose",
        "otp_challenges",
        ["user_id", "purpose"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PENDING_DISPATCH', 'ACTIVE') AND user_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_otp_challenges_one_outstanding_per_browser_purpose",
        "otp_challenges",
        ["browser_binding_digest", "purpose"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING_DISPATCH', 'ACTIVE')"),
    )
    op.create_index(
        "ix_otp_challenges_terminal_at",
        "otp_challenges",
        ["terminal_at"],
        unique=False,
    )

    op.create_table(
        "otp_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'PREPARED', 'SENT', 'FAILED', 'UNKNOWN', "
            "'CANCELLED')",
            name="ck_otp_dispatches_status_allowed",
        ),
        sa.CheckConstraint(
            "locale IN ('uz-Latn', 'ru')",
            name="ck_otp_dispatches_locale_allowed",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_otp_dispatches_failure_code_format",
        ),
        sa.CheckConstraint(
            "("
            "status = 'PENDING' "
            "AND prepared_at IS NULL "
            "AND sent_at IS NULL "
            "AND terminal_at IS NULL "
            "AND failure_code IS NULL"
            ") OR ("
            "status = 'PREPARED' "
            "AND claimed_at IS NOT NULL "
            "AND prepared_at IS NOT NULL "
            "AND sent_at IS NULL "
            "AND terminal_at IS NULL "
            "AND failure_code IS NULL"
            ") OR ("
            "status = 'SENT' "
            "AND prepared_at IS NOT NULL "
            "AND sent_at IS NOT NULL "
            "AND terminal_at IS NOT NULL "
            "AND failure_code IS NULL"
            ") OR ("
            "status IN ('FAILED', 'UNKNOWN') "
            "AND prepared_at IS NOT NULL "
            "AND sent_at IS NULL "
            "AND terminal_at IS NOT NULL "
            "AND failure_code IS NOT NULL"
            ") OR ("
            "status = 'CANCELLED' "
            "AND terminal_at IS NOT NULL "
            "AND sent_at IS NULL "
            "AND failure_code IS NULL"
            ")",
            name="ck_otp_dispatches_state_consistent",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at "
            "AND (claimed_at IS NULL OR claimed_at >= created_at) "
            "AND (prepared_at IS NULL OR claimed_at IS NOT NULL) "
            "AND (sent_at IS NULL OR prepared_at IS NOT NULL) "
            "AND (terminal_at IS NULL OR terminal_at >= created_at)",
            name="ck_otp_dispatches_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["otp_challenges.id"],
            name="fk_otp_dispatches_challenge_id_otp_challenges_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_otp_dispatches"),
        sa.UniqueConstraint(
            "challenge_id",
            name="uq_otp_dispatches_challenge_id",
        ),
    )
    op.create_index(
        "ix_otp_dispatches_status_created_at",
        "otp_dispatches",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_otp_dispatches_terminal_at",
        "otp_dispatches",
        ["terminal_at"],
        unique=False,
    )

    op.create_table(
        "otp_challenge_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("safe_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "action IN ("
            "'ISSUED', 'DISPATCH_PREPARED', 'DISPATCH_RESULT', "
            "'VERIFY_FAILED', 'CONSUMED', 'SUPERSEDED', 'EXPIRED', 'BURNED', "
            "'INVALIDATED_BY_LINK_CHANGE'"
            ")",
            name="ck_otp_challenge_events_action_allowed",
        ),
        sa.CheckConstraint(
            "safe_code IS NULL OR safe_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_otp_challenge_events_safe_code_format",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_otp_challenge_events_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_otp_challenge_events"),
    )
    op.create_index(
        "ix_otp_challenge_events_challenge_id_occurred_at",
        "otp_challenge_events",
        ["challenge_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_otp_challenge_events_occurred_at",
        "otp_challenge_events",
        ["occurred_at"],
        unique=False,
    )

    op.create_table(
        "otp_dispatcher_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
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
            name="ck_otp_dispatcher_state_singleton",
        ),
        sa.CheckConstraint(
            "ready_at IS NULL OR heartbeat_at IS NOT NULL",
            name="ck_otp_dispatcher_state_ready_requires_heartbeat",
        ),
        sa.CheckConstraint(
            "heartbeat_at IS NULL OR ready_at IS NULL OR heartbeat_at >= ready_at",
            name="ck_otp_dispatcher_state_heartbeat_not_before_ready",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_otp_dispatcher_state"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("otp_dispatcher_state")
    op.drop_index(
        "ix_otp_challenge_events_occurred_at",
        table_name="otp_challenge_events",
    )
    op.drop_index(
        "ix_otp_challenge_events_challenge_id_occurred_at",
        table_name="otp_challenge_events",
    )
    op.drop_table("otp_challenge_events")
    op.drop_index(
        "ix_otp_dispatches_terminal_at",
        table_name="otp_dispatches",
    )
    op.drop_index(
        "ix_otp_dispatches_status_created_at",
        table_name="otp_dispatches",
    )
    op.drop_table("otp_dispatches")
    op.drop_index(
        "ix_otp_challenges_terminal_at",
        table_name="otp_challenges",
    )
    op.drop_index(
        "uq_otp_challenges_one_outstanding_per_browser_purpose",
        table_name="otp_challenges",
    )
    op.drop_index(
        "uq_otp_challenges_one_outstanding_per_user_purpose",
        table_name="otp_challenges",
    )
    op.drop_table("otp_challenges")
