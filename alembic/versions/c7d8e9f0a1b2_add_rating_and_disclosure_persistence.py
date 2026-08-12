# ruff: noqa: E501
"""add rating and disclosure persistence

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M15_MIGRATION_PATH = Path(__file__).with_name(
    "b6c7d8e9f0a1_add_overdue_persistence.py"
)
_RECONCILIATION_NAMESPACE = UUID("c7d8e9f0-a1b2-5c16-8000-000000000001")


def _load_frozen_m15_migration():
    spec = spec_from_file_location("m16_frozen_m15_revision", _M15_MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("M16 migration cannot load its frozen M15 predecessor")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FROZEN_M15 = _load_frozen_m15_migration()
_M15_EVENTS = tuple(_FROZEN_M15._M15_EVENTS)
_M16_EVENTS = _M15_EVENTS + ("disclosure.risk_band_viewed",)
_M15_OBJECTS = tuple(_FROZEN_M15._M14_OBJECTS)
_M16_OBJECTS = _M15_OBJECTS + ("disclosure_view",)

_IDEMPOTENCY_M15 = "(endpoint = 'shop.debts.create' AND result_object_type = 'debt') OR (endpoint = 'shop.debt_payments.create' AND result_object_type = 'payment')"
_IDEMPOTENCY_M16 = _IDEMPOTENCY_M15 + " OR (endpoint = 'shop.risk_band_disclosures.create' AND result_object_type = 'disclosure_view')"


def _disclosure_payload_sql() -> str:
    return "(event_type = 'disclosure.risk_band_viewed' AND payload ?& ARRAY['purpose','band'] AND payload - ARRAY['purpose','band'] = '{}'::jsonb AND jsonb_typeof(payload -> 'purpose') = 'string' AND jsonb_typeof(payload -> 'band') = 'string' AND payload ->> 'purpose' IN ('debt_proposal_review','credit_limit_review','existing_debt_review') AND payload ->> 'band' IN ('new','green','yellow','red','blocked'))"


def _audit_payload_sql(*, include_m16: bool) -> str:
    m15_sql = _FROZEN_M15._audit_payload_sql(include_m15=True)
    if not include_m16:
        return m15_sql
    return m15_sql[:-1] + " OR " + _disclosure_payload_sql() + ")"


def _audit_object_mapping_sql(*, include_m16: bool) -> str:
    mapping = "(event_type = 'platform_admin.bootstrapped' AND object_type = 'user') OR (event_type IN ('offer.version_created','offer.version_approved','offer.version_made_current','offer.version_demoted') AND object_type = 'offer_version') OR (event_type = 'offer.text_updated' AND object_type = 'offer_text') OR (event_type = 'offer.registration_accepted' AND object_type = 'offer_acceptance') OR (event_type = 'customer.identity_saved' AND object_type = 'customer_identity') OR (event_type IN ('customer.document_attached','customer.document_superseded','customer.document_access_granted') AND object_type = 'customer_document') OR (event_type = 'customer.activated' AND object_type = 'customer') OR (event_type IN ('shop_customer.linked','shop_customer.policy_updated') AND object_type = 'shop_customer') OR (event_type = 'shop.customer_defaults_updated' AND object_type = 'shop') OR (event_type IN ('debt.created','debt.accepted','debt.rejected','debt.cancelled','debt.expired','debt.paid','debt.overdue','debt.clawback_applied') AND object_type = 'debt') OR (event_type = 'payment.recorded' AND object_type = 'payment')"
    if include_m16:
        mapping += " OR (event_type = 'disclosure.risk_band_viewed' AND object_type = 'disclosure_view')"
    return mapping


def _audit_actor_sql(*, include_m16: bool) -> str:
    del include_m16
    system = "'platform_admin.bootstrapped','debt.expired','debt.overdue','debt.clawback_applied'"
    return f"(event_type IN ({system}) AND actor_kind = 'SYSTEM' AND actor_user_id IS NULL) OR (event_type NOT IN ({system}) AND actor_kind = 'USER' AND actor_user_id IS NOT NULL)"


def _replace_audit_checks(*, include_m16: bool) -> None:
    for name in (
        "ck_audit_log_payload_exact_shape",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_matches_event",
    ):
        op.drop_constraint(name, "audit_log", type_="check")
    events = _M16_EVENTS if include_m16 else _M15_EVENTS
    objects = _M16_OBJECTS if include_m16 else _M15_OBJECTS
    op.create_check_constraint(
        "ck_audit_log_event_type_allowed",
        "audit_log",
        f"event_type IN ({', '.join(repr(value) for value in events)})",
    )
    op.create_check_constraint(
        "ck_audit_log_object_type_allowed",
        "audit_log",
        f"object_type IN ({', '.join(repr(value) for value in objects)})",
    )
    op.create_check_constraint(
        "ck_audit_log_actor_matches_event",
        "audit_log",
        _audit_actor_sql(include_m16=include_m16),
    )
    op.create_check_constraint(
        "ck_audit_log_object_matches_event",
        "audit_log",
        _audit_object_mapping_sql(include_m16=include_m16),
    )
    op.create_check_constraint(
        "ck_audit_log_payload_exact_shape",
        "audit_log",
        _audit_payload_sql(include_m16=include_m16),
    )


def _replace_idempotency_check(*, include_m16: bool) -> None:
    op.drop_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        type_="check",
    )
    op.create_check_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        _IDEMPOTENCY_M16 if include_m16 else _IDEMPOTENCY_M15,
    )


_POSITIVE_RECONCILIATION_SQL = sa.text(
    """
    SELECT d.id AS debt_id, d.shop_customer_id, d.paid_at AS occurred_at,
           (d.paid_at AT TIME ZONE 'Asia/Tashkent')::date AS business_date,
           COALESCE(SUM(p.amount_uzs), 0) = d.discounted_amount_uzs AS total_ok,
           COUNT(*) FILTER (
               WHERE p.debt_revision_after = d.revision
                 AND p.created_at = d.paid_at
           ) = 1 AS terminal_ok,
           COUNT(p.id) > 0 AND COUNT(p.id) = COUNT(*) FILTER (
               WHERE EXISTS (
                   SELECT 1 FROM audit_log a
                   WHERE a.event_type = 'payment.recorded'
                     AND a.object_type = 'payment' AND a.object_id = p.id
                     AND a.actor_kind = 'USER'
                     AND a.actor_user_id = p.recorded_by_user_id
                     AND a.occurred_at = p.created_at
                     AND (a.payload ->> 'debt_revision_after')::integer = p.debt_revision_after
               )
           ) AS payment_audits_ok,
           COUNT(*) FILTER (
               WHERE p.debt_revision_after = d.revision
                 AND p.created_at = d.paid_at
                 AND EXISTS (
                     SELECT 1 FROM audit_log a
                     WHERE a.event_type = 'debt.paid'
                       AND a.object_type = 'debt' AND a.object_id = d.id
                       AND a.actor_kind = 'USER'
                       AND a.actor_user_id = p.recorded_by_user_id
                       AND a.occurred_at = d.paid_at
                       AND (a.payload ->> 'debt_revision_after')::integer = d.revision
                 )
           ) = 1 AS paid_audit_ok
    FROM debts d
    LEFT JOIN payments p ON p.debt_id = d.id
    WHERE d.status = 'paid'
      AND d.overdue_at IS NULL AND d.overdue_revision IS NULL
      AND d.original_amount_uzs >= 100000
      AND (d.accepted_at AT TIME ZONE 'Asia/Tashkent')::date
          < (d.paid_at AT TIME ZONE 'Asia/Tashkent')::date
      AND (d.paid_at AT TIME ZONE 'Asia/Tashkent')::date <= d.due_date
    GROUP BY d.id
    ORDER BY d.paid_at, d.id
    """
)

_NEGATIVE_RECONCILIATION_SQL = sa.text(
    """
    SELECT d.id AS debt_id, d.shop_customer_id, d.overdue_at AS occurred_at,
           (d.overdue_at AT TIME ZONE 'Asia/Tashkent')::date AS business_date,
           EXISTS (
               SELECT 1 FROM audit_log a
               WHERE a.event_type = 'debt.overdue'
                 AND a.object_type = 'debt' AND a.object_id = d.id
                 AND a.actor_kind = 'SYSTEM' AND a.actor_user_id IS NULL
                 AND a.occurred_at = d.overdue_at
                 AND (a.payload ->> 'overdue_revision')::integer = d.overdue_revision
           ) AS overdue_audit_ok,
           EXISTS (
               SELECT 1 FROM audit_log a
               WHERE a.event_type = 'debt.clawback_applied'
                 AND a.object_type = 'debt' AND a.object_id = d.id
                 AND a.actor_kind = 'SYSTEM' AND a.actor_user_id IS NULL
                 AND a.occurred_at = d.overdue_at
                 AND (a.payload ->> 'overdue_revision')::integer = d.overdue_revision
           ) AS clawback_audit_ok
    FROM debts d
    WHERE d.status IN ('overdue', 'paid')
      AND d.overdue_at IS NOT NULL AND d.overdue_revision IS NOT NULL
    ORDER BY d.overdue_at, d.id
    """
)


def _insert_historical_event(bind, *, event_type: str, delta: int, row) -> None:
    event_id = uuid5(
        _RECONCILIATION_NAMESPACE,
        f"{event_type}:{row.debt_id}",
    )
    bind.execute(
        sa.text(
            "INSERT INTO rating_events (id, shop_customer_id, debt_id, event_type, delta, occurred_at, business_date, recording_source) VALUES (:id, :shop_customer_id, :debt_id, :event_type, :delta, :occurred_at, :business_date, 'historical_reconciliation')"
        ),
        {
            "id": event_id,
            "shop_customer_id": row.shop_customer_id,
            "debt_id": row.debt_id,
            "event_type": event_type,
            "delta": delta,
            "occurred_at": row.occurred_at,
            "business_date": row.business_date,
        },
    )


def _reconcile_historical_rating_events() -> None:
    bind = op.get_bind()
    positives = bind.execute(_POSITIVE_RECONCILIATION_SQL).all()
    for row in positives:
        if not (row.total_ok and row.terminal_ok and row.payment_audits_ok and row.paid_audit_ok):
            raise RuntimeError(
                "M16 reconciliation blocked: incoherent positive source history"
            )
    negatives = bind.execute(_NEGATIVE_RECONCILIATION_SQL).all()
    for row in negatives:
        if not (row.overdue_audit_ok and row.clawback_audit_ok):
            raise RuntimeError(
                "M16 reconciliation blocked: incoherent negative source history"
            )

    winning_pair_days: set[tuple[object, object]] = set()
    for row in positives:
        pair_day = (row.shop_customer_id, row.business_date)
        if pair_day in winning_pair_days:
            continue
        winning_pair_days.add(pair_day)
        _insert_historical_event(
            bind,
            event_type="on_time_paid",
            delta=5,
            row=row,
        )
    for row in negatives:
        _insert_historical_event(bind, event_type="overdue", delta=-15, row=row)


def upgrade() -> None:
    # First source-data action: closes the in-flight M15 Debt-writer/scan race.
    op.execute("LOCK TABLE debts IN SHARE ROW EXCLUSIVE MODE")

    op.create_unique_constraint(
        "uq_debts_id_shop_customer_id", "debts", ["id", "shop_customer_id"]
    )
    op.create_unique_constraint(
        "uq_shop_customers_id_shop_id", "shop_customers", ["id", "shop_id"]
    )
    op.create_table(
        "rating_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shop_customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("debt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("delta", sa.SmallInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("recording_source", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('on_time_paid','overdue')",
            name="ck_rating_events_event_type_allowed",
        ),
        sa.CheckConstraint(
            "(event_type = 'on_time_paid' AND delta = 5) OR (event_type = 'overdue' AND delta = -15)",
            name="ck_rating_events_delta_matches_event",
        ),
        sa.CheckConstraint(
            "recording_source IN ('live','historical_reconciliation')",
            name="ck_rating_events_recording_source_allowed",
        ),
        sa.CheckConstraint(
            "business_date = (occurred_at AT TIME ZONE 'Asia/Tashkent')::date",
            name="ck_rating_events_business_date_matches_occurred_at",
        ),
        sa.ForeignKeyConstraint(
            ["debt_id", "shop_customer_id"],
            ["debts.id", "debts.shop_customer_id"],
            name="fk_rating_events_debt_shop_customer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rating_events"),
        sa.UniqueConstraint(
            "debt_id", "event_type", name="uq_rating_events_debt_id_event_type"
        ),
    )
    op.create_index(
        "ux_rating_events_positive_shop_customer_business_date",
        "rating_events",
        ["shop_customer_id", "business_date"],
        unique=True,
        postgresql_where=sa.text("event_type = 'on_time_paid'"),
    )
    op.create_index(
        "ix_rating_events_shop_customer_occurred_debt_event",
        "rating_events",
        ["shop_customer_id", "occurred_at", "debt_id", "event_type"],
        unique=False,
    )
    op.create_table(
        "disclosure_view_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shop_customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('debt_proposal_review','credit_limit_review','existing_debt_review')",
            name="ck_disclosure_view_logs_purpose_allowed",
        ),
        sa.CheckConstraint(
            "band IN ('new','green','yellow','red','blocked')",
            name="ck_disclosure_view_logs_band_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_disclosure_view_logs_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_customer_id", "shop_id"],
            ["shop_customers.id", "shop_customers.shop_id"],
            name="fk_disclosure_logs_shop_customer_shop",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_disclosure_view_logs"),
    )
    op.create_index(
        "ix_disclosure_view_logs_shop_id_id",
        "disclosure_view_logs",
        ["shop_id", "id"],
        unique=False,
    )
    _replace_idempotency_check(include_m16=True)
    _replace_audit_checks(include_m16=True)
    _reconcile_historical_rating_events()


def _m15_audit_valid_sql() -> str:
    events = ", ".join(repr(value) for value in _M15_EVENTS)
    objects = ", ".join(repr(value) for value in _M15_OBJECTS)
    return f"event_type IN ({events}) AND object_type IN ({objects}) AND ({_audit_actor_sql(include_m16=False)}) AND ({_audit_object_mapping_sql(include_m16=False)}) AND ({_audit_payload_sql(include_m16=False)})"


def downgrade() -> None:
    bind = op.get_bind()
    guards = (
        (
            "SELECT EXISTS (SELECT 1 FROM rating_events)",
            "M16 downgrade blocked: rating event history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM disclosure_view_logs)",
            "M16 downgrade blocked: disclosure view history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM idempotency_keys WHERE endpoint = 'shop.risk_band_disclosures.create' OR result_object_type = 'disclosure_view')",
            "M16 downgrade blocked: disclosure idempotency history exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type = 'disclosure.risk_band_viewed' OR object_type = 'disclosure_view')",
            "M16 downgrade blocked: disclosure audit history exists",
        ),
        (
            f"SELECT EXISTS (SELECT 1 FROM idempotency_keys WHERE NOT ({_IDEMPOTENCY_M15}))",
            "M16 downgrade blocked: idempotency row is not M15-compatible",
        ),
        (
            f"SELECT EXISTS (SELECT 1 FROM audit_log WHERE NOT ({_m15_audit_valid_sql()}))",
            "M16 downgrade blocked: audit row is not M15-compatible",
        ),
    )
    for sql, message in guards:
        if bind.scalar(sa.text(sql)):
            raise RuntimeError(message)

    _replace_audit_checks(include_m16=False)
    _replace_idempotency_check(include_m16=False)
    op.drop_index(
        "ix_disclosure_view_logs_shop_id_id", table_name="disclosure_view_logs"
    )
    op.drop_table("disclosure_view_logs")
    op.drop_index(
        "ix_rating_events_shop_customer_occurred_debt_event",
        table_name="rating_events",
    )
    op.drop_index(
        "ux_rating_events_positive_shop_customer_business_date",
        table_name="rating_events",
    )
    op.drop_table("rating_events")
    op.drop_constraint(
        "uq_shop_customers_id_shop_id", "shop_customers", type_="unique"
    )
    op.drop_constraint(
        "uq_debts_id_shop_customer_id", "debts", type_="unique"
    )
