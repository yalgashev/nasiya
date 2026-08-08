# ruff: noqa: E501
"""add pending debt persistence

Revision ID: f4a5b6c7d8e
Revises: e3f4a5b6c7d8
Create Date: 2026-08-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4a5b6c7d8e"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M13_AUDIT_EVENTS = (
    "debt.created",
    "debt.accepted",
    "debt.rejected",
    "debt.cancelled",
    "debt.expired",
)


def _replace_audit_checks(*, include_m13: bool) -> None:
    # M12's exact payload contract remains intact; M13 adds only five typed clauses.
    if not include_m13:
        from importlib.util import module_from_spec, spec_from_file_location
        from pathlib import Path

        op.drop_constraint(
            "ck_audit_log_actor_matches_event", "audit_log", type_="check"
        )
        op.create_check_constraint(
            "ck_audit_log_actor_matches_event",
            "audit_log",
            "(event_type = 'platform_admin.bootstrapped' AND actor_kind = 'SYSTEM' "
            "AND actor_user_id IS NULL) OR (event_type <> "
            "'platform_admin.bootstrapped' AND actor_kind = 'USER' "
            "AND actor_user_id IS NOT NULL)",
        )
        spec = spec_from_file_location(
            "m12_audit_checks",
            Path(__file__).with_name("e3f4a5b6c7d8_add_shop_customer_foundation.py"),
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("M12 audit migration source is unavailable")
        m12 = module_from_spec(spec)
        spec.loader.exec_module(m12)
        m12._replace_audit_checks(include_m12=True)
        return
    from app.audit.models import _AUDIT_PAYLOAD_EXACT_SHAPE_SQL

    for name in (
        "ck_audit_log_payload_exact_shape",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_matches_event",
    ):
        op.drop_constraint(name, "audit_log", type_="check")
    old_events = (
        "platform_admin.bootstrapped",
        "offer.version_created",
        "offer.text_updated",
        "offer.version_approved",
        "offer.version_made_current",
        "offer.version_demoted",
        "offer.registration_accepted",
        "customer.identity_saved",
        "customer.document_attached",
        "customer.document_superseded",
        "customer.document_access_granted",
        "customer.activated",
        "shop_customer.linked",
        "shop_customer.policy_updated",
        "shop.customer_defaults_updated",
    )
    events = old_events + _M13_AUDIT_EVENTS if include_m13 else old_events
    objects = (
        "user",
        "offer_version",
        "offer_text",
        "offer_acceptance",
        "customer_identity",
        "customer_document",
        "customer",
        "shop_customer",
        "shop",
    ) + (("debt",) if include_m13 else ())
    op.create_check_constraint(
        "ck_audit_log_event_type_allowed",
        "audit_log",
        f"event_type IN ({', '.join(repr(item) for item in events)})",
    )
    op.create_check_constraint(
        "ck_audit_log_object_type_allowed",
        "audit_log",
        f"object_type IN ({', '.join(repr(item) for item in objects)})",
    )
    system_events = "'platform_admin.bootstrapped'" + (
        ", 'debt.expired'" if include_m13 else ""
    )
    op.create_check_constraint(
        "ck_audit_log_actor_matches_event",
        "audit_log",
        f"(event_type IN ({system_events}) AND actor_kind = 'SYSTEM' AND actor_user_id IS NULL) OR (event_type NOT IN ({system_events}) AND actor_kind = 'USER' AND actor_user_id IS NOT NULL)",
    )
    mapping = "(event_type = 'platform_admin.bootstrapped' AND object_type = 'user') OR (event_type IN ('offer.version_created','offer.version_approved','offer.version_made_current','offer.version_demoted') AND object_type = 'offer_version') OR (event_type = 'offer.text_updated' AND object_type = 'offer_text') OR (event_type = 'offer.registration_accepted' AND object_type = 'offer_acceptance') OR (event_type = 'customer.identity_saved' AND object_type = 'customer_identity') OR (event_type IN ('customer.document_attached','customer.document_superseded','customer.document_access_granted') AND object_type = 'customer_document') OR (event_type = 'customer.activated' AND object_type = 'customer') OR (event_type IN ('shop_customer.linked','shop_customer.policy_updated') AND object_type = 'shop_customer') OR (event_type = 'shop.customer_defaults_updated' AND object_type = 'shop')"
    if include_m13:
        mapping += " OR (event_type IN ('debt.created','debt.accepted','debt.rejected','debt.cancelled','debt.expired') AND object_type = 'debt')"
    op.create_check_constraint(
        "ck_audit_log_object_matches_event", "audit_log", mapping
    )
    op.create_check_constraint(
        "ck_audit_log_payload_exact_shape", "audit_log", _AUDIT_PAYLOAD_EXACT_SHAPE_SQL
    )


def upgrade() -> None:
    op.create_table(
        "debts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("shop_customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_amount_uzs", sa.Numeric(18, 0), nullable=False),
        sa.Column("discount_basis_points", sa.SmallInteger(), nullable=False),
        sa.Column("discounted_amount_uzs", sa.Numeric(18, 0), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("pending_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column(
            "revision", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column("rejection_reason", sa.Text()),
        sa.Column("cancellation_reason", sa.Text()),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
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
            "original_amount_uzs BETWEEN 1 AND 1000000000000",
            name="ck_debts_original_amount_uzs_bounds",
        ),
        sa.CheckConstraint(
            "discount_basis_points BETWEEN 0 AND 10000",
            name="ck_debts_discount_basis_points_bounds",
        ),
        sa.CheckConstraint(
            "discounted_amount_uzs BETWEEN 1 AND original_amount_uzs",
            name="ck_debts_discounted_amount_uzs_bounds",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'rejected', 'cancelled', 'expired')",
            name="ck_debts_status_allowed",
        ),
        sa.CheckConstraint("revision > 0", name="ck_debts_revision_positive"),
        sa.CheckConstraint(
            "rejection_reason IS NULL OR (char_length(rejection_reason) BETWEEN 1 AND 500 AND rejection_reason = btrim(rejection_reason) AND rejection_reason !~ '[[:cntrl:]]')",
            name="ck_debts_rejection_reason_normalized",
        ),
        sa.CheckConstraint(
            "cancellation_reason IS NULL OR (char_length(cancellation_reason) BETWEEN 1 AND 500 AND cancellation_reason = btrim(cancellation_reason) AND cancellation_reason !~ '[[:cntrl:]]')",
            name="ck_debts_cancellation_reason_normalized",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL) OR (status = 'active' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL) OR (status = 'rejected' AND accepted_at IS NULL AND rejected_at IS NOT NULL AND cancelled_at IS NULL AND expired_at IS NULL AND cancellation_reason IS NULL) OR (status = 'cancelled' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NOT NULL AND expired_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NOT NULL) OR (status = 'expired' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NOT NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL)",
            name="ck_debts_status_metadata_matches_status",
        ),
        sa.CheckConstraint(
            "pending_expires_at = created_at + INTERVAL '72 hours'",
            name="ck_debts_pending_expires_at_exact",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at AND (accepted_at IS NULL OR accepted_at >= created_at) AND (rejected_at IS NULL OR rejected_at >= created_at) AND (cancelled_at IS NULL OR cancelled_at >= created_at) AND (expired_at IS NULL OR expired_at >= created_at)",
            name="ck_debts_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["shop_customer_id"],
            ["shop_customers.id"],
            name="fk_debts_shop_customer_id_shop_customers_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_debts_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_debts"),
    )
    op.create_index(
        "ix_debts_shop_customer_id_created_at_id",
        "debts",
        ["shop_customer_id", sa.text("created_at DESC"), "id"],
    )
    op.create_index(
        "ix_debts_shop_customer_id_status_due_date_id",
        "debts",
        ["shop_customer_id", "status", "due_date", "id"],
    )
    op.create_index(
        "ix_debts_status_pending_expires_at_id",
        "debts",
        ["status", "pending_expires_at", "id"],
    )
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.String(100), nullable=False),
        sa.Column("key_digest", sa.CHAR(64), nullable=False),
        sa.Column("request_hash", sa.CHAR(64), nullable=False),
        sa.Column("result_object_type", sa.String(32), nullable=False),
        sa.Column("result_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "endpoint = 'shop.debts.create'",
            name="ck_idempotency_keys_endpoint_allowed",
        ),
        sa.CheckConstraint(
            "key_digest ~ '^[0-9a-f]{64}$'",
            name="ck_idempotency_keys_key_digest_sha256_hex",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_idempotency_keys_request_hash_sha256_hex",
        ),
        sa.CheckConstraint(
            "result_object_type = 'debt'",
            name="ck_idempotency_keys_result_object_type_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_idempotency_keys_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_keys"),
        sa.UniqueConstraint(
            "actor_user_id",
            "endpoint",
            "key_digest",
            name="uq_idempotency_keys_actor_user_id_endpoint_key_digest",
        ),
    )
    op.add_column(
        "offer_acceptances",
        sa.Column("debt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_offer_acceptances_debt_id_debts_id",
        "offer_acceptances",
        "debts",
        ["debt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_offer_acceptances_purpose_debt_id_consistent",
        "offer_acceptances",
        "(purpose = 'REGISTRATION' AND debt_id IS NULL) OR (purpose = 'DEBT_ACCEPTANCE' AND debt_id IS NOT NULL)",
    )
    op.drop_constraint(
        "uq_offer_acceptances_user_id_offer_text_id_purpose",
        "offer_acceptances",
        type_="unique",
    )
    op.create_index(
        "uq_offer_acceptances_user_id_offer_text_id_purpose",
        "offer_acceptances",
        ["user_id", "offer_text_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("purpose = 'REGISTRATION' AND debt_id IS NULL"),
    )
    op.create_index(
        "uq_offer_acceptances_debt_id",
        "offer_acceptances",
        ["debt_id"],
        unique=True,
        postgresql_where=sa.text("debt_id IS NOT NULL"),
    )
    _replace_audit_checks(include_m13=True)


def downgrade() -> None:
    bind = op.get_bind()
    guards = (
        (
            "SELECT EXISTS (SELECT 1 FROM debts)",
            "M13 downgrade blocked: debt state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM idempotency_keys)",
            "M13 downgrade blocked: idempotency state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM offer_acceptances WHERE debt_id IS NOT NULL)",
            "M13 downgrade blocked: debt acceptance state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type IN ('debt.created','debt.accepted','debt.rejected','debt.cancelled','debt.expired'))",
            "M13 downgrade blocked: M13 audit history exists",
        ),
    )
    for sql, message in guards:
        if bind.scalar(sa.text(sql)):
            raise RuntimeError(message)
    _replace_audit_checks(include_m13=False)
    op.drop_index("uq_offer_acceptances_debt_id", table_name="offer_acceptances")
    op.drop_index(
        "uq_offer_acceptances_user_id_offer_text_id_purpose",
        table_name="offer_acceptances",
    )
    op.drop_constraint(
        "ck_offer_acceptances_purpose_debt_id_consistent",
        "offer_acceptances",
        type_="check",
    )
    op.drop_constraint(
        "fk_offer_acceptances_debt_id_debts_id", "offer_acceptances", type_="foreignkey"
    )
    op.drop_column("offer_acceptances", "debt_id")
    op.create_unique_constraint(
        "uq_offer_acceptances_user_id_offer_text_id_purpose",
        "offer_acceptances",
        ["user_id", "offer_text_id", "purpose"],
    )
    op.drop_table("idempotency_keys")
    op.drop_index("ix_debts_status_pending_expires_at_id", table_name="debts")
    op.drop_index("ix_debts_shop_customer_id_status_due_date_id", table_name="debts")
    op.drop_index("ix_debts_shop_customer_id_created_at_id", table_name="debts")
    op.drop_table("debts")
