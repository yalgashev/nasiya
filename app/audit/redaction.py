from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID

from app.audit.contracts import (
    CUSTOMER_ACTIVATION_FROM_STATUS,
    CUSTOMER_ACTIVATION_METHOD,
    CUSTOMER_ACTIVATION_TO_STATUS,
    AuditEvent,
    AuditEventType,
)
from app.customer_document.contracts import CustomerDocumentStatus
from app.customer_identity.contracts import CustomerDocumentType
from app.debt.enums import DebtExpirySource, DebtOverdueSource
from app.debt.values import MAX_DEBT_AMOUNT_UZS
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
from app.payment.enums import PaymentMethod
from app.payment.values import MAX_PAYMENT_AMOUNT_UZS
from app.shop_customer.enums import ShopCustomerListStatus
from app.shop_customer.values import MAX_CREDIT_LIMIT_UZS, MAX_OPEN_DEBTS

AuditPayload = dict[str, str | int | bool | None]
_CONTENT_HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_LEGAL_REVIEW_REFERENCE_PATTERN: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._ -]{0,199}"
)


def redact_audit_payload(event: AuditEvent) -> AuditPayload:
    builder = _PAYLOAD_BUILDERS[event.event_type]
    return builder(event.candidate_metadata)


def _bootstrap_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(metadata, "bootstrap_method")
    method = values["bootstrap_method"]
    if method != "operator_cli":
        raise ValueError("Audit bootstrap method is invalid")
    return {"bootstrap_method": "operator_cli"}


def _version_created_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(metadata, "purpose", "version_number", "status")
    status = _status(values["status"])
    if status is not OfferStatus.DRAFT:
        raise ValueError("Created offer audit status must be DRAFT")
    return {
        "purpose": _purpose(values["purpose"]),
        "version_number": _positive_integer(values["version_number"]),
        "status": status.value,
    }


def _text_updated_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(
        metadata,
        "purpose",
        "version_number",
        "language",
        "content_hash",
    )
    return {
        "purpose": _purpose(values["purpose"]),
        "version_number": _positive_integer(values["version_number"]),
        "language": _language(values["language"]),
        "content_hash": _content_hash(values["content_hash"]),
    }


def _version_approved_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(
        metadata,
        "purpose",
        "version_number",
        "from_status",
        "to_status",
        "legal_review_authority",
        "legal_review_reference",
        "legal_reviewed_at",
    )
    _require_transition(
        values,
        source=OfferStatus.DRAFT,
        target=OfferStatus.APPROVED,
    )
    return {
        "purpose": _purpose(values["purpose"]),
        "version_number": _positive_integer(values["version_number"]),
        "from_status": OfferStatus.DRAFT.value,
        "to_status": OfferStatus.APPROVED.value,
        "legal_review_authority": _review_authority(values["legal_review_authority"]),
        "legal_review_reference": _review_reference(values["legal_review_reference"]),
        "legal_reviewed_at": _utc_iso8601(values["legal_reviewed_at"]),
    }


def _version_made_current_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(
        metadata,
        "purpose",
        "version_number",
        "from_status",
        "to_status",
        "previous_current_version_id",
    )
    _require_transition(
        values,
        source=OfferStatus.APPROVED,
        target=OfferStatus.CURRENT,
    )
    return {
        "purpose": _purpose(values["purpose"]),
        "version_number": _positive_integer(values["version_number"]),
        "from_status": OfferStatus.APPROVED.value,
        "to_status": OfferStatus.CURRENT.value,
        "previous_current_version_id": _nullable_uuid(
            values["previous_current_version_id"]
        ),
    }


def _version_demoted_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(
        metadata,
        "purpose",
        "version_number",
        "from_status",
        "to_status",
        "replacement_version_id",
    )
    _require_transition(
        values,
        source=OfferStatus.CURRENT,
        target=OfferStatus.APPROVED,
    )
    return {
        "purpose": _purpose(values["purpose"]),
        "version_number": _positive_integer(values["version_number"]),
        "from_status": OfferStatus.CURRENT.value,
        "to_status": OfferStatus.APPROVED.value,
        "replacement_version_id": _uuid(values["replacement_version_id"]),
    }


