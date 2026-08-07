"""add shop customer foundation

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M11_AUDIT_EVENTS = (
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
)
_M12_AUDIT_EVENTS = _M11_AUDIT_EVENTS + (
    "shop_customer.linked",
    "shop_customer.policy_updated",
    "shop.customer_defaults_updated",
)
_M11_AUDIT_OBJECT_TYPES = (
    "user",
    "offer_version",
    "offer_text",
    "offer_acceptance",
    "customer_identity",
    "customer_document",
    "customer",
)
_M12_AUDIT_OBJECT_TYPES = _M11_AUDIT_OBJECT_TYPES + (
    "shop_customer",
    "shop",
)
_AUDIT_CHECK_NAMES = (
    "ck_audit_log_payload_exact_shape",
    "ck_audit_log_object_matches_event",
    "ck_audit_log_object_type_allowed",
    "ck_audit_log_event_type_allowed",
)
_M12_EVENT_VALUES_SQL = ", ".join(
    f"'{event_type}'" for event_type in _M12_AUDIT_EVENTS[-3:]
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _exact_payload_clause(
    event_type: str,
    keys: tuple[str, ...],
    *,
    extra_predicate: str | None = None,
) -> str:
    key_array = _sql_values(keys)
    clause = (
        f"(event_type = '{event_type}' "
        f"AND payload ?& ARRAY[{key_array}] "
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


def _audit_payload_shape_sql(*, include_m12: bool) -> str:
    clauses = (
        _exact_payload_clause(
            "platform_admin.bootstrapped",
            ("bootstrap_method",),
            extra_predicate="payload ->> 'bootstrap_method' = 'operator_cli'",
        ),
        _exact_payload_clause(
            "offer.version_created",
            ("purpose", "version_number", "status"),
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
    )
    if include_m12:
        clauses += (
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
                    + _whole_number_predicate("revision", minimum=1)
                    + " AND ("
                    "payload -> 'old_credit_limit_uzs' <> "
                    "payload -> 'new_credit_limit_uzs' OR "
                    "payload -> 'old_max_open_debts' <> "
                    "payload -> 'new_max_open_debts' OR "
                    "payload -> 'old_list_status' <> "
                    "payload -> 'new_list_status')"
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
                    + " AND ("
                    "payload -> 'old_default_credit_limit_uzs' <> "
                    "payload -> 'new_default_credit_limit_uzs' OR "
                    "payload -> 'old_default_max_open_debts' <> "
                    "payload -> 'new_default_max_open_debts')"
                ),
            ),
        )
    return "jsonb_typeof(payload) = 'object' AND (" + " OR ".join(clauses) + ")"


def _audit_object_matches_event_sql(*, include_m12: bool) -> str:
    clause = (
        "(event_type = 'platform_admin.bootstrapped' AND object_type = 'user') "
        "OR (event_type IN ("
        "'offer.version_created', 'offer.version_approved', "
        "'offer.version_made_current', 'offer.version_demoted') "
        "AND object_type = 'offer_version') "
        "OR (event_type = 'offer.text_updated' AND object_type = 'offer_text') "
        "OR (event_type = 'offer.registration_accepted' "
        "AND object_type = 'offer_acceptance') "
        "OR (event_type = 'customer.identity_saved' "
        "AND object_type = 'customer_identity') "
        "OR (event_type IN ("
        "'customer.document_attached', 'customer.document_superseded', "
        "'customer.document_access_granted') "
        "AND object_type = 'customer_document') "
        "OR (event_type = 'customer.activated' AND object_type = 'customer')"
    )
    if include_m12:
        clause += (
            " OR (event_type IN ('shop_customer.linked', "
            "'shop_customer.policy_updated') AND object_type = 'shop_customer')"
            " OR (event_type = 'shop.customer_defaults_updated' "
            "AND object_type = 'shop')"
        )
    return clause


def _replace_audit_checks(*, include_m12: bool) -> None:
    for constraint_name in _AUDIT_CHECK_NAMES:
        op.drop_constraint(constraint_name, "audit_log", type_="check")
    events = _M12_AUDIT_EVENTS if include_m12 else _M11_AUDIT_EVENTS
    object_types = _M12_AUDIT_OBJECT_TYPES if include_m12 else _M11_AUDIT_OBJECT_TYPES
    op.create_check_constraint(
        "ck_audit_log_event_type_allowed",
        "audit_log",
        f"event_type IN ({_sql_values(events)})",
    )
    op.create_check_constraint(
        "ck_audit_log_object_type_allowed",
        "audit_log",
        f"object_type IN ({_sql_values(object_types)})",
    )
    op.create_check_constraint(
        "ck_audit_log_object_matches_event",
        "audit_log",
        _audit_object_matches_event_sql(include_m12=include_m12),
    )
    op.create_check_constraint(
        "ck_audit_log_payload_exact_shape",
        "audit_log",
        _audit_payload_shape_sql(include_m12=include_m12),
    )


def upgrade() -> None:
    op.add_column(
        "shops",
        sa.Column(
            "default_credit_limit_uzs",
            sa.Numeric(precision=18, scale=0),
            server_default=sa.text("1000000"),
            nullable=False,
        ),
    )
    op.add_column(
        "shops",
        sa.Column(
            "default_max_open_debts",
            sa.SmallInteger(),
            server_default=sa.text("2"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_shops_default_credit_limit_uzs_bounds",
        "shops",
        "default_credit_limit_uzs BETWEEN 0 AND 1000000000000",
    )
    op.create_check_constraint(
        "ck_shops_default_max_open_debts_bounds",
        "shops",
        "default_max_open_debts BETWEEN 1 AND 100",
    )

    op.create_table(
        "shop_customers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("shop_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "credit_limit_uzs",
            sa.Numeric(precision=18, scale=0),
            nullable=False,
        ),
        sa.Column("max_open_debts", sa.SmallInteger(), nullable=False),
        sa.Column(
            "list_status",
            sa.Text(),
            server_default=sa.text("'normal'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
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
            "credit_limit_uzs BETWEEN 0 AND 1000000000000",
            name="ck_shop_customers_credit_limit_uzs_bounds",
        ),
        sa.CheckConstraint(
            "max_open_debts BETWEEN 1 AND 100",
            name="ck_shop_customers_max_open_debts_bounds",
        ),
        sa.CheckConstraint(
            "list_status IN ('normal', 'whitelisted', 'blacklisted')",
            name="ck_shop_customers_list_status_allowed",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_shop_customers_revision_positive",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_shop_customers_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["shop_id"],
            ["shops.id"],
            name="fk_shop_customers_shop_id_shops_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_shop_customers_customer_id_customers_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_shop_customers_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shop_customers"),
        sa.UniqueConstraint(
            "shop_id",
            "customer_id",
            name="uq_shop_customers_shop_id_customer_id",
        ),
    )
    op.create_index(
        "ix_shop_customers_shop_id_created_at_id",
        "shop_customers",
        ["shop_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_shop_customers_customer_id_created_at_id",
        "shop_customers",
        ["customer_id", "created_at", "id"],
        unique=False,
    )
    _replace_audit_checks(include_m12=True)


def downgrade() -> None:
    connection = op.get_bind()
    has_shop_customer_rows = connection.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM shop_customers)")
    )
    if has_shop_customer_rows:
        raise RuntimeError("M12 downgrade blocked: shop customer state exists")

    has_changed_defaults = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM shops "
            "WHERE default_credit_limit_uzs <> 1000000 "
            "OR default_max_open_debts <> 2)"
        )
    )
    if has_changed_defaults:
        raise RuntimeError("M12 downgrade blocked: shop defaults changed")

    has_m12_audit_history = connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM audit_log "
            f"WHERE event_type IN ({_M12_EVENT_VALUES_SQL}))"
        )
    )
    if has_m12_audit_history:
        raise RuntimeError("M12 downgrade blocked: M12 audit history exists")

    _replace_audit_checks(include_m12=False)
    op.drop_index(
        "ix_shop_customers_customer_id_created_at_id",
        table_name="shop_customers",
    )
    op.drop_index(
        "ix_shop_customers_shop_id_created_at_id",
        table_name="shop_customers",
    )
    op.drop_table("shop_customers")
    op.drop_constraint(
        "ck_shops_default_max_open_debts_bounds",
        "shops",
        type_="check",
    )
    op.drop_constraint(
        "ck_shops_default_credit_limit_uzs_bounds",
        "shops",
        type_="check",
    )
    op.drop_column("shops", "default_max_open_debts")
    op.drop_column("shops", "default_credit_limit_uzs")
