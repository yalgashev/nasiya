"""extend customer activation foundation

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "b0c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_M10_OTP_EVENT_ACTIONS = (
    "ISSUED",
    "DISPATCH_PREPARED",
    "DISPATCH_RESULT",
    "VERIFY_FAILED",
    "CONSUMED",
    "SUPERSEDED",
    "EXPIRED",
    "BURNED",
    "INVALIDATED_BY_LINK_CHANGE",
)
_M11_OTP_EVENT_ACTIONS = _M10_OTP_EVENT_ACTIONS + (
    "INVALIDATED_BY_REGISTRATION_STATE_CHANGE",
)
_M10_AUDIT_EVENTS = (
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
)
_M11_AUDIT_EVENTS = _M10_AUDIT_EVENTS + ("customer.activated",)
_M10_AUDIT_OBJECT_TYPES = (
    "user",
    "offer_version",
    "offer_text",
    "offer_acceptance",
    "customer_identity",
    "customer_document",
)
_M11_AUDIT_OBJECT_TYPES = _M10_AUDIT_OBJECT_TYPES + ("customer",)
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


def _audit_payload_shape_sql(*, include_m11: bool) -> str:
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
    )
    if include_m11:
        clauses += (
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
    return "jsonb_typeof(payload) = 'object' AND (" + " OR ".join(clauses) + ")"


def _audit_object_matches_event_sql(*, include_m11: bool) -> str:
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
        "AND object_type = 'customer_document')"
    )
    if include_m11:
        clause += " OR (event_type = 'customer.activated' AND object_type = 'customer')"
    return clause


def _replace_audit_checks(*, include_m11: bool) -> None:
    for constraint_name in _AUDIT_CHECK_NAMES:
        op.drop_constraint(constraint_name, "audit_log", type_="check")
    events = _M11_AUDIT_EVENTS if include_m11 else _M10_AUDIT_EVENTS
    object_types = _M11_AUDIT_OBJECT_TYPES if include_m11 else _M10_AUDIT_OBJECT_TYPES
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
        _audit_object_matches_event_sql(include_m11=include_m11),
    )
    op.create_check_constraint(
        "ck_audit_log_payload_exact_shape",
        "audit_log",
        _audit_payload_shape_sql(include_m11=include_m11),
    )


def _replace_otp_event_action_check(*, include_m11: bool) -> None:
    op.drop_constraint(
        "ck_otp_challenge_events_action_allowed",
        "otp_challenge_events",
        type_="check",
    )
    actions = _M11_OTP_EVENT_ACTIONS if include_m11 else _M10_OTP_EVENT_ACTIONS
    op.create_check_constraint(
        "ck_otp_challenge_events_action_allowed",
        "otp_challenge_events",
        f"action IN ({_sql_values(actions)})",
    )


def _validate(table_name: str, constraint_name: str) -> None:
    op.execute(
        sa.text(f'ALTER TABLE "{table_name}" VALIDATE CONSTRAINT "{constraint_name}"')
    )


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "otp_challenges",
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "otp_challenges",
        sa.Column(
            "registration_offer_acceptance_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "otp_challenges",
        sa.Column("customer_identity_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "otp_challenges",
        sa.Column(
            "customer_document_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_otp_challenges_customer_id_customers_id",
        "otp_challenges",
        "customers",
        ["customer_id"],
        ["id"],
        ondelete="RESTRICT",
        postgresql_not_valid=True,
    )
    op.create_foreign_key(
        "fk_otp_challenges_registration_acceptance_offer_acceptances",
        "otp_challenges",
        "offer_acceptances",
        ["registration_offer_acceptance_id"],
        ["id"],
        ondelete="RESTRICT",
        postgresql_not_valid=True,
    )
    op.create_foreign_key(
        "fk_otp_challenges_customer_document_id_customer_documents",
        "otp_challenges",
        "customer_documents",
        ["customer_document_id"],
        ["id"],
        ondelete="RESTRICT",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_customers_onboarding_status_allowed",
        "customers",
        "onboarding_status IN ('draft', 'active')",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_customers_activation_state_consistent",
        "customers",
        "(onboarding_status = 'draft' AND activated_at IS NULL) "
        "OR (onboarding_status = 'active' AND activated_at IS NOT NULL)",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_customers_timestamp_order",
        "customers",
        "updated_at >= created_at "
        "AND (activated_at IS NULL OR activated_at >= created_at) "
        "AND (activated_at IS NULL OR updated_at >= activated_at)",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_otp_challenges_purpose_allowed",
        "otp_challenges",
        "purpose IN ('LOGIN', 'REGISTRATION')",
        postgresql_not_valid=True,
    )
    op.create_check_constraint(
        "ck_otp_challenges_registration_context_matches_purpose",
        "otp_challenges",
        "(purpose = 'LOGIN' "
        "AND customer_id IS NULL "
        "AND registration_offer_acceptance_id IS NULL "
        "AND customer_identity_revision IS NULL "
        "AND customer_document_id IS NULL) "
        "OR (purpose = 'REGISTRATION' "
        "AND user_id IS NOT NULL "
        "AND telegram_link_id IS NOT NULL "
        "AND telegram_linked_at IS NOT NULL "
        "AND customer_id IS NOT NULL "
        "AND registration_offer_acceptance_id IS NOT NULL "
        "AND customer_identity_revision > 0 "
        "AND customer_document_id IS NOT NULL)",
        postgresql_not_valid=True,
    )

    for table_name, constraint_name in (
        ("otp_challenges", "fk_otp_challenges_customer_id_customers_id"),
        (
            "otp_challenges",
            "fk_otp_challenges_registration_acceptance_offer_acceptances",
        ),
        (
            "otp_challenges",
            "fk_otp_challenges_customer_document_id_customer_documents",
        ),
        ("customers", "ck_customers_onboarding_status_allowed"),
        ("customers", "ck_customers_activation_state_consistent"),
        ("customers", "ck_customers_timestamp_order"),
        ("otp_challenges", "ck_otp_challenges_purpose_allowed"),
        (
            "otp_challenges",
            "ck_otp_challenges_registration_context_matches_purpose",
        ),
    ):
        _validate(table_name, constraint_name)

    op.drop_constraint(
        "ck_customers_onboarding_status_draft_only",
        "customers",
        type_="check",
    )
    op.drop_constraint(
        "ck_otp_challenges_purpose_login",
        "otp_challenges",
        type_="check",
    )
    _replace_otp_event_action_check(include_m11=True)
    _replace_audit_checks(include_m11=True)


def downgrade() -> None:
    connection = op.get_bind()
    active_customer_exists = connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM customers "
            "WHERE onboarding_status = 'active' OR activated_at IS NOT NULL"
            ")"
        )
    )
    if active_customer_exists:
        raise RuntimeError("M11 downgrade blocked: active customer state exists")

    _replace_audit_checks(include_m11=False)
    _replace_otp_event_action_check(include_m11=False)

    op.create_check_constraint(
        "ck_otp_challenges_purpose_login",
        "otp_challenges",
        "purpose = 'LOGIN'",
        postgresql_not_valid=True,
    )
    _validate("otp_challenges", "ck_otp_challenges_purpose_login")
    op.drop_constraint(
        "ck_otp_challenges_registration_context_matches_purpose",
        "otp_challenges",
        type_="check",
    )
    op.drop_constraint(
        "ck_otp_challenges_purpose_allowed",
        "otp_challenges",
        type_="check",
    )
    op.drop_constraint(
        "fk_otp_challenges_customer_document_id_customer_documents",
        "otp_challenges",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_otp_challenges_registration_acceptance_offer_acceptances",
        "otp_challenges",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_otp_challenges_customer_id_customers_id",
        "otp_challenges",
        type_="foreignkey",
    )
    op.drop_column("otp_challenges", "customer_document_id")
    op.drop_column("otp_challenges", "customer_identity_revision")
    op.drop_column("otp_challenges", "registration_offer_acceptance_id")
    op.drop_column("otp_challenges", "customer_id")

    op.create_check_constraint(
        "ck_customers_onboarding_status_draft_only",
        "customers",
        "onboarding_status = 'draft'",
        postgresql_not_valid=True,
    )
    _validate("customers", "ck_customers_onboarding_status_draft_only")
    op.drop_constraint(
        "ck_customers_timestamp_order",
        "customers",
        type_="check",
    )
    op.drop_constraint(
        "ck_customers_activation_state_consistent",
        "customers",
        type_="check",
    )
    op.drop_constraint(
        "ck_customers_onboarding_status_allowed",
        "customers",
        type_="check",
    )
    op.drop_column("customers", "activated_at")
