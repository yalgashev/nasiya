from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
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
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus
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
}


def _required(
    metadata: Mapping[str, object],
    *keys: str,
) -> dict[str, object]:
    missing = [key for key in keys if key not in metadata]
    if missing:
        raise ValueError("Audit payload is missing required metadata")
    return {key: metadata[key] for key in keys}


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
