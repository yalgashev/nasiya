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
from app.customer_document.contracts import CustomerDocumentStatus
from app.customer_identity.contracts import CustomerDocumentType

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
OBJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
REPLACEMENT_ID = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)

_OBJECT_BY_EVENT = {
    AuditEventType.CUSTOMER_IDENTITY_SAVED: AuditObjectType.CUSTOMER_IDENTITY,
    AuditEventType.CUSTOMER_DOCUMENT_ATTACHED: AuditObjectType.CUSTOMER_DOCUMENT,
    AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED: AuditObjectType.CUSTOMER_DOCUMENT,
    AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED: (
        AuditObjectType.CUSTOMER_DOCUMENT
    ),
}


def _event(event_type: AuditEventType, metadata: dict[str, object]) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        actor_kind=AuditActorKind.USER,
        actor_user_id=ACTOR_ID,
        object_type=_OBJECT_BY_EVENT[event_type],
        object_id=OBJECT_ID,
        occurred_at=NOW,
        candidate_metadata=metadata,
    )


@pytest.mark.parametrize(
    ("event_type", "metadata", "expected"),
    [
        (
            AuditEventType.CUSTOMER_IDENTITY_SAVED,
            {
                "revision": 2,
                "created_or_updated": "updated",
                "document_type": CustomerDocumentType.ID_CARD,
            },
            {
                "revision": 2,
                "created_or_updated": "updated",
                "document_type": "ID_CARD",
            },
        ),
        (
            AuditEventType.CUSTOMER_DOCUMENT_ATTACHED,
            {
                "status": CustomerDocumentStatus.CURRENT,
                "submission_replayed": False,
            },
            {"status": "CURRENT", "submission_replayed": False},
        ),
        (
            AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED,
            {"replacement_document_id": REPLACEMENT_ID},
            {"replacement_document_id": str(REPLACEMENT_ID)},
        ),
        (
            AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED,
            {"ttl_seconds": 300},
            {"ttl_seconds": 300},
        ),
    ],
)
def test_m10_audit_payloads_are_exact_and_safe(
    event_type: AuditEventType,
    metadata: dict[str, object],
    expected: dict[str, object],
) -> None:
    metadata.update(
        {
            "first_name": "SENSITIVE NAME",
            "jshshir": "12345678901234",
            "document_number": "AA12345",
            "ciphertext": b"ciphertext",
            "nonce": b"nonce",
            "key_id": "key-id",
            "object_file_id": UUID(int=9),
            "presigned_url": "https://secret.invalid",
        }
    )
    payload = redact_audit_payload(_event(event_type, metadata))

    assert payload == expected
    assert (
        not {
            "first_name",
            "jshshir",
            "document_number",
            "ciphertext",
            "nonce",
            "key_id",
            "object_file_id",
            "presigned_url",
        }
        & payload.keys()
    )


@pytest.mark.parametrize(
    ("event_type", "metadata"),
    [
        (
            AuditEventType.CUSTOMER_IDENTITY_SAVED,
            {
                "revision": 0,
                "created_or_updated": "created",
                "document_type": CustomerDocumentType.PASSPORT,
            },
        ),
        (
            AuditEventType.CUSTOMER_DOCUMENT_ATTACHED,
            {
                "status": CustomerDocumentStatus.CURRENT,
                "submission_replayed": True,
            },
        ),
        (
            AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED,
            {"replacement_document_id": "not-a-uuid"},
        ),
        (
            AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED,
            {"ttl_seconds": 901},
        ),
    ],
)
def test_m10_audit_payloads_fail_closed(
    event_type: AuditEventType,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        redact_audit_payload(_event(event_type, metadata))