def _registration_accepted_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(
        metadata,
        "purpose",
        "offer_version_id",
        "offer_text_id",
        "version_number",
        "language",
        "content_hash",
    )
    purpose = values["purpose"]
    if purpose is not OfferPurpose.REGISTRATION:
        raise ValueError("Acceptance audit purpose must be REGISTRATION")
    return {
        "purpose": OfferPurpose.REGISTRATION.value,
        "offer_version_id": _uuid(values["offer_version_id"]),
        "offer_text_id": _uuid(values["offer_text_id"]),
        "version_number": _positive_integer(values["version_number"]),
        "language": _language(values["language"]),
        "content_hash": _content_hash(values["content_hash"]),
    }


def _customer_identity_saved_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(
        metadata,
        "revision",
        "created_or_updated",
        "document_type",
    )
    created_or_updated = values["created_or_updated"]
    if created_or_updated not in {"created", "updated"}:
        raise ValueError("Audit identity save outcome is invalid")
    document_type = values["document_type"]
    if not isinstance(document_type, CustomerDocumentType):
        raise ValueError("Audit customer document type is invalid")
    return {
        "revision": _positive_integer(values["revision"]),
        "created_or_updated": created_or_updated,
        "document_type": document_type.value,
    }


def _customer_document_attached_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(metadata, "status", "submission_replayed")
    if values["status"] is not CustomerDocumentStatus.CURRENT:
        raise ValueError("Audit attached document status is invalid")
    if values["submission_replayed"] is not False:
        raise ValueError("Audit attached document replay marker is invalid")
    return {
        "status": CustomerDocumentStatus.CURRENT.value,
        "submission_replayed": False,
    }


def _customer_document_superseded_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(metadata, "replacement_document_id")
    return {"replacement_document_id": _uuid(values["replacement_document_id"])}


def _customer_document_access_granted_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(metadata, "ttl_seconds")
    ttl_seconds = values["ttl_seconds"]
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 60 <= ttl_seconds <= 900
    ):
        raise ValueError("Audit document access TTL is invalid")
    return {"ttl_seconds": ttl_seconds}


def _customer_activated_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(
        metadata,
        "from_status",
        "to_status",
        "activation_method",
    )
    if values["from_status"] != CUSTOMER_ACTIVATION_FROM_STATUS:
        raise ValueError("Audit activation source status is invalid")
    if values["to_status"] != CUSTOMER_ACTIVATION_TO_STATUS:
        raise ValueError("Audit activation target status is invalid")
    if values["activation_method"] != CUSTOMER_ACTIVATION_METHOD:
        raise ValueError("Audit activation method is invalid")
    return {
        "from_status": CUSTOMER_ACTIVATION_FROM_STATUS,
        "to_status": CUSTOMER_ACTIVATION_TO_STATUS,
        "activation_method": CUSTOMER_ACTIVATION_METHOD,
    }


def _shop_customer_linked_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(
        metadata,
        "outcome",
        "credit_limit_uzs",
        "max_open_debts",
        "list_status",
        "revision",
    )
    if values["outcome"] != "created":
        raise ValueError("Shop customer linked audit outcome is invalid")
    return {
        "outcome": "created",
        "credit_limit_uzs": _credit_limit_uzs(values["credit_limit_uzs"]),
        "max_open_debts": _max_open_debts(values["max_open_debts"]),
        "list_status": _shop_customer_list_status(values["list_status"]),
        "revision": _positive_integer(values["revision"]),
    }


def _shop_customer_policy_updated_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(
        metadata,
        "old_credit_limit_uzs",
        "new_credit_limit_uzs",
        "old_max_open_debts",
        "new_max_open_debts",
        "old_list_status",
        "new_list_status",
        "revision",
    )
    payload = {
        "old_credit_limit_uzs": _credit_limit_uzs(values["old_credit_limit_uzs"]),
        "new_credit_limit_uzs": _credit_limit_uzs(values["new_credit_limit_uzs"]),
        "old_max_open_debts": _max_open_debts(values["old_max_open_debts"]),
        "new_max_open_debts": _max_open_debts(values["new_max_open_debts"]),
        "old_list_status": _shop_customer_list_status(values["old_list_status"]),
        "new_list_status": _shop_customer_list_status(values["new_list_status"]),
        "revision": _positive_integer(values["revision"]),
    }
    if (
        payload["old_credit_limit_uzs"],
        payload["old_max_open_debts"],
        payload["old_list_status"],
    ) == (
        payload["new_credit_limit_uzs"],
        payload["new_max_open_debts"],
        payload["new_list_status"],
    ):
        raise ValueError("Shop customer audit policy change must be real")
    return payload


