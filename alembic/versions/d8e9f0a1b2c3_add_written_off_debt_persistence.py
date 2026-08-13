# ruff: noqa: E501
"""add written-off debt persistence

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-13 00:00:00.000000
"""

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M16_MIGRATION_PATH = Path(__file__).with_name(
    "c7d8e9f0a1b2_add_rating_and_disclosure_persistence.py"
)


def _load_frozen_m16_migration():
    spec = spec_from_file_location("m17_frozen_m16_revision", _M16_MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("M17 migration cannot load its frozen M16 predecessor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FROZEN_M16 = _load_frozen_m16_migration()
_M16_DEBT = _FROZEN_M16._FROZEN_M15

_M17_REASONS = (
    "collection_exhausted",
    "customer_unreachable",
    "insolvency_or_deceased",
    "legal_or_compliance",
    "fraud_or_abuse",
)
_M17_AUDIT_EVENTS = ("debt.written_off", "debt.written_off_settled")
_M17_EVENTS = tuple(_FROZEN_M16._M16_EVENTS) + _M17_AUDIT_EVENTS

_DEBT_STATUS_M17 = (
    "status IN ('pending','active','rejected','cancelled','expired','paid',"
    "'overdue','written_off','written_off_settled')"
)
_NULL_M17 = "written_off_at IS NULL AND written_off_revision IS NULL AND written_off_reason IS NULL AND written_off_actor_user_id IS NULL AND written_off_settled_at IS NULL AND written_off_settled_revision IS NULL"
_BASE_METADATA_M17 = (
    "("
    + _M16_DEBT._DEBT_METADATA_M15.replace(
        ") OR (status =", f" AND {_NULL_M17}) OR (status ="
    )
    + f" AND {_NULL_M17})"
)
_WRITTEN_OFF_METADATA = "(status = 'written_off' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL AND overdue_at IS NOT NULL AND overdue_revision IS NOT NULL AND written_off_at IS NOT NULL AND written_off_revision IS NOT NULL AND written_off_reason IS NOT NULL AND written_off_actor_user_id IS NOT NULL AND written_off_settled_at IS NULL AND written_off_settled_revision IS NULL)"
_SETTLED_METADATA = "(status = 'written_off_settled' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL AND overdue_at IS NOT NULL AND overdue_revision IS NOT NULL AND written_off_at IS NOT NULL AND written_off_revision IS NOT NULL AND written_off_reason IS NOT NULL AND written_off_actor_user_id IS NOT NULL AND written_off_settled_at IS NOT NULL AND written_off_settled_revision IS NOT NULL AND written_off_settled_revision = revision)"
_DEBT_METADATA_M17 = (
    f"({_BASE_METADATA_M17}) OR {_WRITTEN_OFF_METADATA} OR {_SETTLED_METADATA}"
)
_DEBT_TIMESTAMPS_M17 = (
    _M16_DEBT._DEBT_TIMESTAMPS_M15
    + " AND (written_off_at IS NULL OR (overdue_at IS NOT NULL AND written_off_at >= overdue_at AND updated_at >= written_off_at)) AND (written_off_settled_at IS NULL OR (written_off_at IS NOT NULL AND written_off_settled_at >= written_off_at AND updated_at >= written_off_settled_at))"
)

_IDEMPOTENCY_M17 = (
    _FROZEN_M16._IDEMPOTENCY_M16
    + " OR (endpoint = 'admin.debts.write_off' AND result_object_type = 'debt')"
)


def _m17_audit_payload_sql() -> str:
    write_off = "(event_type = 'debt.written_off' AND payload ?& ARRAY['reason_provided','from_status','to_status','written_off_revision'] AND payload - ARRAY['reason_provided','from_status','to_status','written_off_revision'] = '{}'::jsonb AND payload -> 'reason_provided' = 'true'::jsonb AND jsonb_typeof(payload -> 'from_status') = 'string' AND payload ->> 'from_status' = 'overdue' AND jsonb_typeof(payload -> 'to_status') = 'string' AND payload ->> 'to_status' = 'written_off' AND jsonb_typeof(payload -> 'written_off_revision') = 'number' AND trunc((payload ->> 'written_off_revision')::numeric) = (payload ->> 'written_off_revision')::numeric AND (payload ->> 'written_off_revision')::numeric > 0)"
    settled = "(event_type = 'debt.written_off_settled' AND payload ?& ARRAY['source','from_status','to_status','debt_revision_after'] AND payload - ARRAY['source','from_status','to_status','debt_revision_after'] = '{}'::jsonb AND jsonb_typeof(payload -> 'source') = 'string' AND payload ->> 'source' = 'payment' AND jsonb_typeof(payload -> 'from_status') = 'string' AND payload ->> 'from_status' = 'written_off' AND jsonb_typeof(payload -> 'to_status') = 'string' AND payload ->> 'to_status' = 'written_off_settled' AND jsonb_typeof(payload -> 'debt_revision_after') = 'number' AND trunc((payload ->> 'debt_revision_after')::numeric) = (payload ->> 'debt_revision_after')::numeric AND (payload ->> 'debt_revision_after')::numeric > 0)"
    payment = "(event_type = 'payment.recorded' AND payload ?& ARRAY['amount_uzs','method','from_status','to_status','debt_revision_after'] AND payload - ARRAY['amount_uzs','method','from_status','to_status','debt_revision_after'] = '{}'::jsonb AND jsonb_typeof(payload -> 'amount_uzs') = 'number' AND trunc((payload ->> 'amount_uzs')::numeric) = (payload ->> 'amount_uzs')::numeric AND (payload ->> 'amount_uzs')::numeric BETWEEN 1 AND 1000000000000 AND payload ->> 'method' IN ('cash','card','transfer','other') AND payload ->> 'from_status' = 'written_off' AND payload ->> 'to_status' IN ('written_off','written_off_settled') AND jsonb_typeof(payload -> 'debt_revision_after') = 'number' AND trunc((payload ->> 'debt_revision_after')::numeric) = (payload ->> 'debt_revision_after')::numeric AND (payload ->> 'debt_revision_after')::numeric > 0)"
    return write_off + " OR " + settled + " OR " + payment


def _replace_rating_checks(*, include_m17: bool) -> None:
    for name in (
        "ck_rating_events_recording_source_allowed",
        "ck_rating_events_delta_matches_event",
        "ck_rating_events_event_type_allowed",
    ):
        op.drop_constraint(name, "rating_events", type_="check")
    if include_m17:
        event_sql = "event_type IN ('on_time_paid','overdue','written_off','written_off_settled')"
        delta_sql = "(event_type = 'on_time_paid' AND delta = 5) OR (event_type = 'overdue' AND delta = -15) OR (event_type = 'written_off' AND delta = -40) OR (event_type = 'written_off_settled' AND delta = 10)"
        source_sql = "(event_type IN ('on_time_paid','overdue') AND recording_source IN ('live','historical_reconciliation')) OR (event_type IN ('written_off','written_off_settled') AND recording_source = 'live')"
    else:
        event_sql = "event_type IN ('on_time_paid','overdue')"
        delta_sql = "(event_type = 'on_time_paid' AND delta = 5) OR (event_type = 'overdue' AND delta = -15)"
        source_sql = "recording_source IN ('live','historical_reconciliation')"
    op.create_check_constraint(
        "ck_rating_events_event_type_allowed", "rating_events", event_sql
    )
    op.create_check_constraint(
        "ck_rating_events_delta_matches_event", "rating_events", delta_sql
    )
    op.create_check_constraint(
        "ck_rating_events_recording_source_allowed", "rating_events", source_sql
    )


def _replace_idempotency_check(*, include_m17: bool) -> None:
    op.drop_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        type_="check",
    )
    op.create_check_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        _IDEMPOTENCY_M17 if include_m17 else _FROZEN_M16._IDEMPOTENCY_M16,
    )


