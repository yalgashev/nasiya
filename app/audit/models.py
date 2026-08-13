from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.audit.contracts import (
    AuditActorKind,
    AuditEventType,
    AuditObjectType,
)
from app.auth.models import utc_now
from app.db import Base


def _exact_payload_clause(
    event_type: AuditEventType,
    keys: tuple[str, ...],
    *,
    extra_predicate: str | None = None,
) -> str:
    key_array = ", ".join(f"'{key}'" for key in keys)
    clause = (
        f"(event_type = '{event_type.value}' "
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


_AUDIT_PAYLOAD_EXACT_SHAPE_SQL = (
    "jsonb_typeof(payload) = 'object' AND ("
    + " OR ".join(
        (
            _exact_payload_clause(
                AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
                ("bootstrap_method",),
                extra_predicate=("payload ->> 'bootstrap_method' = 'operator_cli'"),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_VERSION_CREATED,
                ("purpose", "version_number", "status"),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_TEXT_UPDATED,
                ("purpose", "version_number", "language", "content_hash"),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_VERSION_APPROVED,
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
                AuditEventType.OFFER_VERSION_MADE_CURRENT,
                (
                    "purpose",
                    "version_number",
                    "from_status",
                    "to_status",
                    "previous_current_version_id",
                ),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_VERSION_DEMOTED,
                (
                    "purpose",
                    "version_number",
                    "from_status",
                    "to_status",
                    "replacement_version_id",
                ),
            ),
            _exact_payload_clause(
                AuditEventType.OFFER_REGISTRATION_ACCEPTED,
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
                AuditEventType.CUSTOMER_IDENTITY_SAVED,
                ("revision", "created_or_updated", "document_type"),
                extra_predicate=(
                    "jsonb_typeof(payload -> 'revision') = 'number' "
                    "AND (payload ->> 'revision')::integer > 0 "
                    "AND payload ->> 'created_or_updated' IN ('created', 'updated') "
                    "AND payload ->> 'document_type' IN ('PASSPORT', 'ID_CARD')"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.CUSTOMER_DOCUMENT_ATTACHED,
                ("status", "submission_replayed"),
                extra_predicate=(
                    "payload ->> 'status' = 'CURRENT' "
                    "AND payload -> 'submission_replayed' = 'false'::jsonb"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED,
                ("replacement_document_id",),
                extra_predicate=(
                    "payload ->> 'replacement_document_id' "
                    "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                    "[89ab][0-9a-f]{3}-[0-9a-f]{12}$'"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED,
                ("ttl_seconds",),
                extra_predicate=(
                    "jsonb_typeof(payload -> 'ttl_seconds') = 'number' "
                    "AND (payload ->> 'ttl_seconds')::integer BETWEEN 60 AND 900"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.CUSTOMER_ACTIVATED,
                ("from_status", "to_status", "activation_method"),
                extra_predicate=(
                    "payload ->> 'from_status' = 'draft' "
                    "AND payload ->> 'to_status' = 'active' "
                    "AND payload ->> 'activation_method' = "
                    "'TELEGRAM_REGISTRATION_OTP'"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.SHOP_CUSTOMER_LINKED,
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
                AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED,
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
                    "payload -> 'old_list_status' <> "
                    "payload -> 'new_list_status')"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED,
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
                AuditEventType.DEBT_CREATED,
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
                AuditEventType.DEBT_ACCEPTED,
                ("offer_version_number", "language", "content_hash"),
                extra_predicate=(
                    _whole_number_predicate("offer_version_number", minimum=1)
                    + " AND payload ->> 'language' IN ('UZ_LATN', 'UZ_CYRL', 'RU') "
                    "AND payload ->> 'content_hash' ~ '^[0-9a-f]{64}$'"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_REJECTED,
                ("reason_provided",),
                extra_predicate=(
                    "jsonb_typeof(payload -> 'reason_provided') = 'boolean'"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_CANCELLED,
                ("reason_provided",),
                extra_predicate="payload -> 'reason_provided' = 'true'::jsonb",
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_EXPIRED,
                ("source",),
                extra_predicate="payload ->> 'source' IN ('inline', 'batch')",
            ),
            _exact_payload_clause(
                AuditEventType.PAYMENT_RECORDED,
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
                    "AND payload ->> 'from_status' = 'active' "
                    "AND payload ->> 'to_status' IN ('active', 'paid') AND "
                    + _whole_number_predicate("debt_revision_after", minimum=1)
                ),
            ),
            _exact_payload_clause(
                AuditEventType.PAYMENT_VOIDED,
                ("reason", "from_status", "to_status", "debt_revision_after"),
                extra_predicate=(
                    "payload ->> 'reason' IN "
                    "('duplicate_payment','incorrect_amount','incorrect_method',"
                    "'payment_not_received','wrong_debt') AND "
                    "((payload ->> 'from_status' = 'active' AND "
                    "payload ->> 'to_status' = 'active') OR "
                    "(payload ->> 'from_status' = 'overdue' AND "
                    "payload ->> 'to_status' = 'overdue') OR "
                    "(payload ->> 'from_status' = 'written_off' AND "
                    "payload ->> 'to_status' = 'written_off') OR "
                    "(payload ->> 'from_status' = 'paid' AND "
                    "payload ->> 'to_status' IN ('active','overdue')) OR "
                    "(payload ->> 'from_status' = 'written_off_settled' AND "
                    "payload ->> 'to_status' = 'written_off')) AND "
                    + _whole_number_predicate("debt_revision_after", minimum=1)
                ),
            ),
            _exact_payload_clause(
                AuditEventType.PAYMENT_RECORDED,
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
                    "AND payload ->> 'from_status' = 'written_off' "
                    "AND payload ->> 'to_status' IN "
                    "('written_off', 'written_off_settled') AND "
                    + _whole_number_predicate("debt_revision_after", minimum=1)
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_WRITTEN_OFF,
                (
                    "reason_provided",
                    "from_status",
                    "to_status",
                    "written_off_revision",
                ),
                extra_predicate=(
                    "payload -> 'reason_provided' = 'true'::jsonb "
                    "AND jsonb_typeof(payload -> 'from_status') = 'string' "
                    "AND payload ->> 'from_status' = 'overdue' "
                    "AND jsonb_typeof(payload -> 'to_status') = 'string' "
                    "AND payload ->> 'to_status' = 'written_off' AND "
                    + _whole_number_predicate("written_off_revision", minimum=1)
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_WRITTEN_OFF_SETTLED,
                ("source", "from_status", "to_status", "debt_revision_after"),
                extra_predicate=(
                    "jsonb_typeof(payload -> 'source') = 'string' "
                    "AND payload ->> 'source' = 'payment' "
                    "AND jsonb_typeof(payload -> 'from_status') = 'string' "
                    "AND payload ->> 'from_status' = 'written_off' "
                    "AND jsonb_typeof(payload -> 'to_status') = 'string' "
                    "AND payload ->> 'to_status' = 'written_off_settled' AND "
                    + _whole_number_predicate("debt_revision_after", minimum=1)
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DISCLOSURE_RISK_BAND_VIEWED,
                ("purpose", "band"),
                extra_predicate=(
                    "jsonb_typeof(payload -> 'purpose') = 'string' "
                    "AND jsonb_typeof(payload -> 'band') = 'string' "
                    "AND payload ->> 'purpose' IN "
                    "('debt_proposal_review','credit_limit_review',"
                    "'existing_debt_review') "
                    "AND payload ->> 'band' IN "
                    "('new','green','yellow','red','blocked')"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_PAID,
                ("source", "debt_revision_after"),
                extra_predicate=(
                    "payload ->> 'source' = 'payment' AND "
                    + _whole_number_predicate("debt_revision_after", minimum=1)
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_REOPENED_AFTER_PAYMENT_VOID,
                ("source", "from_status", "to_status", "debt_revision_after"),
                extra_predicate=(
                    "payload ->> 'source' = 'payment_void' AND "
                    "((payload ->> 'from_status' = 'paid' AND "
                    "payload ->> 'to_status' IN ('active','overdue')) OR "
                    "(payload ->> 'from_status' = 'written_off_settled' AND "
                    "payload ->> 'to_status' = 'written_off')) AND "
                    + _whole_number_predicate("debt_revision_after", minimum=1)
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_OVERDUE,
                (
                    "source",
                    "from_status",
                    "to_status",
                    "overdue_revision",
                    "business_date",
                ),
                extra_predicate=(
                    "((payload ->> 'source' IN ('inline_payment', 'batch') "
                    "AND payload ->> 'from_status' = 'active') OR "
                    "(payload ->> 'source' = 'payment_void' "
                    "AND payload ->> 'from_status' = 'paid')) "
                    "AND payload ->> 'to_status' = 'overdue' AND "
                    + _whole_number_predicate("overdue_revision", minimum=1)
                    + " AND payload ->> 'business_date' "
                    "~ '^\\d{4}-\\d{2}-\\d{2}$'"
                ),
            ),
            _exact_payload_clause(
                AuditEventType.DEBT_CLAWBACK_APPLIED,
                (
                    "source",
                    "from_basis",
                    "to_basis",
                    "balance_increase_uzs",
                    "overdue_revision",
                ),
                extra_predicate=(
                    "payload ->> 'source' IN "
                    "('inline_payment', 'batch', 'payment_void') "
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
                AuditEventType.PAYMENT_RECORDED,
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
    )
    + ")"
)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            (
                "event_type IN ("
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}', "
                f"'{AuditEventType.OFFER_VERSION_CREATED.value}', "
                f"'{AuditEventType.OFFER_TEXT_UPDATED.value}', "
                f"'{AuditEventType.OFFER_VERSION_APPROVED.value}', "
                f"'{AuditEventType.OFFER_VERSION_MADE_CURRENT.value}', "
                f"'{AuditEventType.OFFER_VERSION_DEMOTED.value}', "
                f"'{AuditEventType.OFFER_REGISTRATION_ACCEPTED.value}'"
                f", '{AuditEventType.CUSTOMER_IDENTITY_SAVED.value}'"
                f", '{AuditEventType.CUSTOMER_DOCUMENT_ATTACHED.value}'"
                f", '{AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED.value}'"
                f", '{AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED.value}'"
                f", '{AuditEventType.CUSTOMER_ACTIVATED.value}'"
                f", '{AuditEventType.SHOP_CUSTOMER_LINKED.value}'"
                f", '{AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED.value}'"
                f", '{AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED.value}'"
                f", '{AuditEventType.DEBT_CREATED.value}'"
                f", '{AuditEventType.DEBT_ACCEPTED.value}'"
                f", '{AuditEventType.DEBT_REJECTED.value}'"
                f", '{AuditEventType.DEBT_CANCELLED.value}'"
                f", '{AuditEventType.DEBT_EXPIRED.value}'"
                f", '{AuditEventType.DEBT_OVERDUE.value}'"
                f", '{AuditEventType.DEBT_CLAWBACK_APPLIED.value}'"
                f", '{AuditEventType.PAYMENT_RECORDED.value}'"
                f", '{AuditEventType.DEBT_PAID.value}'"
                f", '{AuditEventType.DISCLOSURE_RISK_BAND_VIEWED.value}'"
                f", '{AuditEventType.DEBT_WRITTEN_OFF.value}'"
                f", '{AuditEventType.DEBT_WRITTEN_OFF_SETTLED.value}'"
                f", '{AuditEventType.PAYMENT_VOIDED.value}'"
                f", '{AuditEventType.DEBT_REOPENED_AFTER_PAYMENT_VOID.value}'"
                ")"
            ),
            name="ck_audit_log_event_type_allowed",
        ),
        CheckConstraint(
            (
                "actor_kind IN "
                f"('{AuditActorKind.USER.value}', "
                f"'{AuditActorKind.SYSTEM.value}')"
            ),
            name="ck_audit_log_actor_kind_allowed",
        ),
        CheckConstraint(
            (
                "object_type IN ("
                f"'{AuditObjectType.USER.value}', "
                f"'{AuditObjectType.OFFER_VERSION.value}', "
                f"'{AuditObjectType.OFFER_TEXT.value}', "
                f"'{AuditObjectType.OFFER_ACCEPTANCE.value}'"
                f", '{AuditObjectType.CUSTOMER_IDENTITY.value}'"
                f", '{AuditObjectType.CUSTOMER_DOCUMENT.value}'"
                f", '{AuditObjectType.CUSTOMER.value}'"
                f", '{AuditObjectType.SHOP_CUSTOMER.value}'"
                f", '{AuditObjectType.SHOP.value}'"
                f", '{AuditObjectType.DEBT.value}'"
                f", '{AuditObjectType.PAYMENT.value}'"
                f", '{AuditObjectType.DISCLOSURE_VIEW.value}'"
                ")"
            ),
            name="ck_audit_log_object_type_allowed",
        ),
        CheckConstraint(
            (
                "(event_type IN ("
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}', "
                f"'{AuditEventType.DEBT_EXPIRED.value}', "
                f"'{AuditEventType.DEBT_OVERDUE.value}', "
                f"'{AuditEventType.DEBT_CLAWBACK_APPLIED.value}') "
                f"AND actor_kind = '{AuditActorKind.SYSTEM.value}' "
                "AND actor_user_id IS NULL) "
                "OR (event_type NOT IN ("
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}', "
                f"'{AuditEventType.DEBT_EXPIRED.value}', "
                f"'{AuditEventType.DEBT_OVERDUE.value}', "
                f"'{AuditEventType.DEBT_CLAWBACK_APPLIED.value}') "
                f"AND actor_kind = '{AuditActorKind.USER.value}' "
                "AND actor_user_id IS NOT NULL)"
            ),
            name="ck_audit_log_actor_matches_event",
        ),
        CheckConstraint(
            (
                f"(event_type = "
                f"'{AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED.value}' "
                f"AND object_type = '{AuditObjectType.USER.value}') "
                f"OR (event_type IN ("
                f"'{AuditEventType.OFFER_VERSION_CREATED.value}', "
                f"'{AuditEventType.OFFER_VERSION_APPROVED.value}', "
                f"'{AuditEventType.OFFER_VERSION_MADE_CURRENT.value}', "
                f"'{AuditEventType.OFFER_VERSION_DEMOTED.value}') "
                f"AND object_type = '{AuditObjectType.OFFER_VERSION.value}') "
                f"OR (event_type = '{AuditEventType.OFFER_TEXT_UPDATED.value}' "
                f"AND object_type = '{AuditObjectType.OFFER_TEXT.value}') "
                f"OR (event_type = "
                f"'{AuditEventType.OFFER_REGISTRATION_ACCEPTED.value}' "
                f"AND object_type = '{AuditObjectType.OFFER_ACCEPTANCE.value}')"
                f" OR (event_type = "
                f"'{AuditEventType.CUSTOMER_IDENTITY_SAVED.value}' "
                f"AND object_type = '{AuditObjectType.CUSTOMER_IDENTITY.value}')"
                f" OR (event_type IN ("
                f"'{AuditEventType.CUSTOMER_DOCUMENT_ATTACHED.value}', "
                f"'{AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED.value}', "
                f"'{AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED.value}') "
                f"AND object_type = '{AuditObjectType.CUSTOMER_DOCUMENT.value}')"
                f" OR (event_type = '{AuditEventType.CUSTOMER_ACTIVATED.value}' "
                f"AND object_type = '{AuditObjectType.CUSTOMER.value}')"
                f" OR (event_type IN ("
                f"'{AuditEventType.SHOP_CUSTOMER_LINKED.value}', "
                f"'{AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED.value}') "
                f"AND object_type = '{AuditObjectType.SHOP_CUSTOMER.value}')"
                f" OR (event_type = "
                f"'{AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED.value}' "
                f"AND object_type = '{AuditObjectType.SHOP.value}')"
                f" OR (event_type IN ('{AuditEventType.DEBT_CREATED.value}', "
                f"'{AuditEventType.DEBT_ACCEPTED.value}', "
                f"'{AuditEventType.DEBT_REJECTED.value}', "
                f"'{AuditEventType.DEBT_CANCELLED.value}', "
                f"'{AuditEventType.DEBT_EXPIRED.value}', "
                f"'{AuditEventType.DEBT_OVERDUE.value}', "
                f"'{AuditEventType.DEBT_CLAWBACK_APPLIED.value}', "
                f"'{AuditEventType.DEBT_PAID.value}', "
                f"'{AuditEventType.DEBT_WRITTEN_OFF.value}', "
                f"'{AuditEventType.DEBT_WRITTEN_OFF_SETTLED.value}', "
                f"'{AuditEventType.DEBT_REOPENED_AFTER_PAYMENT_VOID.value}') "
                f"AND object_type = '{AuditObjectType.DEBT.value}')"
                f" OR (event_type = '{AuditEventType.PAYMENT_RECORDED.value}' "
                f"AND object_type = '{AuditObjectType.PAYMENT.value}')"
                f" OR (event_type = '{AuditEventType.PAYMENT_VOIDED.value}' "
                f"AND object_type = '{AuditObjectType.PAYMENT.value}')"
                f" OR (event_type = "
                f"'{AuditEventType.DISCLOSURE_RISK_BAND_VIEWED.value}' "
                f"AND object_type = "
                f"'{AuditObjectType.DISCLOSURE_VIEW.value}')"
            ),
            name="ck_audit_log_object_matches_event",
        ),
        CheckConstraint(
            _AUDIT_PAYLOAD_EXACT_SHAPE_SQL,
            name="ck_audit_log_payload_exact_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            name="fk_audit_log_actor_user_id_users_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            "AuditLog("
            f"id={'<set>' if self.id is not None else '<unset>'}, "
            f"occurred_at={self.occurred_at!r}, "
            f"event_type={self.event_type!r}, "
            f"actor_kind={self.actor_kind!r}, "
            f"object_type={self.object_type!r}, "
            "object_id=<redacted>, actor_user_id=<redacted>, "
            "payload=<redacted>)"
        )
