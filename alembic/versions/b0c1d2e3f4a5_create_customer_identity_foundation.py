"""create customer identity foundation

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-08-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b0c1d2e3f4a5"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M9_EVENTS = (
    "platform_admin.bootstrapped",
    "offer.version_created",
    "offer.text_updated",
    "offer.version_approved",
    "offer.version_made_current",
    "offer.version_demoted",
    "offer.registration_accepted",
)
_M10_EVENTS = (
    "customer.identity_saved",
    "customer.document_attached",
    "customer.document_superseded",
    "customer.document_access_granted",
)
_M9_OBJECT_TYPES = ("user", "offer_version", "offer_text", "offer_acceptance")
_M10_OBJECT_TYPES = ("customer_identity", "customer_document")
_AUDIT_CHECK_NAMES = (
    "ck_audit_log_payload_exact_shape",
    "ck_audit_log_object_matches_event",
    "ck_audit_log_object_type_allowed",
    "ck_audit_log_event_type_allowed",
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


def _payload_shape_sql(*, include_m10: bool) -> str:
    clauses = [
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
    ]
    if include_m10:
        clauses.extend(
            (
                _exact_payload_clause(
                    "customer.identity_saved",
                    ("revision", "created_or_updated", "document_type"),
                    extra_predicate=(
                        "jsonb_typeof(payload -> 'revision') = 'number' "
                        "AND (payload ->> 'revision')::integer > 0 "
                        "AND payload ->> 'created_or_updated' "
                        "IN ('created', 'updated') "
                        "AND payload ->> 'document_type' "
                        "IN ('PASSPORT', 'ID_CARD')"
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
                        "AND (payload ->> 'ttl_seconds')::integer "
                        "BETWEEN 60 AND 900"
                    ),
                ),
            )
        )
    return "jsonb_typeof(payload) = 'object' AND (" + " OR ".join(clauses) + ")"


def _object_matches_event_sql(*, include_m10: bool) -> str:
    clause = (
        "(event_type = 'platform_admin.bootstrapped' AND object_type = 'user') "
        "OR (event_type IN ("
        "'offer.version_created', 'offer.version_approved', "
        "'offer.version_made_current', 'offer.version_demoted') "
        "AND object_type = 'offer_version') "
        "OR (event_type = 'offer.text_updated' AND object_type = 'offer_text') "
        "OR (event_type = 'offer.registration_accepted' "
        "AND object_type = 'offer_acceptance')"
    )
    if include_m10:
        clause += (
            " OR (event_type = 'customer.identity_saved' "
            "AND object_type = 'customer_identity') "
            "OR (event_type IN ("
            "'customer.document_attached', 'customer.document_superseded', "
            "'customer.document_access_granted') "
            "AND object_type = 'customer_document')"
        )
    return clause


def _replace_audit_checks(*, include_m10: bool) -> None:
    for constraint_name in _AUDIT_CHECK_NAMES:
        op.drop_constraint(constraint_name, "audit_log", type_="check")
    events = _M9_EVENTS + (_M10_EVENTS if include_m10 else ())
    object_types = _M9_OBJECT_TYPES + (_M10_OBJECT_TYPES if include_m10 else ())
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
        _object_matches_event_sql(include_m10=include_m10),
    )
    op.create_check_constraint(
        "ck_audit_log_payload_exact_shape",
        "audit_log",
        _payload_shape_sql(include_m10=include_m10),
    )


def upgrade() -> None:
    op.create_table(
        "customer_identities",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ciphertext", postgresql.BYTEA(), nullable=False),
        sa.Column("nonce", postgresql.BYTEA(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column(
            "schema_version",
            sa.SmallInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("jshshir_blind_index", postgresql.BYTEA(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
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
            "octet_length(ciphertext) >= 16",
            name="ck_customer_identities_ciphertext_minimum_length",
        ),
        sa.CheckConstraint(
            "octet_length(nonce) = 12",
            name="ck_customer_identities_nonce_length",
        ),
        sa.CheckConstraint(
            "key_id ~ '^[A-Za-z0-9._-]{1,64}$'",
            name="ck_customer_identities_key_id_format",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_customer_identities_schema_version_supported",
        ),
        sa.CheckConstraint(
            "octet_length(jshshir_blind_index) = 32",
            name="ck_customer_identities_blind_index_length",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_customer_identities_revision_positive",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_customer_identities_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_customer_identities_customer_id_customers_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("customer_id", name="pk_customer_identities"),
        sa.UniqueConstraint(
            "jshshir_blind_index",
            name="uq_customer_identities_jshshir_blind_index",
        ),
    )
    op.create_table(
        "customer_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "attached_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "attached_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "superseded_by_document_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "superseded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('CURRENT', 'SUPERSEDED')",
            name="ck_customer_documents_status_allowed",
        ),
        sa.CheckConstraint(
            "(status = 'CURRENT' "
            "AND superseded_by_document_id IS NULL "
            "AND superseded_at IS NULL) "
            "OR (status = 'SUPERSEDED' "
            "AND superseded_by_document_id IS NOT NULL "
            "AND superseded_at IS NOT NULL)",
            name="ck_customer_documents_supersede_metadata_matches_status",
        ),
        sa.CheckConstraint(
            "superseded_by_document_id IS NULL OR superseded_by_document_id <> id",
            name="ck_customer_documents_no_self_replacement",
        ),
        sa.CheckConstraint(
            "superseded_at IS NULL OR superseded_at >= attached_at",
            name="ck_customer_documents_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_customer_documents_customer_id_customers_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["object_file_id"],
            ["object_files.id"],
            name="fk_customer_documents_object_file_id_object_files_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attached_by_user_id"],
            ["users.id"],
            name="fk_customer_documents_attached_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_document_id"],
            ["customer_documents.id"],
            name="fk_customer_documents_superseded_by_id_customer_documents",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer_documents"),
        sa.UniqueConstraint(
            "object_file_id",
            name="uq_customer_documents_object_file_id",
        ),
        sa.UniqueConstraint(
            "customer_id",
            "submission_id",
            name="uq_customer_documents_customer_id_submission_id",
        ),
    )
    op.create_index(
        "uq_customer_documents_current_customer_id",
        "customer_documents",
        ["customer_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CURRENT'"),
    )
    _replace_audit_checks(include_m10=True)


def downgrade() -> None:
    _replace_audit_checks(include_m10=False)
    op.drop_index(
        "uq_customer_documents_current_customer_id",
        table_name="customer_documents",
    )
    op.drop_table("customer_documents")
    op.drop_table("customer_identities")