def _shop_customer_defaults_updated_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _required(
        metadata,
        "old_default_credit_limit_uzs",
        "new_default_credit_limit_uzs",
        "old_default_max_open_debts",
        "new_default_max_open_debts",
    )
    payload = {
        "old_default_credit_limit_uzs": _credit_limit_uzs(
            values["old_default_credit_limit_uzs"]
        ),
        "new_default_credit_limit_uzs": _credit_limit_uzs(
            values["new_default_credit_limit_uzs"]
        ),
        "old_default_max_open_debts": _max_open_debts(
            values["old_default_max_open_debts"]
        ),
        "new_default_max_open_debts": _max_open_debts(
            values["new_default_max_open_debts"]
        ),
    }
    if (
        payload["old_default_credit_limit_uzs"],
        payload["old_default_max_open_debts"],
    ) == (
        payload["new_default_credit_limit_uzs"],
        payload["new_default_max_open_debts"],
    ):
        raise ValueError("Shop defaults audit policy change must be real")
    return payload


def _debt_created_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(
        metadata,
        "original_amount_uzs",
        "discount_basis_points",
        "discounted_amount_uzs",
        "due_date",
        "pending_expires_at",
    )
    original_amount = _debt_amount_uzs(values["original_amount_uzs"])
    discounted_amount = _debt_amount_uzs(values["discounted_amount_uzs"])
    if discounted_amount > original_amount:
        raise ValueError("Debt created audit discounted amount is invalid")
    return {
        "original_amount_uzs": original_amount,
        "discount_basis_points": _discount_basis_points(
            values["discount_basis_points"]
        ),
        "discounted_amount_uzs": discounted_amount,
        "due_date": _iso_date(values["due_date"]),
        "pending_expires_at": _serialized_utc_iso8601(values["pending_expires_at"]),
    }


def _debt_accepted_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(
        metadata,
        "offer_version_number",
        "language",
        "content_hash",
    )
    return {
        "offer_version_number": _positive_integer(values["offer_version_number"]),
        "language": _serialized_language(values["language"]),
        "content_hash": _content_hash(values["content_hash"]),
    }


def _debt_rejected_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(metadata, "reason_provided")
    return {"reason_provided": _boolean(values["reason_provided"])}


def _debt_cancelled_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(metadata, "reason_provided")
    if values["reason_provided"] is not True:
        raise ValueError("Debt cancelled audit requires a reason")
    return {"reason_provided": True}


def _debt_expired_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _required(metadata, "source")
    source = values["source"]
    if not isinstance(source, str):
        raise ValueError("Debt expired audit source is invalid")
    try:
        return {"source": DebtExpirySource(source).value}
    except ValueError as exc:
        raise ValueError("Debt expired audit source is invalid") from exc


def _debt_overdue_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _exact_required(
        metadata,
        "source",
        "from_status",
        "to_status",
        "overdue_revision",
        "business_date",
    )
    if values["from_status"] != "active" or values["to_status"] != "overdue":
        raise ValueError("Debt overdue audit transition is invalid")
    return {
        "source": _overdue_source(values["source"]),
        "from_status": "active",
        "to_status": "overdue",
        "overdue_revision": _payment_revision(values["overdue_revision"]),
        "business_date": _iso_date(values["business_date"]),
    }


def _debt_clawback_applied_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _exact_required(
        metadata,
        "source",
        "from_basis",
        "to_basis",
        "balance_increase_uzs",
        "overdue_revision",
    )
    if values["from_basis"] != "discounted" or values["to_basis"] != "original":
        raise ValueError("Debt clawback audit basis transition is invalid")
    return {
        "source": _overdue_source(values["source"]),
        "from_basis": "discounted",
        "to_basis": "original",
        "balance_increase_uzs": _nonnegative_debt_amount_uzs(
            values["balance_increase_uzs"]
        ),
        "overdue_revision": _payment_revision(values["overdue_revision"]),
    }


