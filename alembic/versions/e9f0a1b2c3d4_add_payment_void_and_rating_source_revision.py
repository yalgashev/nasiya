# ruff: noqa: E501
"""add payment void and rating source revision

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-08-13 00:00:00.000000
"""

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M17_MIGRATION_PATH = Path(__file__).with_name(
    "d8e9f0a1b2c3_add_written_off_debt_persistence.py"
)


def _load_frozen_m17_migration():
    spec = spec_from_file_location("m18_frozen_m17_revision", _M17_MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("M18 migration cannot load its frozen M17 predecessor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FROZEN_M17 = _load_frozen_m17_migration()
_M18_EVENTS = tuple(_FROZEN_M17._M17_EVENTS) + (
    "payment.voided",
    "debt.reopened_after_payment_void",
)
_IDEMPOTENCY_M18 = (
    _FROZEN_M17._IDEMPOTENCY_M17
    + " OR (endpoint = 'shop.payments.void' AND result_object_type = 'payment')"
)
_M18_REASONS = (
    "duplicate_payment",
    "incorrect_amount",
    "incorrect_method",
    "payment_not_received",
    "wrong_debt",
)
# This predecessor index is intentionally preserved unchanged by both directions.
_PRESERVED_POSITIVE_CAP_INDEX = "ux_rating_events_positive_shop_customer_business_date"


def _m18_audit_payload_sql() -> str:
    reasons = ",".join(repr(value) for value in _M18_REASONS)
    integer_revision = "jsonb_typeof(payload -> 'debt_revision_after') = 'number' AND trunc((payload ->> 'debt_revision_after')::numeric) = (payload ->> 'debt_revision_after')::numeric AND (payload ->> 'debt_revision_after')::numeric > 0"
    payment_voided = f"(event_type = 'payment.voided' AND payload ?& ARRAY['reason','from_status','to_status','debt_revision_after'] AND payload - ARRAY['reason','from_status','to_status','debt_revision_after'] = '{{}}'::jsonb AND jsonb_typeof(payload -> 'reason') = 'string' AND payload ->> 'reason' IN ({reasons}) AND jsonb_typeof(payload -> 'from_status') = 'string' AND jsonb_typeof(payload -> 'to_status') = 'string' AND ((payload ->> 'from_status' = 'active' AND payload ->> 'to_status' = 'active') OR (payload ->> 'from_status' = 'overdue' AND payload ->> 'to_status' = 'overdue') OR (payload ->> 'from_status' = 'written_off' AND payload ->> 'to_status' = 'written_off') OR (payload ->> 'from_status' = 'paid' AND payload ->> 'to_status' IN ('active','overdue')) OR (payload ->> 'from_status' = 'written_off_settled' AND payload ->> 'to_status' = 'written_off')) AND {integer_revision})"
    reopened = f"(event_type = 'debt.reopened_after_payment_void' AND payload ?& ARRAY['source','from_status','to_status','debt_revision_after'] AND payload - ARRAY['source','from_status','to_status','debt_revision_after'] = '{{}}'::jsonb AND jsonb_typeof(payload -> 'source') = 'string' AND payload ->> 'source' = 'payment_void' AND jsonb_typeof(payload -> 'from_status') = 'string' AND jsonb_typeof(payload -> 'to_status') = 'string' AND ((payload ->> 'from_status' = 'paid' AND payload ->> 'to_status' IN ('active','overdue')) OR (payload ->> 'from_status' = 'written_off_settled' AND payload ->> 'to_status' = 'written_off')) AND {integer_revision})"
    return payment_voided + " OR " + reopened


def _m18_parent_audit_payload_sql() -> str:
    payload = _FROZEN_M17._FROZEN_M16._audit_payload_sql(include_m16=True)
    payload = payload[:-1] + " OR " + _FROZEN_M17._m17_audit_payload_sql() + ")"
    overdue_old = "payload ->> 'source' IN ('inline_payment', 'batch') AND payload ->> 'from_status' = 'active'"
    overdue_new = "((payload ->> 'source' IN ('inline_payment', 'batch') AND payload ->> 'from_status' = 'active') OR (payload ->> 'source' = 'payment_void' AND payload ->> 'from_status' = 'paid'))"
    clawback_old = "payload ->> 'source' IN ('inline_payment', 'batch') AND payload ->> 'from_basis'"
    clawback_new = "payload ->> 'source' IN ('inline_payment', 'batch', 'payment_void') AND payload ->> 'from_basis'"
    if payload.count(overdue_old) != 1 or payload.count(clawback_old) != 1:
        raise RuntimeError("M18 migration cannot narrow-extend predecessor audit SQL")
    return payload.replace(overdue_old, overdue_new).replace(clawback_old, clawback_new)


def _replace_audit_checks(*, include_m18: bool) -> None:
    if not include_m18:
        _FROZEN_M17._replace_audit_checks(include_m17=True)
        return
    for name in (
        "ck_audit_log_payload_exact_shape",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_matches_event",
    ):
        op.drop_constraint(name, "audit_log", type_="check")
    op.create_check_constraint(
        "ck_audit_log_event_type_allowed",
        "audit_log",
        f"event_type IN ({', '.join(repr(value) for value in _M18_EVENTS)})",
    )
    op.create_check_constraint(
        "ck_audit_log_object_type_allowed",
        "audit_log",
        f"object_type IN ({', '.join(repr(value) for value in _FROZEN_M17._FROZEN_M16._M16_OBJECTS)})",
    )
    op.create_check_constraint(
        "ck_audit_log_actor_matches_event",
        "audit_log",
        _FROZEN_M17._FROZEN_M16._audit_actor_sql(include_m16=True),
    )
    mapping = _FROZEN_M17._FROZEN_M16._audit_object_mapping_sql(include_m16=True)
    mapping += " OR (event_type IN ('debt.written_off','debt.written_off_settled','debt.reopened_after_payment_void') AND object_type = 'debt')"
    mapping += " OR (event_type = 'payment.voided' AND object_type = 'payment')"
    op.create_check_constraint(
        "ck_audit_log_object_matches_event", "audit_log", mapping
    )
    payload = _m18_parent_audit_payload_sql()
    payload = payload[:-1] + " OR " + _m18_audit_payload_sql() + ")"
    op.create_check_constraint("ck_audit_log_payload_exact_shape", "audit_log", payload)


def _replace_idempotency_check(*, include_m18: bool) -> None:
    op.drop_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        type_="check",
    )
    op.create_check_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        _IDEMPOTENCY_M18 if include_m18 else _FROZEN_M17._IDEMPOTENCY_M17,
    )


