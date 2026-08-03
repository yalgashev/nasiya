"""add Telegram self-phone verification

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _validate(table_name: str, constraint_name: str) -> None:
    op.execute(
        sa.text(f'ALTER TABLE "{table_name}" VALIDATE CONSTRAINT "{constraint_name}"')
    )


def upgrade() -> None:
    connection = op.get_bind()
    has_noncanonical_phone = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM users WHERE phone !~ '^\\+998[0-9]{9}$')")
    )
    if has_noncanonical_phone:
        raise RuntimeError("CR-M11-02 upgrade blocked: noncanonical user phone exists")

    op.add_column(
        "telegram_link_tokens",
        sa.Column("pending_contact_binding_mac", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "telegram_link_tokens",
        sa.Column("contact_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telegram_links",
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_check_constraint(
        "ck_telegram_link_tokens_pending_contact_binding_mac_format",
        "telegram_link_tokens",
        "pending_contact_binding_mac IS NULL OR "
        "pending_contact_binding_mac ~ '^[0-9a-f]{64}$'",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_telegram_link_tokens_pending_contact_state_consistent",
        "telegram_link_tokens",
        "(pending_contact_binding_mac IS NULL) = "
        "(contact_requested_at IS NULL) AND ("
        "consumed_at IS NULL AND invalidated_at IS NULL "
        "OR pending_contact_binding_mac IS NULL"
        ")",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_telegram_link_tokens_pending_contact_timestamp_order",
        "telegram_link_tokens",
        "contact_requested_at IS NULL OR contact_requested_at >= created_at",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_telegram_links_phone_verification_consistent",
        "telegram_links",
        "phone_verified_at IS NULL OR ("
        "unlinked_at IS NULL AND phone_verified_at = linked_at"
        ")",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_users_phone_canonical_uz_e164",
        "users",
        "phone ~ '^\\+998[0-9]{9}$'",
        postgresql_not_valid=True,
    )

    for table_name, constraint_name in (
        (
            "telegram_link_tokens",
            "ck_telegram_link_tokens_pending_contact_binding_mac_format",
        ),
        (
            "telegram_link_tokens",
            "ck_telegram_link_tokens_pending_contact_state_consistent",
        ),
        (
            "telegram_link_tokens",
            "ck_telegram_link_tokens_pending_contact_timestamp_order",
        ),
        ("telegram_links", "ck_telegram_links_phone_verification_consistent"),
        ("users", "ck_users_phone_canonical_uz_e164"),
    ):
        _validate(table_name, constraint_name)

    op.create_index(
        "uq_telegram_link_tokens_pending_contact_binding_mac_outstanding",
        "telegram_link_tokens",
        ["pending_contact_binding_mac"],
        unique=True,
        postgresql_where=sa.text(
            "pending_contact_binding_mac IS NOT NULL "
            "AND consumed_at IS NULL AND invalidated_at IS NULL"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    has_recovery_state = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM telegram_link_tokens "
            "WHERE pending_contact_binding_mac IS NOT NULL "
            "OR contact_requested_at IS NOT NULL"
            ") OR EXISTS ("
            "SELECT 1 FROM telegram_links WHERE phone_verified_at IS NOT NULL"
            ")"
        )
    )
    if has_recovery_state:
        raise RuntimeError(
            "CR-M11-02 downgrade blocked: pending or verified Telegram state exists"
        )

    op.drop_index(
        "uq_telegram_link_tokens_pending_contact_binding_mac_outstanding",
        table_name="telegram_link_tokens",
    )
    op.drop_constraint(
        "ck_telegram_link_tokens_pending_contact_timestamp_order",
        "telegram_link_tokens",
        type_="check",
    )
    op.drop_constraint(
        "ck_telegram_link_tokens_pending_contact_state_consistent",
        "telegram_link_tokens",
        type_="check",
    )
    op.drop_constraint(
        "ck_telegram_link_tokens_pending_contact_binding_mac_format",
        "telegram_link_tokens",
        type_="check",
    )
    op.drop_constraint(
        "ck_telegram_links_phone_verification_consistent",
        "telegram_links",
        type_="check",
    )
    op.drop_constraint(
        "ck_users_phone_canonical_uz_e164",
        "users",
        type_="check",
    )
    op.drop_column("telegram_links", "phone_verified_at")
    op.drop_column("telegram_link_tokens", "contact_requested_at")
    op.drop_column("telegram_link_tokens", "pending_contact_binding_mac")