def _payment_recorded_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _exact_required(
        metadata,
        "amount_uzs",
        "method",
        "from_status",
        "to_status",
        "debt_revision_after",
    )
    method = values["method"]
    if not isinstance(method, str):
        raise ValueError("Payment recorded audit method is invalid")
    try:
        canonical_method = PaymentMethod(method).value
    except ValueError as exc:
        raise ValueError("Payment recorded audit method is invalid") from exc
    allowed_targets = {
        "active": {"active", "paid"},
        "overdue": {"overdue", "paid"},
    }
    from_status = values["from_status"]
    if not isinstance(from_status, str) or from_status not in allowed_targets:
        raise ValueError("Payment recorded audit source status is invalid")
    if values["to_status"] not in allowed_targets[from_status]:
        raise ValueError("Payment recorded audit target status is invalid")
    return {
        "amount_uzs": _payment_amount_uzs(values["amount_uzs"]),
        "method": canonical_method,
        "from_status": from_status,
        "to_status": values["to_status"],
        "debt_revision_after": _payment_revision(values["debt_revision_after"]),
    }


def _debt_paid_payload(metadata: Mapping[str, object]) -> AuditPayload:
    values = _exact_required(metadata, "source", "debt_revision_after")
    if values["source"] != "payment":
        raise ValueError("Debt paid audit source is invalid")
    return {
        "source": "payment",
        "debt_revision_after": _payment_revision(values["debt_revision_after"]),
    }


def _risk_band_disclosure_payload(
    metadata: Mapping[str, object],
) -> AuditPayload:
    values = _exact_required(metadata, "purpose", "band")
    purpose = values["purpose"]
    band = values["band"]
    if not isinstance(purpose, str) or purpose not in {
        "debt_proposal_review",
        "credit_limit_review",
        "existing_debt_review",
    }:
        raise ValueError("Disclosure audit purpose is invalid")
    if not isinstance(band, str) or band not in {
        "new",
        "green",
        "yellow",
        "red",
        "blocked",
    }:
        raise ValueError("Disclosure audit band is invalid")
    return {"purpose": purpose, "band": band}


_PAYLOAD_BUILDERS: Final[
    Mapping[AuditEventType, Callable[[Mapping[str, object]], AuditPayload]]
] = {
    AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED: _bootstrap_payload,
    AuditEventType.OFFER_VERSION_CREATED: _version_created_payload,
    AuditEventType.OFFER_TEXT_UPDATED: _text_updated_payload,
    AuditEventType.OFFER_VERSION_APPROVED: _version_approved_payload,
    AuditEventType.OFFER_VERSION_MADE_CURRENT: _version_made_current_payload,
    AuditEventType.OFFER_VERSION_DEMOTED: _version_demoted_payload,
    AuditEventType.OFFER_REGISTRATION_ACCEPTED: _registration_accepted_payload,
    AuditEventType.CUSTOMER_IDENTITY_SAVED: _customer_identity_saved_payload,
    AuditEventType.CUSTOMER_DOCUMENT_ATTACHED: _customer_document_attached_payload,
    AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED: (
        _customer_document_superseded_payload
    ),
    AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED: (
        _customer_document_access_granted_payload
    ),
    AuditEventType.CUSTOMER_ACTIVATED: _customer_activated_payload,
    AuditEventType.SHOP_CUSTOMER_LINKED: _shop_customer_linked_payload,
    AuditEventType.SHOP_CUSTOMER_POLICY_UPDATED: _shop_customer_policy_updated_payload,
    AuditEventType.SHOP_CUSTOMER_DEFAULTS_UPDATED: (
        _shop_customer_defaults_updated_payload
    ),
    AuditEventType.DEBT_CREATED: _debt_created_payload,
    AuditEventType.DEBT_ACCEPTED: _debt_accepted_payload,
    AuditEventType.DEBT_REJECTED: _debt_rejected_payload,
    AuditEventType.DEBT_CANCELLED: _debt_cancelled_payload,
    AuditEventType.DEBT_EXPIRED: _debt_expired_payload,
    AuditEventType.DEBT_OVERDUE: _debt_overdue_payload,
    AuditEventType.DEBT_CLAWBACK_APPLIED: _debt_clawback_applied_payload,
    AuditEventType.PAYMENT_RECORDED: _payment_recorded_payload,
    AuditEventType.DEBT_PAID: _debt_paid_payload,
    AuditEventType.DISCLOSURE_RISK_BAND_VIEWED: (
        _risk_band_disclosure_payload
    ),
}