def _replace_rating_checks(*, include_m18: bool) -> None:
    if not include_m18:
        _FROZEN_M17._replace_rating_checks(include_m17=True)
        return
    for name in (
        "ck_rating_events_recording_source_allowed",
        "ck_rating_events_delta_matches_event",
        "ck_rating_events_event_type_allowed",
    ):
        op.drop_constraint(name, "rating_events", type_="check")
    op.create_check_constraint(
        "ck_rating_events_event_type_allowed",
        "rating_events",
        "event_type IN ('on_time_paid','on_time_paid_voided','overdue','written_off','written_off_settled','written_off_settled_voided')",
    )
    op.create_check_constraint(
        "ck_rating_events_delta_matches_event",
        "rating_events",
        "(event_type = 'on_time_paid' AND delta = 5) OR (event_type = 'on_time_paid_voided' AND delta = -5) OR (event_type = 'overdue' AND delta = -15) OR (event_type = 'written_off' AND delta = -40) OR (event_type = 'written_off_settled' AND delta = 10) OR (event_type = 'written_off_settled_voided' AND delta = -10)",
    )
    op.create_check_constraint(
        "ck_rating_events_recording_source_allowed",
        "rating_events",
        "(event_type IN ('on_time_paid','overdue') AND recording_source IN ('live','historical_reconciliation')) OR (event_type IN ('on_time_paid_voided','written_off','written_off_settled','written_off_settled_voided') AND recording_source = 'live')",
    )


