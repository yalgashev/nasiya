"""create m5 shop tables

Revision ID: a6b4c2d8e9f1
Revises: 4f9c2d7a1b03
Create Date: 2026-07-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6b4c2d8e9f1"
down_revision: str | Sequence[str] | None = "4f9c2d7a1b03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "shops",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("address_text", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
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
            "status IN ('active', 'suspended')",
            name="ck_shops_status_allowed",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 2 AND 120",
            name="ck_shops_name_trimmed_length",
        ),
        sa.CheckConstraint(
            "length(btrim(phone)) > 0",
            name="ck_shops_phone_not_blank",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shops")),
    )
    op.create_index(
        op.f("ix_shops_phone"),
        "shops",
        ["phone"],
        unique=False,
    )

    op.create_table(
        "shop_staff",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
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
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('owner', 'manager', 'cashier')",
            name="ck_shop_staff_role_allowed",
        ),
        sa.CheckConstraint(
            "(is_active = true AND revoked_at IS NULL) "
            "OR (is_active = false AND revoked_at IS NOT NULL)",
            name="ck_shop_staff_active_revoked_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name=op.f("fk_shop_staff_shop_id_shops_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_shop_staff_user_id_users_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_staff")),
        sa.UniqueConstraint(
            "shop_id",
            "user_id",
            name="uq_shop_staff_shop_id_user_id",
        ),
    )
    op.create_index(
        op.f("ix_shop_staff_shop_id"),
        "shop_staff",
        ["shop_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_shop_staff_user_id"),
        "shop_staff",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "shop_status_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('activated', 'suspended', 'reactivated')",
            name="ck_shop_status_events_action_allowed",
        ),
        sa.CheckConstraint(
            "(action = 'activated' AND reason IS NULL) "
            "OR (action IN ('suspended', 'reactivated') "
            "AND reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="ck_shop_status_events_reason_matches_action",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name=op.f("fk_shop_status_events_shop_id_shops_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_shop_status_events_actor_user_id_users_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_status_events")),
    )
    op.create_index(
        "ix_shop_status_events_shop_id_created_at",
        "shop_status_events",
        ["shop_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "shop_staff_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("old_role", sa.Text(), nullable=True),
        sa.Column("new_role", sa.Text(), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('added', 'role_changed', 'revoked')",
            name="ck_shop_staff_events_action_allowed",
        ),
        sa.CheckConstraint(
            "old_role IS NULL OR old_role IN ('owner', 'manager', 'cashier')",
            name="ck_shop_staff_events_old_role_allowed",
        ),
        sa.CheckConstraint(
            "new_role IS NULL OR new_role IN ('owner', 'manager', 'cashier')",
            name="ck_shop_staff_events_new_role_allowed",
        ),
        sa.CheckConstraint(
            "(action = 'added' AND old_role IS NULL AND new_role IS NOT NULL) "
            "OR (action = 'role_changed' AND old_role IS NOT NULL "
            "AND new_role IS NOT NULL AND old_role <> new_role) "
            "OR (action = 'revoked' AND old_role IS NOT NULL AND new_role IS NULL)",
            name="ck_shop_staff_events_role_transition_matches_action",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name=op.f("fk_shop_staff_events_shop_id_shops_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name=op.f("fk_shop_staff_events_subject_user_id_users_id"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_shop_staff_events_actor_user_id_users_id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_staff_events")),
    )
    op.create_index(
        "ix_shop_staff_events_shop_id_created_at",
        "shop_staff_events",
        ["shop_id", "created_at"],
        unique=False,
    )

    op.add_column(
        "sessions",
        sa.Column("active_shop_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_sessions_active_shop_id_shops_id"),
        "sessions",
        "shops",
        ["active_shop_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        op.f("fk_sessions_active_shop_id_shops_id"),
        "sessions",
        type_="foreignkey",
    )
    op.drop_column("sessions", "active_shop_id")

    op.drop_index(
        "ix_shop_staff_events_shop_id_created_at",
        table_name="shop_staff_events",
    )
    op.drop_table("shop_staff_events")

    op.drop_index(
        "ix_shop_status_events_shop_id_created_at",
        table_name="shop_status_events",
    )
    op.drop_table("shop_status_events")

    op.drop_index(op.f("ix_shop_staff_user_id"), table_name="shop_staff")
    op.drop_index(op.f("ix_shop_staff_shop_id"), table_name="shop_staff")
    op.drop_table("shop_staff")

    op.drop_index(op.f("ix_shops_phone"), table_name="shops")
    op.drop_table("shops")
