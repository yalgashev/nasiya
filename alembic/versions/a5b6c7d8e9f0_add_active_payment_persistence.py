# ruff: noqa: E501
"""add active payment persistence

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e
Create Date: 2026-08-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M13_EVENTS = (
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
    "debt.created",
    "debt.accepted",
    "debt.rejected",
    "debt.cancelled",
    "debt.expired",
)
_M14_EVENTS = _M13_EVENTS + ("payment.recorded", "debt.paid")
_M13_OBJECTS = (
    "user",
    "offer_version",
    "offer_text",
    "offer_acceptance",
    "customer_identity",
    "customer_document",
    "customer",
    "shop_customer",
    "shop",
    "debt",
)
_M14_OBJECTS = _M13_OBJECTS + ("payment",)

_DEBT_STATUS_M13 = "status IN ('pending', 'active', 'rejected', 'cancelled', 'expired')"
_DEBT_STATUS_M14 = (
    "status IN ('pending', 'active', 'rejected', 'cancelled', 'expired', 'paid')"
)
_DEBT_METADATA_M13 = "(status = 'pending' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL) OR (status = 'active' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL) OR (status = 'rejected' AND accepted_at IS NULL AND rejected_at IS NOT NULL AND cancelled_at IS NULL AND expired_at IS NULL AND cancellation_reason IS NULL) OR (status = 'cancelled' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NOT NULL AND expired_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NOT NULL) OR (status = 'expired' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NOT NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL)"
_DEBT_METADATA_M14 = "(status = 'pending' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL) OR (status = 'active' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL) OR (status = 'rejected' AND accepted_at IS NULL AND rejected_at IS NOT NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NULL AND cancellation_reason IS NULL) OR (status = 'cancelled' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NOT NULL AND expired_at IS NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NOT NULL) OR (status = 'expired' AND accepted_at IS NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NOT NULL AND paid_at IS NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL) OR (status = 'paid' AND accepted_at IS NOT NULL AND rejected_at IS NULL AND cancelled_at IS NULL AND expired_at IS NULL AND paid_at IS NOT NULL AND rejection_reason IS NULL AND cancellation_reason IS NULL)"
_DEBT_TIMESTAMPS_M13 = "updated_at >= created_at AND (accepted_at IS NULL OR accepted_at >= created_at) AND (rejected_at IS NULL OR rejected_at >= created_at) AND (cancelled_at IS NULL OR cancelled_at >= created_at) AND (expired_at IS NULL OR expired_at >= created_at)"
_DEBT_TIMESTAMPS_M14 = (
    _DEBT_TIMESTAMPS_M13
    + " AND (paid_at IS NULL OR (accepted_at IS NOT NULL AND paid_at >= accepted_at AND updated_at >= paid_at))"
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


def _policy_predicate(
    *,
    credit_key: str,
    max_debts_key: str,
    status_key: str,
) -> str:
    return (
        _whole_number_predicate(
            credit_key,
            minimum=0,
            maximum=1_000_000_000_000,
        )
        + " AND "
        + _whole_number_predicate(max_debts_key, minimum=1, maximum=100)
        + f" AND payload ->> '{status_key}' "
        "IN ('normal', 'whitelisted', 'blacklisted')"
    )


def _audit_payload_sql(*, include_m14: bool) -> str:
    clauses = [
        _exact_payload_clause(
            "platform_admin.bootstrapped",
            ("bootstrap_method",),
            extra_predicate="payload ->> 'bootstrap_method' = 'operator_cli'",
        ),
        _exact_payload_clause(
            "offer.version_created", ("purpose", "version_number", "status")
        ),
        _exact_payload_clause(
            "offer.text_updated",
            ("purpose", "version_number", "language", "content_hash"),
        ),
        _exact_payload_clause(
            "offer.version_approved",
            (
                "purpose",
                "version_number",
                "from_status",
                "to_status",
                "legal_review_authority",
                "legal_review_reference",
                "legal_reviewed_at",
            ),
        ),
        _exact_payload_clause(
            "offer.version_made_current",
            (
                "purpose",
                "version_number",
                "from_status",
                "to_status",
                "previous_current_version_id",
            ),
        ),
        _exact_payload_clause(
            "offer.version_demoted",
            (
                "purpose",
                "version_number",
                "from_status",
                "to_status",
                "replacement_version_id",
            ),
        ),
        _exact_payload_clause(
            "offer.registration_accepted",
            (
                "purpose",
                "offer_version_id",
                "offer_text_id",
                "version_number",
                "language",
                "content_hash",
            ),
        ),
        _exact_payload_clause(
            "customer.identity_saved",
            ("revision", "created_or_updated", "document_type"),
            extra_predicate=(
                "jsonb_typeof(payload -> 'revision') = 'number' "
                "AND (payload ->> 'revision')::integer > 0 "
                "AND payload ->> 'created_or_updated' IN ('created', 'updated') "
                "AND payload ->> 'document_type' IN ('PASSPORT', 'ID_CARD')"
            ),
        ),
        _exact_payload_clause(
            "customer.document_attached",
            ("status", "submission_replayed"),
            extra_predicate=(
                "payload ->> 'status' = 'CURRENT' "
                "AND payload -> 'submission_replayed' = 'false'::jsonb"
            ),
        ),
        _exact_payload_clause(
            "customer.document_superseded",
            ("replacement_document_id",),
            extra_predicate=(
                "payload ->> 'replacement_document_id' "
                "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                "[89ab][0-9a-f]{3}-[0-9a-f]{12}$'"
            ),
        ),
        _exact_payload_clause(
            "customer.document_access_granted",
            ("ttl_seconds",),
            extra_predicate=(
                "jsonb_typeof(payload -> 'ttl_seconds') = 'number' "
                "AND (payload ->> 'ttl_seconds')::integer BETWEEN 60 AND 900"
            ),
        ),
        _exact_payload_clause(
            "customer.activated",
            ("from_status", "to_status", "activation_method"),
            extra_predicate=(
                "payload ->> 'from_status' = 'draft' "
                "AND payload ->> 'to_status' = 'active' "
                "AND payload ->> 'activation_method' = "
                "'TELEGRAM_REGISTRATION_OTP'"
            ),
        ),
        _exact_payload_clause(
            "shop_customer.linked",
            (
                "outcome",
                "credit_limit_uzs",
                "max_open_debts",
                "list_status",
                "revision",
            ),
            extra_predicate=(
                "payload ->> 'outcome' = 'created' AND "
                + _policy_predicate(
                    credit_key="credit_limit_uzs",
                    max_debts_key="max_open_debts",
                    status_key="list_status",
                )
                + " AND "
                + _whole_number_predicate("revision", minimum=1)
            ),
        ),
        _exact_payload_clause(
            "shop_customer.policy_updated",
            (
                "old_credit_limit_uzs",
                "new_credit_limit_uzs",
                "old_max_open_debts",
                "new_max_open_debts",
                "old_list_status",
                "new_list_status",
                "revision",
            ),
            extra_predicate=(
                _policy_predicate(
                    credit_key="old_credit_limit_uzs",
                    max_debts_key="old_max_open_debts",
                    status_key="old_list_status",
                )
                + " AND "
                + _policy_predicate(
                    credit_key="new_credit_limit_uzs",
                    max_debts_key="new_max_open_debts",
                    status_key="new_list_status",
                )
                + " AND "
                + _whole_number_predicate("revision", minimum=2)
                + " AND (payload -> 'old_credit_limit_uzs' <> "
                "payload -> 'new_credit_limit_uzs' OR "
                "payload -> 'old_max_open_debts' <> "
                "payload -> 'new_max_open_debts' OR "
                "payload -> 'old_list_status' <> payload -> 'new_list_status')"
            ),
        ),
        _exact_payload_clause(
            "shop.customer_defaults_updated",
            (
                "old_default_credit_limit_uzs",
                "new_default_credit_limit_uzs",
                "old_default_max_open_debts",
                "new_default_max_open_debts",
            ),
            extra_predicate=(
                _whole_number_predicate(
                    "old_default_credit_limit_uzs",
                    minimum=0,
                    maximum=1_000_000_000_000,
                )
                + " AND "
                + _whole_number_predicate(
                    "new_default_credit_limit_uzs",
                    minimum=0,
                    maximum=1_000_000_000_000,
                )
                + " AND "
                + _whole_number_predicate(
                    "old_default_max_open_debts", minimum=1, maximum=100
                )
                + " AND "
                + _whole_number_predicate(
                    "new_default_max_open_debts", minimum=1, maximum=100
                )
                + " AND (payload -> 'old_default_credit_limit_uzs' <> "
                "payload -> 'new_default_credit_limit_uzs' OR "
                "payload -> 'old_default_max_open_debts' <> "
                "payload -> 'new_default_max_open_debts')"
            ),
        ),
        _exact_payload_clause(
            "debt.created",
            (
                "original_amount_uzs",
                "discount_basis_points",
                "discounted_amount_uzs",
                "due_date",
                "pending_expires_at",
            ),
            extra_predicate=(
                _whole_number_predicate(
                    "original_amount_uzs", minimum=1, maximum=1_000_000_000_000
                )
                + " AND "
                + _whole_number_predicate(
                    "discount_basis_points", minimum=0, maximum=10_000
                )
                + " AND "
                + _whole_number_predicate("discounted_amount_uzs", minimum=1)
                + " AND (payload ->> 'discounted_amount_uzs')::numeric <= "
                "(payload ->> 'original_amount_uzs')::numeric "
                "AND payload ->> 'due_date' ~ '^\\d{4}-\\d{2}-\\d{2}$' "
                "AND payload ->> 'pending_expires_at' <> ''"
            ),
        ),
        _exact_payload_clause(
            "debt.accepted",
            ("offer_version_number", "language", "content_hash"),
            extra_predicate=(
                _whole_number_predicate("offer_version_number", minimum=1)
                + " AND payload ->> 'language' IN ('UZ_LATN', 'UZ_CYRL', 'RU') "
                "AND payload ->> 'content_hash' ~ '^[0-9a-f]{64}$'"
            ),
        ),
        _exact_payload_clause(
            "debt.rejected",
            ("reason_provided",),
            extra_predicate="jsonb_typeof(payload -> 'reason_provided') = 'boolean'",
        ),
        _exact_payload_clause(
            "debt.cancelled",
            ("reason_provided",),
            extra_predicate="payload -> 'reason_provided' = 'true'::jsonb",
        ),
        _exact_payload_clause(
            "debt.expired",
            ("source",),
            extra_predicate="payload ->> 'source' IN ('inline', 'batch')",
        ),
    ]
    if include_m14:
        clauses.extend(
            (
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
                            "amount_uzs",
                            minimum=1,
                            maximum=1_000_000_000_000,
                        )
                        + " AND payload ->> 'method' IN "
                        "('cash', 'card', 'transfer', 'other') "
                        "AND payload ->> 'from_status' = 'active' "
                        "AND payload ->> 'to_status' IN ('active', 'paid') AND "
                        + _whole_number_predicate("debt_revision_after", minimum=1)
                    ),
                ),
                _exact_payload_clause(
                    "debt.paid",
                    ("source", "debt_revision_after"),
                    extra_predicate=(
                        "payload ->> 'source' = 'payment' AND "
                        + _whole_number_predicate("debt_revision_after", minimum=1)
                    ),
                ),
            )
        )
    return "jsonb_typeof(payload) = 'object' AND (" + " OR ".join(clauses) + ")"


def _replace_audit_checks(*, include_m14: bool) -> None:
    for name in (
        "ck_audit_log_payload_exact_shape",
        "ck_audit_log_object_matches_event",
        "ck_audit_log_object_type_allowed",
        "ck_audit_log_event_type_allowed",
        "ck_audit_log_actor_matches_event",
    ):
        op.drop_constraint(name, "audit_log", type_="check")

    events = _M14_EVENTS if include_m14 else _M13_EVENTS
    objects = _M14_OBJECTS if include_m14 else _M13_OBJECTS
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
    system_events = "'platform_admin.bootstrapped', 'debt.expired'"
    op.create_check_constraint(
        "ck_audit_log_actor_matches_event",
        "audit_log",
        f"(event_type IN ({system_events}) AND actor_kind = 'SYSTEM' AND actor_user_id IS NULL) OR (event_type NOT IN ({system_events}) AND actor_kind = 'USER' AND actor_user_id IS NOT NULL)",
    )
    mapping = "(event_type = 'platform_admin.bootstrapped' AND object_type = 'user') OR (event_type IN ('offer.version_created','offer.version_approved','offer.version_made_current','offer.version_demoted') AND object_type = 'offer_version') OR (event_type = 'offer.text_updated' AND object_type = 'offer_text') OR (event_type = 'offer.registration_accepted' AND object_type = 'offer_acceptance') OR (event_type = 'customer.identity_saved' AND object_type = 'customer_identity') OR (event_type IN ('customer.document_attached','customer.document_superseded','customer.document_access_granted') AND object_type = 'customer_document') OR (event_type = 'customer.activated' AND object_type = 'customer') OR (event_type IN ('shop_customer.linked','shop_customer.policy_updated') AND object_type = 'shop_customer') OR (event_type = 'shop.customer_defaults_updated' AND object_type = 'shop')"
    debt_events = (
        "'debt.created','debt.accepted','debt.rejected','debt.cancelled','debt.expired'"
    )
    if include_m14:
        debt_events += ",'debt.paid'"
    mapping += f" OR (event_type IN ({debt_events}) AND object_type = 'debt')"
    if include_m14:
        mapping += " OR (event_type = 'payment.recorded' AND object_type = 'payment')"
    op.create_check_constraint(
        "ck_audit_log_object_matches_event", "audit_log", mapping
    )
    op.create_check_constraint(
        "ck_audit_log_payload_exact_shape",
        "audit_log",
        _audit_payload_sql(include_m14=include_m14),
    )


def _replace_debt_checks(*, include_m14: bool) -> None:
    for name in (
        "ck_debts_timestamp_order",
        "ck_debts_status_metadata_matches_status",
        "ck_debts_status_allowed",
    ):
        op.drop_constraint(name, "debts", type_="check")
    op.create_check_constraint(
        "ck_debts_status_allowed",
        "debts",
        _DEBT_STATUS_M14 if include_m14 else _DEBT_STATUS_M13,
    )
    op.create_check_constraint(
        "ck_debts_status_metadata_matches_status",
        "debts",
        _DEBT_METADATA_M14 if include_m14 else _DEBT_METADATA_M13,
    )
    op.create_check_constraint(
        "ck_debts_timestamp_order",
        "debts",
        _DEBT_TIMESTAMPS_M14 if include_m14 else _DEBT_TIMESTAMPS_M13,
    )


def upgrade() -> None:
    op.add_column(
        "debts",
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
    )
    _replace_debt_checks(include_m14=True)
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("debt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_uzs", sa.Numeric(18, 0), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("debt_revision_after", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_uzs BETWEEN 1 AND 1000000000000",
            name="ck_payments_amount_uzs_bounds",
        ),
        sa.CheckConstraint(
            "method IN ('cash', 'card', 'transfer', 'other')",
            name="ck_payments_method_allowed",
        ),
        sa.CheckConstraint(
            "debt_revision_after > 0",
            name="ck_payments_debt_revision_after_positive",
        ),
        sa.ForeignKeyConstraint(
            ["debt_id"],
            ["debts.id"],
            name="fk_payments_debt_id_debts_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name="fk_payments_recorded_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.UniqueConstraint(
            "debt_id",
            "debt_revision_after",
            name="uq_payments_debt_id_debt_revision_after",
        ),
    )
    op.drop_constraint(
        "ck_idempotency_keys_endpoint_allowed",
        "idempotency_keys",
        type_="check",
    )
    op.drop_constraint(
        "ck_idempotency_keys_result_object_type_allowed",
        "idempotency_keys",
        type_="check",
    )
    op.create_check_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        "(endpoint = 'shop.debts.create' AND result_object_type = 'debt') OR (endpoint = 'shop.debt_payments.create' AND result_object_type = 'payment')",
    )
    _replace_audit_checks(include_m14=True)


def downgrade() -> None:
    bind = op.get_bind()
    guards = (
        (
            "SELECT EXISTS (SELECT 1 FROM payments)",
            "M14 downgrade blocked: payment state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM idempotency_keys WHERE endpoint = 'shop.debt_payments.create' OR result_object_type = 'payment')",
            "M14 downgrade blocked: payment idempotency state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM debts WHERE status = 'paid' OR paid_at IS NOT NULL)",
            "M14 downgrade blocked: paid debt state exists",
        ),
        (
            "SELECT EXISTS (SELECT 1 FROM audit_log WHERE event_type IN ('payment.recorded','debt.paid'))",
            "M14 downgrade blocked: M14 audit history exists",
        ),
    )
    for sql, message in guards:
        if bind.scalar(sa.text(sql)):
            raise RuntimeError(message)

    _replace_audit_checks(include_m14=False)
    op.drop_constraint(
        "ck_idempotency_keys_endpoint_result_pair_allowed",
        "idempotency_keys",
        type_="check",
    )
    op.create_check_constraint(
        "ck_idempotency_keys_endpoint_allowed",
        "idempotency_keys",
        "endpoint = 'shop.debts.create'",
    )
    op.create_check_constraint(
        "ck_idempotency_keys_result_object_type_allowed",
        "idempotency_keys",
        "result_object_type = 'debt'",
    )
    op.drop_table("payments")
    _replace_debt_checks(include_m14=False)
    op.drop_column("debts", "paid_at")