_SOURCE_CANDIDATES_SQL = """
SELECT re.id AS event_id, p.debt_revision_after AS source_revision
FROM rating_events re
JOIN payments p ON p.debt_id = re.debt_id AND p.created_at = re.occurred_at
JOIN debts d ON d.id = p.debt_id AND d.shop_customer_id = re.shop_customer_id
WHERE re.event_type = 'on_time_paid'
  AND (SELECT count(*) FROM audit_log a WHERE a.event_type = 'payment.recorded' AND a.object_type = 'payment' AND a.object_id = p.id AND a.actor_kind = 'USER' AND a.actor_user_id = p.recorded_by_user_id AND a.occurred_at = p.created_at AND (a.payload ->> 'debt_revision_after')::integer = p.debt_revision_after) = 1
  AND (SELECT count(*) FROM audit_log a WHERE a.event_type = 'debt.paid' AND a.object_type = 'debt' AND a.object_id = d.id AND a.actor_kind = 'USER' AND a.actor_user_id = p.recorded_by_user_id AND a.occurred_at = re.occurred_at AND (a.payload ->> 'debt_revision_after')::integer = p.debt_revision_after) = 1
UNION ALL
SELECT re.id, d.overdue_revision
FROM rating_events re
JOIN debts d ON d.id = re.debt_id AND d.shop_customer_id = re.shop_customer_id AND d.overdue_at = re.occurred_at AND d.overdue_revision IS NOT NULL
WHERE re.event_type = 'overdue'
  AND (SELECT count(*) FROM audit_log a WHERE a.event_type = 'debt.overdue' AND a.object_type = 'debt' AND a.object_id = d.id AND a.actor_kind = 'SYSTEM' AND a.actor_user_id IS NULL AND a.occurred_at = re.occurred_at AND (a.payload ->> 'overdue_revision')::integer = d.overdue_revision AND a.payload ->> 'source' IN ('inline_payment','batch')) = 1
  AND (SELECT count(*) FROM audit_log a WHERE a.event_type = 'debt.clawback_applied' AND a.object_type = 'debt' AND a.object_id = d.id AND a.actor_kind = 'SYSTEM' AND a.actor_user_id IS NULL AND a.occurred_at = re.occurred_at AND (a.payload ->> 'overdue_revision')::integer = d.overdue_revision AND a.payload ->> 'source' IN ('inline_payment','batch')) = 1
  AND EXISTS (SELECT 1 FROM audit_log ao JOIN audit_log ac ON ac.event_type = 'debt.clawback_applied' AND ac.object_type = 'debt' AND ac.object_id = ao.object_id AND ac.occurred_at = ao.occurred_at AND ac.payload ->> 'source' = ao.payload ->> 'source' WHERE ao.event_type = 'debt.overdue' AND ao.object_type = 'debt' AND ao.object_id = d.id AND ao.occurred_at = re.occurred_at)
UNION ALL
SELECT re.id, d.written_off_revision
FROM rating_events re
JOIN debts d ON d.id = re.debt_id AND d.shop_customer_id = re.shop_customer_id AND d.written_off_at = re.occurred_at AND d.written_off_revision IS NOT NULL
WHERE re.event_type = 'written_off'
  AND (SELECT count(*) FROM audit_log a WHERE a.event_type = 'debt.written_off' AND a.object_type = 'debt' AND a.object_id = d.id AND a.actor_kind = 'USER' AND a.actor_user_id = d.written_off_actor_user_id AND a.occurred_at = re.occurred_at AND (a.payload ->> 'written_off_revision')::integer = d.written_off_revision) = 1
UNION ALL
SELECT re.id, p.debt_revision_after
FROM rating_events re
JOIN debts d ON d.id = re.debt_id AND d.shop_customer_id = re.shop_customer_id AND d.written_off_settled_at = re.occurred_at AND d.written_off_settled_revision IS NOT NULL
JOIN payments p ON p.debt_id = d.id AND p.debt_revision_after = d.written_off_settled_revision AND p.created_at = re.occurred_at
WHERE re.event_type = 'written_off_settled'
  AND (SELECT count(*) FROM audit_log a WHERE a.event_type = 'payment.recorded' AND a.object_type = 'payment' AND a.object_id = p.id AND a.actor_kind = 'USER' AND a.actor_user_id = p.recorded_by_user_id AND a.occurred_at = re.occurred_at AND (a.payload ->> 'debt_revision_after')::integer = p.debt_revision_after) = 1
  AND (SELECT count(*) FROM audit_log a WHERE a.event_type = 'debt.written_off_settled' AND a.object_type = 'debt' AND a.object_id = d.id AND a.actor_kind = 'USER' AND a.actor_user_id = p.recorded_by_user_id AND a.occurred_at = re.occurred_at AND (a.payload ->> 'debt_revision_after')::integer = p.debt_revision_after) = 1
"""


