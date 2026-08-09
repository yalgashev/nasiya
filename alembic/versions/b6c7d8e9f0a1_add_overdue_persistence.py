# ruff: noqa: E501
"""add overdue persistence

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-08-09 00:00:00.000000
"""

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M14_MIGRATION_PATH = Path(__file__).with_name(
    "a5b6c7d8e9f0_add_active_payment_persistence.py"
)


def _load_frozen_m14_migration():
    spec = spec_from_file_location("m15_frozen_m14_revision", _M14_MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("M15 migration cannot load its frozen M14 predecessor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FROZEN_M14 = _load_frozen_m14_migration()
_M14_EVENTS = tuple(_FROZEN_M14._M14_EVENTS)
_M15_EVENTS = _M14_EVENTS + ("debt.overdue", "debt.clawback_applied")
_M14_OBJECTS = tuple(_FROZEN_M14._M14_OBJECTS)

_DEBT_STATUS_M14 = _FROZEN_M14._DEBT_STATUS_M14
_DEBT_METADATA_M14 = _FROZEN_M14._DEBT_METADATA_M14
_DEBT_TIMESTAMPS_M14 = _FROZEN_M14._DEBT_TIMESTAMPS_M14

_DEBT_STATUS_M15 = (
    "status IN ('pending', 'active', 'rejected', 'cancelled', 'expired', "
    "'paid', 'overdue')"
)
_DEBT_METADATA_M15 = "(status = 'pending' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL AND overdue_at IS NULL AND overdue_revision IS NULL) OR (status = 'active' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL AND overdue_at IS NULL AND overdue_revision IS NULL) OR (status = 'rejected' AND accepted_at IS NULL AND rejected_at IS NOT NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND cancellation_reason IS NULL AND overdue_at IS NULL AND overdue_revision IS NULL) OR (status = 'cancelled' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NOT NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NOT NULL AND overdue_at IS NULL AND overdue_revision IS NULL) OR (status = 'expired' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NOT NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL AND overdue_at IS NULL AND overdue_revision IS NULL) OR (status = 'overdue' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL AND overdue_at IS NOT NULL AND overdue_revision IS NOT NULL) OR (status = 'paid' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NOT NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL AND ((overdue_at IS NULL AND overdue_revision IS NULL) OR (overdue_at IS NOT NULL AND overdue_revision IS NOT NULL AND overdue_revision < revision)))"
_DEBT_TIMESTAMPS_M15 = (
    _DEBT_TIMESTAMPS_M14
    + " AND (overdue_at IS NULL OR (accepted_at IS NOT NULL AND overdue_at >= accepted_at AND updated_at >= overdue_at)) AND (paid_at IS NULL OR overdue_at IS NULL OR paid_at >= overdue_at)"
)


def _exact_payload_clause(
    event_type: str,
    keys: tuple[str, ...],
    *,
    extra_predicate: str | None = None,
) -> str:
    key_array = ", ".join(f"'{key}'" for key in keys)
    clause = (
        f"(event_type = '{event_type}' AND payload ?& ARRAY[{key_array}] "
        f"AND payload - ARRAY[{key_array}] = '{{}}'::jsonb"
    )
    if extra_predicate is not None:
        clause += f" AND {extra_predicate}"
    return clause + ")"


def _whole_number_predicate(
    key: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> str:
    numeric_value = f"(payload ->> '{key}')::numeric"
    bounds = f"{numeric_value} >= {minimum}"
    if maximum is not None:
        bounds += f" AND {numeric_value} <= {maximum}"
    return (
        f"jsonb_typeof(payload -> '{key}') = 'number' "
        f"AND trunc({numeric_value}) = {numeric_value} AND {bounds}"
    )


def _m15_additional_audit_payload_sql() -> str:
    clauses = (
        _exact_payload_clause(
            "debt.overdue",
            (
                "source",
                "from_status",
                "to_status",
                "overdue_revision",
                "business_date",
            ),
            extra_predicate=(
                "payload ->> 'source' IN ('inline_payment', 'batch') "
                "AND payload ->> 'from_status' = 'active' "
                "AND payload ->> 'to_status' = 'overdue' AND "
                + _whole_number_predicate("overdue_revision", minimum=1)
                + " AND payload ->> 'business_date' "
                "~ '^\\d{4}-\\d{2}-\\d{2}$'"
            ),
        ),
        _exact_payload_clause(
            "debt.clawback_applied",
            (
                "source",
                "from_basis",
                "to_basis",
                "balance_increase_uzs",
                "overdue_revision",
            ),
            extra_predicate=(
                "payload ->> 'source' IN ('inline_payment', 'batch') "
                "AND payload ->> 'from_basis' = 'discounted' "
                "AND payload ->> 'to_basis' = 'original' AND "
                + _whole_number_predicate(
                    "balance_increase_uzs",
                    minimum=0,
                    maximum=1_000_000_000_000,
                )
                + " AND "
                + _whole_number_predicate("overdue_revision", minimum=1)
            ),
        ),
        _exact_payload_clause(
            "payment.recorded",
            (
                "amount_uzs",
                "method",
                "from_status",
                "to_status",
                "debt_revision_after",
            ),
            extra_predicate=(
                _whole_number_predicate(
                    "amount_uzs", minimum=1, maximum=1_000_000_000_000
                )
                + " AND payload ->> 'method' IN "
                "('cash', 'card', 'transfer', 'other') "
                "AND payload ->> 'from_status' = 'overdue' "
                "AND payload ->> 'to_status' IN ('overdue', 'paid') AND "
                + _whole_number_predicate("debt_revision_after", minimum=1)
            ),
        ),
    )
    return " OR ".join(clauses)


def _audit_payload_sql(*, include_m15: bool) -> str:
    m14_sql = _FROZEN_M14._audit_payload_sql(include_m14=True)
    if not include_m15:
        return m14_sql
    return m14_sql[:-1] + " OR " + _m15_additional_audit_payload_sql() + ")"


def _replace_audit_checks(*, include_m15: bool) -> None:
    for name in (
        "ck_audit_log_payload_exact_shape",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_matches_event",
    ):
        op.drop_constraint(name, "audit_log", type_="check")

    events = _M15_EVENTS if include_m15 else _M14_EVENTS
    op.create_check_constraint(
        "ck_audit_log_event_type_allowed",
        "audit_log",
        f"event_type IN ({', '.join(repr(item) for item in events)})",
    )
    op.create_check_constraint(
        "ck_audit_log_object_type_allowed",
        "audit_log",
        f"object_type IN ({', '.join(repr(item) for item in _M14_OBJECTS)})",
    )
    system_events = ["platform_admin.bootstrapped", "debt.expired"]
    if include_m15:
        system_events.extend(("debt.overdue", "debt.clawback_applied"))
    system_sql = ", ".join(repr(item) for item in system_events)
    op.create_check_constraint(
        "ck_audit_log_actor_matches_event",
        "audit_log",
        f"(event_type IN ({system_sql}) AND actor_kind = 'SYSTEM' AND actor_user_id IS NULL) OR (event_type NOT IN ({system_sql}) AND actor_kind = 'USER' AND actor_user_id IS NOT NULL)",
    )
    mapping = "(event_type = 'platform_admin.bootstrapped' AND object_type = 'user') OR (event_type IN ('offer.version_created','offer.version_approved','offer.version_made_current','offer.version_demoted') AND object_type = 'offer_version') OR (event_type = 'offer.text_updated' AND object_type = 'offer_text') OR (event_type = 'offer.registration_accepted' AND object_type = 'offer_acceptance') OR (event_type = 'customer.identity_saved' AND object_type = 'customer_identity') OR (event_type IN ('customer.document_attached','customer.document_superseded','customer.document_access_granted') AND object_type = 'customer_document') OR (event_type = 'customer.activated' AND object_type = 'customer') OR (event_type IN ('shop_customer.linked','shop_customer.policy_updated') AND object_type = 'shop_customer') OR (event_type = 'shop.customer_defaults_updated' AND object_type = 'shop')"
    debt_events = "'debt.created','debt.accepted','debt.rejected','debt.cancelled','debt.expired','debt.paid'"
    if include_m15:
        debt_events += ",'debt.overdue','debt.clawback_applied'"
    mapping += f" OR (event_type IN ({debt_events}) AND object_type = 'debt')"
    mapping += " OR (event_type = 'payment.recorded' AND object_type = 'payment')"
    op.create_check_constraint(
        "ck_audit_log_object_matches_event", "audit_log", mapping
    )
    op.create_check_constraint(
        "ck_audit_log_payload_exact_shape",
        "audit_log",
        _audit_payload_sql(include_m15=include_m15),
    )


def _replace_debt_checks(*, include_m15: bool) -> None:
    for name in (
        "ck_debts_timestamp_order",
        "ck_debts_status_metadata_matches_status",
        "ck_debts_status_allowed",
    ):
        op.drop_constraint(name, "debts", type_="check")
    op.create_check_constraint(
        "ck_debts_status_allowed",
        "debts",
        _DEBT_STATUS_M15 if include_m15 else _DEBT_STATUS_M14,
    )
    op.create_check_constraint(
        "ck_debts_status_metadata_matches_status",
        "debts",
        _DEBT_METADATA_M15 if include_m15 else _DEBT_METADATA_M14,
    )
    op.create_check_constraint(
        "ck_debts_timestamp_order",
        "debts",
        _DEBT_TIMESTAMPS_M15 if include_m15 else _DEBT_TIMESTAMPS_M14,
    )


def upgrade() -> None:
    op.add_column(
        "debts",
        sa.Column("overdue_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "debts",
        sa.Column("overdue_revision", sa.Integer(), nullable=True),
    )
    _replace_debt_checks(include_m15=True)
    op.create_check_constraint(
        "ck_debts_overdue_metadata_pair",
        "debts",
        "(overdue_at IS NULL) = (overdue_revision IS NULL)",
    )
    op.create_check_constraint(
        "ck_debts_overdue_revision_positive",
        "debts",
        "overdue_revision IS NULL OR overdue_revision > 0",
    )
    op.create_check_constraint(
        "ck_debts_overdue_revision_not_after_revision",
        "debts",
        "overdue_revision IS NULL OR overdue_revision <= revision",
    )
    op.create_index(
        "ix_debts_status_due_date_id",
        "debts",
        ["status", "due_date", "id"],
        unique=False,
    )
    _replace_audit_checks(include_m15=True)


def downgrade() -> None:
    bind = op.get_bind()
    guards = (
        (
            "SELECT EXISTS (SELECT 1 FROM debts WHERE status = 'overdue' OR overdue_at IS NOT NULL OR overdue_revision IS NOT NULL)",
            "M15 downgrade blocked: overdue debt state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type IN ('debt.overdue','debt.clawback_applied'))",
            "M15 downgrade blocked: overdue audit history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type = 'payment.recorded' AND payload ->> 'from_status' = 'overdue')",
            "M15 downgrade blocked: overdue payment audit history exists",
        ),
        (
            f"SELECT EXISTS (SELECT 1 FROM debts WHERE NOT (({_DEBT_STATUS_M14}) AND ({_DEBT_METADATA_M14}) AND ({_DEBT_TIMESTAMPS_M14})))",
            "M15 downgrade blocked: debt row is not M14-compatible",
        ),
    )
    for sql, message in guards:
        if bind.scalar(sa.text(sql)):
            raise RuntimeError(message)

    _replace_audit_checks(include_m15=False)
    op.drop_index("ix_debts_status_due_date_id", table_name="debts")
    for name in (
        "ck_debts_overdue_revision_not_after_revision",
        "ck_debts_overdue_revision_positive",
        "ck_debts_overdue_metadata_pair",
    ):
        op.drop_constraint(name, "debts", type_="check")
    _replace_debt_checks(include_m15=False)
    op.drop_column("debts", "overdue_revision")
    op.drop_column("debts", "overdue_at")
