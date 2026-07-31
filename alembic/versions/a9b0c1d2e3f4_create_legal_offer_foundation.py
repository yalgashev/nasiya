"""create legal offer foundation

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "f8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _exact_payload_clause(
    event_type: str,
    keys: tuple[str, ...],
    *,
    extra_predicate: str | None = None,
) -> str:
    key_array = ", ".join(f"'{key}'" for key in keys)
    clause = (
        f"(event_type = '{event_type}' "
        f"AND payload ?& ARRAY[{key_array}] "
        f"AND payload - ARRAY[{key_array}] = '{{}}'::jsonb"
    )
    if extra_predicate is not None:
        clause += f" AND {extra_predicate}"
    return clause + ")"


_AUDIT_PAYLOAD_EXACT_SHAPE_SQL = (
    "jsonb_typeof(payload) = 'object' AND ("
    + " OR ".join(
        (
            _exact_payload_clause(
                "platform_admin.bootstrapped",
                ("bootstrap_method",),
                extra_predicate=("payload ->> 'bootstrap_method' = 'operator_cli'"),
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
        )
    )
    + ")"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )

    op.create_table(
        "offer_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'DRAFT'"),
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
            "legal_review_authority",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "legal_reviewed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "legal_review_reference",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "approved_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "current_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "current_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "purpose IN ('REGISTRATION', 'DEBT_ACCEPTANCE')",
            name="ck_offer_versions_purpose_allowed",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_offer_versions_version_number_positive",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'APPROVED', 'CURRENT')",
            name="ck_offer_versions_status_allowed",
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' "
            "AND legal_review_authority IS NULL "
            "AND legal_reviewed_at IS NULL "
            "AND legal_review_reference IS NULL "
            "AND approved_by_user_id IS NULL "
            "AND approved_at IS NULL) "
            "OR (status IN ('APPROVED', 'CURRENT') "
            "AND legal_review_authority IS NOT NULL "
            "AND legal_reviewed_at IS NOT NULL "
            "AND legal_review_reference IS NOT NULL "
            "AND approved_by_user_id IS NOT NULL "
            "AND approved_at IS NOT NULL)",
            name="ck_offer_versions_approval_evidence_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'DRAFT' "
            "AND current_by_user_id IS NULL "
            "AND current_at IS NULL) "
            "OR (status = 'APPROVED' "
            "AND ((current_by_user_id IS NULL AND current_at IS NULL) "
            "OR (current_by_user_id IS NOT NULL "
            "AND current_at IS NOT NULL))) "
            "OR (status = 'CURRENT' "
            "AND current_by_user_id IS NOT NULL "
            "AND current_at IS NOT NULL)",
            name="ck_offer_versions_current_metadata_matches_status",
        ),
        sa.CheckConstraint(
            "legal_review_authority IS NULL "
            "OR (char_length(legal_review_authority) BETWEEN 1 AND 200 "
            "AND legal_review_authority = btrim(legal_review_authority) "
            "AND legal_review_authority !~ '[[:cntrl:]]')",
            name="ck_offer_versions_legal_review_authority_valid",
        ),
        sa.CheckConstraint(
            "legal_review_reference IS NULL "
            "OR legal_review_reference "
            "~ '^[A-Za-z0-9][A-Za-z0-9._ -]{0,199}$'",
            name="ck_offer_versions_legal_review_reference_valid",
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL OR approved_at >= created_at) "
            "AND (legal_reviewed_at IS NULL "
            "OR legal_reviewed_at <= approved_at) "
            "AND (current_at IS NULL OR current_at >= approved_at)",
            name="ck_offer_versions_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_offer_versions_created_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name="fk_offer_versions_approved_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["current_by_user_id"],
            ["users.id"],
            name="fk_offer_versions_current_by_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_offer_versions"),
        sa.UniqueConstraint(
            "purpose",
            "version_number",
            name="uq_offer_versions_purpose_version_number",
        ),
    )
    op.create_index(
        "uq_offer_versions_current_purpose",
        "offer_versions",
        ["purpose"],
        unique=True,
        postgresql_where=sa.text("status = 'CURRENT'"),
    )

    op.create_table(
        "offer_texts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "offer_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
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
            "language IN ('UZ_LATN', 'UZ_CYRL', 'RU')",
            name="ck_offer_texts_language_allowed",
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0 "
            "AND length(btrim(body)) > 0 "
            "AND position(chr(13) in title) = 0 "
            "AND position(chr(13) in body) = 0",
            name="ck_offer_texts_content_canonical",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_offer_texts_content_hash_sha256_hex",
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name="ck_offer_texts_timestamp_order",
        ),
        sa.ForeignKeyConstraint(
            ["offer_version_id"],
            ["offer_versions.id"],
            name="fk_offer_texts_offer_version_id_offer_versions_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_offer_texts"),
        sa.UniqueConstraint(
            "offer_version_id",
            "language",
            name="uq_offer_texts_offer_version_id_language",
        ),
    )

    op.create_table(
        "offer_acceptances",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "offer_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "offer_text_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('REGISTRATION', 'DEBT_ACCEPTANCE')",
            name="ck_offer_acceptances_purpose_allowed",
        ),
        sa.CheckConstraint(
            "language IN ('UZ_LATN', 'UZ_CYRL', 'RU')",
            name="ck_offer_acceptances_language_allowed",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_offer_acceptances_version_number_positive",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_offer_acceptances_content_hash_sha256_hex",
        ),
        sa.CheckConstraint(
            "user_agent IS NULL "
            "OR (char_length(user_agent) BETWEEN 1 AND 512 "
            "AND user_agent = btrim(user_agent) "
            "AND user_agent !~ '[[:cntrl:]]' "
            "AND user_agent !~ '  +')",
            name="ck_offer_acceptances_user_agent_normalized",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_offer_acceptances_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_version_id"],
            ["offer_versions.id"],
            name="fk_offer_acceptances_offer_version_id_offer_versions_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_text_id"],
            ["offer_texts.id"],
            name="fk_offer_acceptances_offer_text_id_offer_texts_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_offer_acceptances"),
        sa.UniqueConstraint(
            "user_id",
            "offer_text_id",
            "purpose",
            name="uq_offer_acceptances_user_id_offer_text_id_purpose",
        ),
    )

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_kind", sa.String(length=8), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("object_type", sa.String(length=32), nullable=False),
        sa.Column(
            "object_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "event_type IN ("
            "'platform_admin.bootstrapped', "
            "'offer.version_created', "
            "'offer.text_updated', "
            "'offer.version_approved', "
            "'offer.version_made_current', "
            "'offer.version_demoted', "
            "'offer.registration_accepted'"
            ")",
            name="ck_audit_log_event_type_allowed",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('USER', 'SYSTEM')",
            name="ck_audit_log_actor_kind_allowed",
        ),
        sa.CheckConstraint(
            "object_type IN ("
            "'user', 'offer_version', 'offer_text', 'offer_acceptance'"
            ")",
            name="ck_audit_log_object_type_allowed",
        ),
        sa.CheckConstraint(
            "(event_type = 'platform_admin.bootstrapped' "
            "AND actor_kind = 'SYSTEM' "
            "AND actor_user_id IS NULL) "
            "OR (event_type <> 'platform_admin.bootstrapped' "
            "AND actor_kind = 'USER' "
            "AND actor_user_id IS NOT NULL)",
            name="ck_audit_log_actor_matches_event",
        ),
        sa.CheckConstraint(
            "(event_type = 'platform_admin.bootstrapped' "
            "AND object_type = 'user') "
            "OR (event_type IN ("
            "'offer.version_created', "
            "'offer.version_approved', "
            "'offer.version_made_current', "
            "'offer.version_demoted') "
            "AND object_type = 'offer_version') "
            "OR (event_type = 'offer.text_updated' "
            "AND object_type = 'offer_text') "
            "OR (event_type = 'offer.registration_accepted' "
            "AND object_type = 'offer_acceptance')",
            name="ck_audit_log_object_matches_event",
        ),
        sa.CheckConstraint(
            _AUDIT_PAYLOAD_EXACT_SHAPE_SQL,
            name="ck_audit_log_payload_exact_shape",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_log_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("audit_log")
    op.drop_table("offer_acceptances")
    op.drop_table("offer_texts")
    op.drop_index(
        "uq_offer_versions_current_purpose",
        table_name="offer_versions",
    )
    op.drop_table("offer_versions")
    op.drop_column("users", "is_platform_admin")