def _backfill_rating_source_revision() -> None:
    bind = op.get_bind()
    ambiguous = bind.scalar(
        sa.text(
            f"""
            WITH candidates AS ({_SOURCE_CANDIDATES_SQL}),
            candidate_counts AS (
                SELECT re.id, count(c.event_id) AS candidate_count
                FROM rating_events re
                LEFT JOIN candidates c ON c.event_id = re.id
                GROUP BY re.id
            )
            SELECT EXISTS (
                SELECT 1 FROM candidate_counts WHERE candidate_count <> 1
            )
            """
        )
    )
    if ambiguous:
        raise RuntimeError(
            "M18 source revision backfill blocked: missing or ambiguous source"
        )
    bind.execute(
        sa.text(
            f"""
            WITH candidates AS ({_SOURCE_CANDIDATES_SQL})
            UPDATE rating_events AS re
            SET source_revision = candidates.source_revision
            FROM candidates
            WHERE candidates.event_id = re.id
            """
        )
    )


def upgrade() -> None:
    # Operational prerequisite: all pre-M18 writers are drained and cannot restart.
    op.execute(
        "LOCK TABLE debts, payments, rating_events, audit_log IN SHARE ROW EXCLUSIVE MODE"
    )
    op.create_unique_constraint(
        "uq_payments_id_debt_id_debt_revision_after",
        "payments",
        ["id", "debt_id", "debt_revision_after"],
    )
    op.create_table(
        "payment_voids",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("payment_id", sa.UUID(), nullable=False),
        sa.Column("debt_id", sa.UUID(), nullable=False),
        sa.Column("shop_customer_id", sa.UUID(), nullable=False),
        sa.Column("source_payment_revision", sa.Integer(), nullable=False),
        sa.Column("debt_revision_after", sa.Integer(), nullable=False),
        sa.Column("voided_by_user_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_payment_voids"),
        sa.ForeignKeyConstraint(
            ["payment_id", "debt_id", "source_payment_revision"],
            ["payments.id", "payments.debt_id", "payments.debt_revision_after"],
            name="fk_payment_voids_payment_debt_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["debt_id", "shop_customer_id"],
            ["debts.id", "debts.shop_customer_id"],
            name="fk_payment_voids_debt_shop_customer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_user_id"],
            ["users.id"],
            name="fk_payment_voids_voided_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("payment_id", name="uq_payment_voids_payment_id"),
        sa.UniqueConstraint(
            "debt_id",
            "debt_revision_after",
            name="uq_payment_voids_debt_id_debt_revision_after",
        ),
        sa.CheckConstraint(
            f"reason IN ({', '.join(repr(value) for value in _M18_REASONS)})",
            name="ck_payment_voids_reason_allowed",
        ),
        sa.CheckConstraint(
            "source_payment_revision > 0",
            name="ck_payment_voids_source_payment_revision_positive",
        ),
        sa.CheckConstraint(
            "debt_revision_after > 0",
            name="ck_payment_voids_debt_revision_after_positive",
        ),
        sa.CheckConstraint(
            "source_payment_revision < debt_revision_after",
            name="ck_payment_voids_revision_order",
        ),
    )
    op.create_index(
        "ix_payment_voids_shop_customer_voided_at_id",
        "payment_voids",
        ["shop_customer_id", "voided_at", "id"],
        unique=False,
    )
    op.add_column(
        "rating_events", sa.Column("source_revision", sa.Integer(), nullable=True)
    )
    _backfill_rating_source_revision()
    op.alter_column("rating_events", "source_revision", nullable=False)
    op.create_check_constraint(
        "ck_rating_events_source_revision_positive",
        "rating_events",
        "source_revision > 0",
    )
    op.drop_constraint(
        "uq_rating_events_debt_id_event_type", "rating_events", type_="unique"
    )
    op.drop_index(
        "ix_rating_events_shop_customer_occurred_debt_event",
        table_name="rating_events",
    )
    op.create_unique_constraint(
        "uq_rating_events_debt_event_source_revision",
        "rating_events",
        ["debt_id", "event_type", "source_revision"],
    )
    op.create_index(
        "ux_rating_events_single_debt_negative_source",
        "rating_events",
        ["debt_id", "event_type"],
        unique=True,
        postgresql_where=sa.text("event_type IN ('overdue','written_off')"),
    )
    op.create_index(
        "ix_rating_events_shop_customer_occurred_debt_event_src_rev",
        "rating_events",
        [
            "shop_customer_id",
            "occurred_at",
            "debt_id",
            "event_type",
            "source_revision",
        ],
        unique=False,
    )
    _replace_rating_checks(include_m18=True)
    _replace_audit_checks(include_m18=True)
    _replace_idempotency_check(include_m18=True)