def _required(
    metadata: Mapping[str, object],
    *keys: str,
) -> dict[str, object]:
    missing = [key for key in keys if key not in metadata]
    if missing:
        raise ValueError("Audit payload is missing required metadata")
    return {key: metadata[key] for key in keys}


def _exact_required(
    metadata: Mapping[str, object],
    *keys: str,
) -> dict[str, object]:
    values = _required(metadata, *keys)
    unexpected = set(metadata).difference(keys)
    if unexpected:
        raise ValueError("Audit payload contains unexpected metadata")
    return values


def _purpose(value: object) -> str:
    if not isinstance(value, OfferPurpose):
        raise ValueError("Audit offer purpose is invalid")
    return value.value


def _language(value: object) -> str:
    if not isinstance(value, OfferLanguage):
        raise ValueError("Audit offer language is invalid")
    return value.value


def _status(value: object) -> OfferStatus:
    if not isinstance(value, OfferStatus):
        raise ValueError("Audit offer status is invalid")
    return value


def _positive_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Audit version number must be positive")
    return value


def _credit_limit_uzs(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= int(MAX_CREDIT_LIMIT_UZS)
    ):
        raise ValueError("Shop customer audit credit limit is invalid")
    return value


def _max_open_debts(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= MAX_OPEN_DEBTS
    ):
        raise ValueError("Shop customer audit maximum open debts is invalid")
    return value


def _shop_customer_list_status(value: object) -> str:
    if not isinstance(value, ShopCustomerListStatus):
        raise ValueError("Shop customer audit list status is invalid")
    return value.value


def _debt_amount_uzs(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= int(MAX_DEBT_AMOUNT_UZS)
    ):
        raise ValueError("Debt created audit amount is invalid")
    return value


def _nonnegative_debt_amount_uzs(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= int(MAX_DEBT_AMOUNT_UZS)
    ):
        raise ValueError("Debt audit amount increase is invalid")
    return value


def _overdue_source(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Debt overdue audit source is invalid")
    try:
        return DebtOverdueSource(value).value
    except ValueError as exc:
        raise ValueError("Debt overdue audit source is invalid") from exc


def _payment_amount_uzs(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= int(MAX_PAYMENT_AMOUNT_UZS)
    ):
        raise ValueError("Payment recorded audit amount is invalid")
    return value


def _payment_revision(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("Payment audit debt revision is invalid")
    return value


def _discount_basis_points(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= 10_000
    ):
        raise ValueError("Debt created audit discount is invalid")
    return value


def _iso_date(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Debt created audit due date is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Debt created audit due date is invalid") from exc
    if parsed.isoformat() != value:
        raise ValueError("Debt created audit due date is invalid")
    return value


def _serialized_utc_iso8601(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Debt created audit expiry is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Debt created audit expiry is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Debt created audit expiry is invalid")
    normalized = parsed.astimezone(UTC).isoformat()
    if normalized != value:
        raise ValueError("Debt created audit expiry is invalid")
    return value


def _serialized_language(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Debt accepted audit language is invalid")
    try:
        return OfferLanguage(value).value
    except ValueError as exc:
        raise ValueError("Debt accepted audit language is invalid") from exc


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Debt audit boolean is invalid")
    return value


def _content_hash(value: object) -> str:
    if not isinstance(value, str) or _CONTENT_HASH_PATTERN.fullmatch(value) is None:
        raise ValueError("Audit content hash is invalid")
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, UUID):
        raise ValueError("Audit UUID metadata is invalid")
    return str(value)


def _nullable_uuid(value: object) -> str | None:
    if value is None:
        return None
    return _uuid(value)


def _review_authority(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Audit legal review authority is invalid")
    authority = value.strip()
    if not 1 <= len(authority) <= 200 or any(
        unicodedata.category(char) == "Cc" for char in authority
    ):
        raise ValueError("Audit legal review authority is invalid")
    return authority


def _review_reference(value: object) -> str:
    if (
        not isinstance(value, str)
        or _LEGAL_REVIEW_REFERENCE_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("Audit legal review reference is invalid")
    return value


def _utc_iso8601(value: object) -> str:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Audit legal review time must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _require_transition(
    values: Mapping[str, object],
    *,
    source: OfferStatus,
    target: OfferStatus,
) -> None:
    if _status(values["from_status"]) is not source:
        raise ValueError("Audit offer transition source is invalid")
    if _status(values["to_status"]) is not target:
        raise ValueError("Audit offer transition target is invalid")