def _replace_audit_checks(*, include_m17: bool) -> None:
    for name in (
        "ck_audit_log_payload_exact_shape",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_matches_event",
    ):
        op.drop_constraint(name, "audit_log", type_="check")
    events = _M17_EVENTS if include_m17 else tuple(_FROZEN_M16._M16_EVENTS)
    op.create_check_constraint(
        "ck_audit_log_event_type_allowed",
        "audit_log",
        f"event_type IN ({', '.join(repr(value) for value in events)})",
    )
    op.create_check_constraint(
        "ck_audit_log_object_type_allowed",
        "audit_log",
        f"object_type IN ({', '.join(repr(value) for value in _FROZEN_M16._M16_OBJECTS)})",
    )
    op.create_check_constraint(
        "ck_audit_log_actor_matches_event",
        "audit_log",
        _FROZEN_M16._audit_actor_sql(include_m16=True),
    )
    mapping = _FROZEN_M16._audit_object_mapping_sql(include_m16=True)
    if include_m17:
        mapping += " OR (event_type IN ('debt.written_off','debt.written_off_settled') AND object_type = 'debt')"
    op.create_check_constraint(
        "ck_audit_log_object_matches_event", "audit_log", mapping
    )
    payload = _FROZEN_M16._audit_payload_sql(include_m16=True)
    if include_m17:
        payload = payload[:-1] + " OR " + _m17_audit_payload_sql() + ")"
    op.create_check_constraint("ck_audit_log_payload_exact_shape", "audit_log", payload)