def _guard_m18_downgrade_loss() -> None:
    bind = op.get_bind()
    guards = (
        (
            "SELECT EXISTS (SELECT 1 FROM payment_voids)",
            "M18 downgrade blocked: PaymentVoid history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM rating_events WHERE event_type IN ('on_time_paid_voided','written_off_settled_voided'))",
            "M18 downgrade blocked: compensation rating history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM rating_events GROUP BY debt_id, event_type HAVING count(*) > 1)",
            "M18 downgrade blocked: predecessor rating uniqueness cannot represent cycles",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type IN ('payment.voided','debt.reopened_after_payment_void'))",
            "M18 downgrade blocked: payment void audit history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM idempotency_keys WHERE endpoint = 'shop.payments.void')",
            "M18 downgrade blocked: payment void idempotency history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type IN ('debt.overdue','debt.clawback_applied') AND payload ->> 'source' = 'payment_void')",
            "M18 downgrade blocked: payment void overdue audit history exists",
        ),
        (
            f"WITH candidates AS ({_SOURCE_CANDIDATES_SQL}), candidate_counts AS (SELECT re.id, re.source_revision, count(c.event_id) AS candidate_count, min(c.source_revision) AS expected_revision FROM rating_events re LEFT JOIN candidates c ON c.event_id = re.id GROUP BY re.id, re.source_revision) SELECT EXISTS (SELECT 1 FROM candidate_counts WHERE candidate_count <> 1 OR source_revision <> expected_revision)",
            "M18 downgrade blocked: source revision is not reconstructible",
        ),
    )
    for sql, message in guards:
        if bind.scalar(sa.text(sql)):
            raise RuntimeError(message)


def downgrade() -> None:
    _guard_m18_downgrade_loss()
    _replace_idempotency_check(include_m18=False)
    _replace_audit_checks(include_m18=False)
    _replace_rating_checks(include_m18=False)
    op.drop_index(
        "ix_rating_events_shop_customer_occurred_debt_event_src_rev",
        table_name="rating_events",
    )
    op.drop_index(
        "ux_rating_events_single_debt_negative_source", table_name="rating_events"
    )
    op.drop_constraint(
        "uq_rating_events_debt_event_source_revision",
        "rating_events",
        type_="unique",
    )
    op.drop_constraint(
        "ck_rating_events_source_revision_positive",
        "rating_events",
        type_="check",
    )
    op.create_unique_constraint(
        "uq_rating_events_debt_id_event_type",
        "rating_events",
        ["debt_id", "event_type"],
    )
    op.create_index(
        "ix_rating_events_shop_customer_occurred_debt_event",
        "rating_events",
        ["shop_customer_id", "occurred_at", "debt_id", "event_type"],
        unique=False,
    )
    op.drop_column("rating_events", "source_revision")
    op.drop_index(
        "ix_payment_voids_shop_customer_voided_at_id", table_name="payment_voids"
    )
    op.drop_table("payment_voids")
    op.drop_constraint(
        "uq_payments_id_debt_id_debt_revision_after", "payments", type_="unique"
    )
