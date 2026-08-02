from datetime import UTC, datetime
from types import MappingProxyType
from uuid import UUID

import pytest

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
)

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
OBJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 7, 31, 13, 0, tzinfo=UTC)


def test_audit_registries_are_exactly_twelve_events_and_seven_objects() -> None:
    assert {event.value for event in AuditEventType} == {
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
    }
    assert {object_type.value for object_type in AuditObjectType} == {
        "user",
        "offer_version",
        "offer_text",
        "offer_acceptance",
        "customer_identity",
        "customer_document",
        "customer",
    }
    assert {kind.value for kind in AuditActorKind} == {"USER", "SYSTEM"}


def test_bootstrap_requires_system_actor_and_user_object() -> None:
    event = AuditEvent(
        event_type=AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
        actor_kind=AuditActorKind.SYSTEM,
        actor_user_id=None,
        object_type=AuditObjectType.USER,
        object_id=OBJECT_ID,
        occurred_at=NOW,
        candidate_metadata={"bootstrap_method": "operator_cli"},
    )

    assert event.actor_user_id is None
    assert event.object_type is AuditObjectType.USER

    with pytest.raises(ValueError, match="Bootstrap audit actor must be SYSTEM"):
        AuditEvent(
            event_type=AuditEventType.PLATFORM_ADMIN_BOOTSTRAPPED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR_ID,
            object_type=AuditObjectType.USER,
            object_id=OBJECT_ID,
            occurred_at=NOW,
            candidate_metadata={"bootstrap_method": "operator_cli"},
        )


def test_offer_events_require_user_actor_and_exact_object_type() -> None:
    with pytest.raises(ValueError, match="Offer audit actor must be a user"):
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_CREATED,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=OBJECT_ID,
            occurred_at=NOW,
            candidate_metadata={},
        )

    with pytest.raises(ValueError, match="event object type is invalid"):
        AuditEvent(
            event_type=AuditEventType.OFFER_TEXT_UPDATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR_ID,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=OBJECT_ID,
            occurred_at=NOW,
            candidate_metadata={},
        )


def test_audit_event_rejects_untyped_or_serialized_input() -> None:
    with pytest.raises(ValueError, match="Audit event type is invalid"):
        AuditEvent(
            event_type="offer.version_created",
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR_ID,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=OBJECT_ID,
            occurred_at=NOW,
            candidate_metadata={},
        )

    with pytest.raises(ValueError, match="metadata must be a mapping"):
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_CREATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR_ID,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=OBJECT_ID,
            occurred_at=NOW,
            candidate_metadata='{"purpose":"REGISTRATION"}',
        )


def test_audit_event_time_is_aware_and_metadata_is_immutable_copy() -> None:
    metadata: dict[str, object] = {"status": "DRAFT"}
    event = AuditEvent(
        event_type=AuditEventType.OFFER_VERSION_CREATED,
        actor_kind=AuditActorKind.USER,
        actor_user_id=ACTOR_ID,
        object_type=AuditObjectType.OFFER_VERSION,
        object_id=OBJECT_ID,
        occurred_at=NOW,
        candidate_metadata=metadata,
    )
    metadata["status"] = "MUTATED"

    assert isinstance(event.candidate_metadata, MappingProxyType)
    assert event.candidate_metadata["status"] == "DRAFT"
    with pytest.raises(TypeError):
        event.candidate_metadata["new"] = "value"

    with pytest.raises(ValueError, match="must be timezone-aware"):
        AuditEvent(
            event_type=AuditEventType.OFFER_VERSION_CREATED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR_ID,
            object_type=AuditObjectType.OFFER_VERSION,
            object_id=OBJECT_ID,
            occurred_at=NOW.replace(tzinfo=None),
            candidate_metadata={},
        )


def test_audit_event_repr_redacts_actor_and_candidate_metadata() -> None:
    event = AuditEvent(
        event_type=AuditEventType.OFFER_TEXT_UPDATED,
        actor_kind=AuditActorKind.USER,
        actor_user_id=ACTOR_ID,
        object_type=AuditObjectType.OFFER_TEXT,
        object_id=OBJECT_ID,
        occurred_at=NOW,
        candidate_metadata={"body": "SECRET LEGAL BODY"},
    )

    rendered = repr(event)

    assert str(ACTOR_ID) not in rendered
    assert str(OBJECT_ID) not in rendered
    assert "SECRET" not in rendered
    assert "candidate_metadata=<redacted>" in rendered