def _replace_debt_lifecycle_checks(*, include_m17: bool) -> None:
    for name in (
        "ck_debts_timestamp_order",
        "ck_debts_status_metadata_matches_status",
        "ck_debts_status_allowed",
    ):
        op.drop_constraint(name, "debts", type_="check")
    op.create_check_constraint(
        "ck_debts_status_allowed",
        "debts",
        _DEBT_STATUS_M17 if include_m17 else _M16_DEBT._DEBT_STATUS_M15,
    )
    op.create_check_constraint(
        "ck_debts_status_metadata_matches_status",
        "debts",
        _DEBT_METADATA_M17 if include_m17 else _M16_DEBT._DEBT_METADATA_M15,
    )
    op.create_check_constraint(
        "ck_debts_timestamp_order",
        "debts",
        _DEBT_TIMESTAMPS_M17 if include_m17 else _M16_DEBT._DEBT_TIMESTAMPS_M15,
    )


def upgrade() -> None:
    # Operational prerequisite: all pre-M17 writers are drained and cannot restart.
    op.execute("LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE")
    op.add_column(
        "debts", sa.Column("written_off_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "debts", sa.Column("written_off_revision", sa.Integer(), nullable=True)
    )
    op.add_column("debts", sa.Column("written_off_reason", sa.Text(), nullable=True))
    op.add_column(
        "debts", sa.Column("written_off_actor_user_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "debts",
        sa.Column("written_off_settled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "debts", sa.Column("written_off_settled_revision", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "fk_debts_written_off_actor_user_id_users_id",
        "debts",
        "users",
        ["written_off_actor_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    _replace_debt_lifecycle_checks(include_m17=True)
    op.create_check_constraint(
        "ck_debts_written_off_metadata_complete",
        "debts",
        "(written_off_at IS NULL AND written_off_revision IS NULL AND written_off_reason IS NULL AND written_off_actor_user_id IS NULL) OR (written_off_at IS NOT NULL AND written_off_revision IS NOT NULL AND written_off_reason IS NOT NULL AND written_off_actor_user_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_debts_written_off_reason_allowed",
        "debts",
        f"written_off_reason IS NULL OR written_off_reason IN ({', '.join(repr(value) for value in _M17_REASONS)})",
    )
    op.create_check_constraint(
        "ck_debts_written_off_revision_positive",
        "debts",
        "written_off_revision IS NULL OR written_off_revision > 0",
    )
    op.create_check_constraint(
        "ck_debts_written_off_revision_not_after_revision",
        "debts",
        "written_off_revision IS NULL OR written_off_revision <= revision",
    )
    op.create_check_constraint(
        "ck_debts_written_off_settled_metadata_pair",
        "debts",
        "(written_off_settled_at IS NULL) = (written_off_settled_revision IS NULL)",
    )
    op.create_check_constraint(
        "ck_debts_written_off_settled_revision_positive",
        "debts",
        "written_off_settled_revision IS NULL OR written_off_settled_revision > 0",
    )
    op.create_check_constraint(
        "ck_debts_written_off_settled_revision_not_after_revision",
        "debts",
        "written_off_settled_revision IS NULL OR written_off_settled_revision <= revision",
    )
    op.create_check_constraint(
        "ck_debts_written_off_revision_chain",
        "debts",
        "written_off_revision IS NULL OR (overdue_revision IS NOT NULL AND overdue_revision < written_off_revision)",
    )
    op.create_check_constraint(
        "ck_debts_written_off_settled_revision_chain",
        "debts",
        "written_off_settled_revision IS NULL OR (written_off_revision IS NOT NULL AND written_off_revision < written_off_settled_revision)",
    )
    op.create_index(
        "ix_debts_status_overdue_at_id",
        "debts",
        ["status", "overdue_at", "id"],
        unique=False,
    )
    _replace_rating_checks(include_m17=True)
    _replace_idempotency_check(include_m17=True)
    _replace_audit_checks(include_m17=True)


def _guard_m17_downgrade_loss() -> None:
    bind = op.get_bind()
    guards = (
        (
            "SELECT EXISTS (SELECT 1 FROM debts WHERE status IN ('written_off','written_off_settled') OR written_off_at IS NOT NULL OR written_off_revision IS NOT NULL OR written_off_reason IS NOT NULL OR written_off_actor_user_id IS NOT NULL OR written_off_settled_at IS NOT NULL OR written_off_settled_revision IS NOT NULL)",
            "M17 downgrade blocked: written-off Debt state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM rating_events WHERE event_type IN ('written_off','written_off_settled'))",
            "M17 downgrade blocked: written-off rating history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type IN ('debt.written_off','debt.written_off_settled'))",
            "M17 downgrade blocked: written-off audit history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM idempotency_keys WHERE endpoint = 'admin.debts.write_off')",
            "M17 downgrade blocked: write-off idempotency history exists",
        ),
    )
    for sql, message in guards:
        if bind.scalar(sa.text(sql)):
            raise RuntimeError(message)


def downgrade() -> None:
    _guard_m17_downgrade_loss()
    _replace_audit_checks(include_m17=False)
    _replace_idempotency_check(include_m17=False)
    _replace_rating_checks(include_m17=False)
    op.drop_index("ix_debts_status_overdue_at_id", table_name="debts")
    for name in (
        "ck_debts_written_off_settled_revision_chain",
        "ck_debts_written_off_revision_chain",
        "ck_debts_written_off_settled_revision_not_after_revision",
        "ck_debts_written_off_settled_revision_positive",
        "ck_debts_written_off_settled_metadata_pair",
        "ck_debts_written_off_revision_not_after_revision",
        "ck_debts_written_off_revision_positive",
        "ck_debts_written_off_reason_allowed",
        "ck_debts_written_off_metadata_complete",
    ):
        op.drop_constraint(name, "debts", type_="check")
    _replace_debt_lifecycle_checks(include_m17=False)
    op.drop_constraint(
        "fk_debts_written_off_actor_user_id_users_id", "debts", type_="foreignkey"
    )
    for column in (
        "written_off_settled_revision",
        "written_off_settled_at",
        "written_off_actor_user_id",
        "written_off_reason",
        "written_off_revision",
        "written_off_at",
    ):
        op.drop_column("debts", column)
