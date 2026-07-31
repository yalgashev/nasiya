from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
)
from app.audit.redaction import redact_audit_payload
from app.offers.enums import OfferLanguage, OfferPurpose, OfferStatus

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
OBJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
VERSION_ID = UUID("33333333-3333-4333-8333-333333333333")
TEXT_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 7, 31, 13, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)

_OBJECT_BY_EVENT = {
    AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED: AuditObjectType.USER,
    AuditEventType.OFFER_VERSION_CREATED: AuditObjectType.OFFER_VERSION,
    AuditEventType.OFFER_TEXT_UPDATED: AuditObjectType.OFFER_TEXT,
    AuditEventType.OFFER_VERSION_APPROVED: AuditObjectType.OFFER_VERSION,
    AuditEventType.OFFER_VERSION_MADE_CURRENT: AuditObjectType.OFFER_VERSION,
    AuditEventType.OFFER_VERSION_DEMOTED: AuditObjectType.OFFER_VERSION,
    AuditEventType.OFFER_REGISTRATION_ACCEPTED: AuditObjectType.OFFER_ACCEPTANCE,
}


def _event(
    event_type: AuditEventType,
    metadata: dict[str, object],
) -> AuditEvent:
    is_bootstrap = event_type is AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED
    return AuditEvent(
        event_type=event_type,
        actor_kind=(AuditActorKind.SYSTEM if is_bootstrap else AuditActorKind.USER),
        actor_user_id=None if is_bootstrap else ACTOR_ID,
        object_type=_OBJECT_BY_EVENT[event_type],
        object_id=OBJECT_ID,
        occurred_at=NOW,
        candidate_metadata=metadata,
    )


@pytest.mark.parametrize(
    ("event_type", "metadata", "expected"),
    [
        (
            AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
            {"bootstrap_method": "operator_cli"},
            {"bootstrap_method": "operator_cli"},
        ),
        (
            AuditEventType.OFFER_VERSION_CREATED,
            {
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 1,
                "status": OfferStatus.DRAFT,
            },
            {
                "purpose": "REGISTRATION",
                "version_number": 1,
                "status": "DRAFT",
            },
        ),
        (
            AuditEventType.OFFER_TEXT_UPDATED,
            {
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 2,
                "language": OfferLanguage.UZ_CYRL,
                "content_hash": "a" * 64,
            },
            {
                "purpose": "REGISTRATION",
                "version_number": 2,
                "language": "UZ_CYRL",
                "content_hash": "a" * 64,
            },
        ),
        (
            AuditEventType.OFFER_VERSION_APPROVED,
            {
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 3,
                "from_status": OfferStatus.DRAFT,
                "to_status": OfferStatus.APPROVED,
                "legal_review_authority": " Nasiya Legal ",
                "legal_review_reference": "LEGAL-2026-001",
                "legal_reviewed_at": REVIEWED_AT,
            },
            {
                "purpose": "REGISTRATION",
                "version_number": 3,
                "from_status": "DRAFT",
                "to_status": "APPROVED",
                "legal_review_authority": "Nasiya Legal",
                "legal_review_reference": "LEGAL-2026-001",
                "legal_reviewed_at": "2026-07-31T12:00:00+00:00",
            },
        ),
        (
            AuditEventType.OFFER_VERSION_MADE_CURRENT,
            {
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 4,
                "from_status": OfferStatus.APPROVED,
                "to_status": OfferStatus.CURRENT,
                "previous_current_version_id": None,
            },
            {
                "purpose": "REGISTRATION",
                "version_number": 4,
                "from_status": "APPROVED",
                "to_status": "CURRENT",
                "previous_current_version_id": None,
            },
        ),
        (
            AuditEventType.OFFER_VERSION_DEMOTED,
            {
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 4,
                "from_status": OfferStatus.CURRENT,
                "to_status": OfferStatus.APPROVED,
                "replacement_version_id": VERSION_ID,
            },
            {
                "purpose": "REGISTRATION",
                "version_number": 4,
                "from_status": "CURRENT",
                "to_status": "APPROVED",
                "replacement_version_id": str(VERSION_ID),
            },
        ),
        (
            AuditEventType.OFFER_REGISTRATION_ACCEPTED,
            {
                "purpose": OfferPurpose.REGISTRATION,
                "offer_version_id": VERSION_ID,
                "offer_text_id": TEXT_ID,
                "version_number": 4,
                "language": OfferLanguage.RU,
                "content_hash": "b" * 64,
            },
            {
                "purpose": "REGISTRATION",
                "offer_version_id": str(VERSION_ID),
                "offer_text_id": str(TEXT_ID),
                "version_number": 4,
                "language": "RU",
                "content_hash": "b" * 64,
            },
        ),
    ],
)
def test_redaction_emits_exact_event_specific_payload(
    event_type: AuditEventType,
    metadata: dict[str, object],
    expected: dict[str, str | int | None],
) -> None:
    assert redact_audit_payload(_event(event_type, metadata)) == expected


def test_redaction_drops_every_unknown_sensitive_key() -> None:
    forbidden = {
        "title": "SECRET TITLE",
        "body": "SECRET BODY",
        "user_agent": "SECRET UA",
        "phone": "+998901234567",
        "jshshir": "12345678901234",
        "token": "SECRET TOKEN",
        "session_id": "SECRET SESSION",
        "csrf": "SECRET CSRF",
        "url": "https://secret.example",
        "object_key": "secret/object",
        "raw_form": "SECRET FORM",
        "exception": "SECRET EXCEPTION",
        "sql": "SELECT SECRET",
    }
    metadata: dict[str, object] = {
        "purpose": OfferPurpose.REGISTRATION,
        "version_number": 1,
        "status": OfferStatus.DRAFT,
        **forbidden,
    }

    payload = redact_audit_payload(
        _event(AuditEventType.OFFER_VERSION_CREATED, metadata)
    )

    assert payload == {
        "purpose": "REGISTRATION",
        "version_number": 1,
        "status": "DRAFT",
    }
    assert set(forbidden).isdisjoint(payload)


@pytest.mark.parametrize(
    ("event_type", "metadata", "message"),
    [
        (
            AuditEventType.OFFER_VERSION_CREATED,
            {"purpose": OfferPurpose.REGISTRATION},
            "missing required metadata",
        ),
        (
            AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
            {"bootstrap_method": "web"},
            "bootstrap method is invalid",
        ),
        (
            AuditEventType.OFFER_TEXT_UPDATED,
            {
                "purpose": OfferPurpose.REGISTRATION,
                "version_number": 1,
                "language": OfferLanguage.RU,
                "content_hash": "A" * 64,
            },
            "content hash is invalid",
        ),
        (
            AuditEventType.OFFER_REGISTRATION_ACCEPTED,
            {
                "purpose": OfferPurpose.DEBT_ACCEPTANCE,
                "offer_version_id": VERSION_ID,
                "offer_text_id": TEXT_ID,
                "version_number": 1,
                "language": OfferLanguage.RU,
                "content_hash": "a" * 64,
            },
            "purpose must be REGISTRATION",
        ),
    ],
)
def test_redaction_fails_closed_for_missing_or_invalid_required_values(
    event_type: AuditEventType,
    metadata: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        redact_audit_payload(_event(event_type, metadata))
