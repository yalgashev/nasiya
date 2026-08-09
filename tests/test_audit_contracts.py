from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import pytest

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtClawbackAppliedAuditPayload,
    DebtOverdueAuditPayload,
    DebtPaidAuditPayload,
    PaymentRecordedAuditPayload,
)
from app.debt.enums import DebtOverdueSource, DebtStatus
from app.debt.values import DebtRevision
from app.payment.enums import PaymentMethod
from app.payment.values import ClawbackIncreaseUZS, PaymentAmountUZS

ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
OBJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 7, 31, 13, 0, tzinfo=UTC)


def test_audit_contract_registries_include_exact_m12_extensions() -> None:
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
        "shop_customer.linked",
        "shop_customer.policy_updated",
        "shop.customer_defaults_updated",
        "debt.created",
        "debt.accepted",
        "debt.rejected",
        "debt.cancelled",
        "debt.expired",
        "debt.overdue",
        "debt.clawback_applied",
        "payment.recorded",
        "debt.paid",
    }
    assert {object_type.value for object_type in AuditObjectType} == {
        "user",
        "offer_version",
        "offer_text",
        "offer_acceptance",
        "customer_identity",
        "customer_document",
        "customer",
        "shop_customer",
        "shop",
        "debt",
        "payment",
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
    with pytest.raises(ValueError, match="Offer audit actor must be a user"):
        AuditEvent(
            event_type=AuditEventType.PAYMENT_RECORDED,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.PAYMENT,
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


def test_m14_payment_audit_payloads_are_closed_and_identifier_free() -> None:
    recorded = PaymentRecordedAuditPayload(
        amount=PaymentAmountUZS(Decimal("150000")),
        method=PaymentMethod.CARD,
        from_status=DebtStatus.ACTIVE,
        to_status=DebtStatus.PAID,
        debt_revision_after=DebtRevision(8),
    )
    paid = DebtPaidAuditPayload(debt_revision_after=DebtRevision(8))

    assert dict(recorded.as_candidate_metadata()) == {
        "amount_uzs": 150_000,
        "method": "card",
        "from_status": "active",
        "to_status": "paid",
        "debt_revision_after": 8,
    }
    assert dict(paid.as_candidate_metadata()) == {
        "source": "payment",
        "debt_revision_after": 8,
    }
    assert set(recorded.as_candidate_metadata()).isdisjoint(
        {"payment_id", "debt_id", "shop_id", "customer_id", "user_id"}
    )
    with pytest.raises(TypeError):
        recorded.as_candidate_metadata()["payment_id"] = "forbidden"


@pytest.mark.parametrize(
    ("to_status", "expected_to_status"),
    (
        (DebtStatus.OVERDUE, "overdue"),
        (DebtStatus.PAID, "paid"),
    ),
)
def test_m15_overdue_payment_audit_payload_has_exact_lawful_pairs(
    to_status: DebtStatus, expected_to_status: str
) -> None:
    payload = PaymentRecordedAuditPayload(
        amount=PaymentAmountUZS(Decimal("1")),
        method=PaymentMethod.CASH,
        from_status=DebtStatus.OVERDUE,
        to_status=to_status,
        debt_revision_after=DebtRevision(4),
    )

    assert dict(payload.as_candidate_metadata()) == {
        "amount_uzs": 1,
        "method": "cash",
        "from_status": "overdue",
        "to_status": expected_to_status,
        "debt_revision_after": 4,
    }


def test_m14_payment_audit_events_require_user_and_exact_object_type() -> None:
    event = AuditEvent(
        event_type=AuditEventType.PAYMENT_RECORDED,
        actor_kind=AuditActorKind.USER,
        actor_user_id=ACTOR_ID,
        object_type=AuditObjectType.PAYMENT,
        object_id=OBJECT_ID,
        occurred_at=NOW,
        candidate_metadata=PaymentRecordedAuditPayload(
            amount=PaymentAmountUZS(Decimal("1")),
            method=PaymentMethod.CASH,
            from_status=DebtStatus.ACTIVE,
            to_status=DebtStatus.ACTIVE,
            debt_revision_after=DebtRevision(2),
        ).as_candidate_metadata(),
    )

    assert event.object_type is AuditObjectType.PAYMENT
    with pytest.raises(ValueError, match="event object type is invalid"):
        AuditEvent(
            event_type=AuditEventType.DEBT_PAID,
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR_ID,
            object_type=AuditObjectType.PAYMENT,
            object_id=OBJECT_ID,
            occurred_at=NOW,
            candidate_metadata={},
        )


def test_m15_overdue_audit_payloads_are_closed_identifier_free_and_redacted() -> None:
    overdue = DebtOverdueAuditPayload(
        source=DebtOverdueSource.INLINE_PAYMENT,
        overdue_revision=DebtRevision(7),
        business_date=NOW.date(),
    )
    clawback = DebtClawbackAppliedAuditPayload(
        source=DebtOverdueSource.BATCH,
        balance_increase_uzs=ClawbackIncreaseUZS(Decimal("25000")),
        overdue_revision=DebtRevision(7),
    )

    assert dict(overdue.as_candidate_metadata()) == {
        "source": "inline_payment",
        "from_status": "active",
        "to_status": "overdue",
        "overdue_revision": 7,
        "business_date": NOW.date().isoformat(),
    }
    assert dict(clawback.as_candidate_metadata()) == {
        "source": "batch",
        "from_basis": "discounted",
        "to_basis": "original",
        "balance_increase_uzs": 25_000,
        "overdue_revision": 7,
    }
    assert "25000" not in repr(clawback)
    assert "inline_payment" not in repr(overdue)
    with pytest.raises(TypeError):
        overdue.as_candidate_metadata()["debt_id"] = "forbidden"


def test_m15_system_audits_require_system_actor_and_debt_object() -> None:
    for event_type, payload in (
        (
            AuditEventType.DEBT_OVERDUE,
            DebtOverdueAuditPayload(
                source=DebtOverdueSource.BATCH,
                overdue_revision=DebtRevision(4),
                business_date=NOW.date(),
            ).as_candidate_metadata(),
        ),
        (
            AuditEventType.DEBT_CLAWBACK_APPLIED,
            DebtClawbackAppliedAuditPayload(
                source=DebtOverdueSource.BATCH,
                balance_increase_uzs=ClawbackIncreaseUZS(Decimal("0")),
                overdue_revision=DebtRevision(4),
            ).as_candidate_metadata(),
        ),
    ):
        event = AuditEvent(
            event_type=event_type,
            actor_kind=AuditActorKind.SYSTEM,
            actor_user_id=None,
            object_type=AuditObjectType.DEBT,
            object_id=OBJECT_ID,
            occurred_at=NOW,
            candidate_metadata=payload,
        )
        assert event.actor_user_id is None
        with pytest.raises(ValueError, match="Debt overdue audit actor must be SYSTEM"):
            AuditEvent(
                event_type=event_type,
                actor_kind=AuditActorKind.USER,
                actor_user_id=ACTOR_ID,
                object_type=AuditObjectType.DEBT,
                object_id=OBJECT_ID,
                occurred_at=NOW,
                candidate_metadata=payload,
            )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"from_status": DebtStatus.PENDING},
        {"to_status": DebtStatus.CANCELLED},
        {
            "from_status": DebtStatus.OVERDUE,
            "to_status": DebtStatus.ACTIVE,
        },
    ],
)
def test_m14_payment_audit_payload_rejects_invalid_transition(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "amount": PaymentAmountUZS(Decimal("1")),
        "method": PaymentMethod.CASH,
        "from_status": DebtStatus.ACTIVE,
        "to_status": DebtStatus.ACTIVE,
        "debt_revision_after": DebtRevision(2),
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        PaymentRecordedAuditPayload(**values)  # type: ignore[arg-type]
