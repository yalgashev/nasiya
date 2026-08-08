from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtAcceptedAuditPayload,
    DebtCancelledAuditPayload,
    DebtCreatedAuditPayload,
    DebtExpiredAuditPayload,
    DebtRejectedAuditPayload,
)
from app.audit.redaction import redact_audit_payload
from app.auth.error_codes import ERROR_CATALOG, ErrorCode
from app.debt.enums import DebtExpirySource
from app.debt.values import DiscountBasisPoints, DiscountedAmountUZS, OriginalAmountUZS
from app.offers.enums import OfferLanguage


def test_exact_five_debt_audit_payloads_are_identifier_and_reason_safe() -> None:
    created = DebtCreatedAuditPayload(
        original_amount=OriginalAmountUZS(Decimal("1000")),
        discount_basis_points=DiscountBasisPoints(100),
        discounted_amount=DiscountedAmountUZS(Decimal("990")),
        due_date=date(2026, 5, 4),
        pending_expires_at=datetime(2026, 5, 4, tzinfo=UTC),
    )
    accepted = DebtAcceptedAuditPayload(3, OfferLanguage.UZ_LATN, "a" * 64)
    rejected = DebtRejectedAuditPayload(reason_provided=False)
    cancelled = DebtCancelledAuditPayload()
    expired = DebtExpiredAuditPayload(DebtExpirySource.INLINE)

    assert created.as_candidate_metadata().keys() == {
        "original_amount_uzs",
        "discount_basis_points",
        "discounted_amount_uzs",
        "due_date",
        "pending_expires_at",
    }
    assert accepted.as_candidate_metadata().keys() == {
        "offer_version_number",
        "language",
        "content_hash",
    }
    assert rejected.as_candidate_metadata() == {"reason_provided": False}
    assert cancelled.as_candidate_metadata() == {"reason_provided": True}
    assert expired.as_candidate_metadata() == {"source": "inline"}
    with pytest.raises(ValueError, match="requires a reason"):
        DebtCancelledAuditPayload(reason_provided=False)

    cases = (
        (AuditEventType.DEBT_CREATED, created),
        (AuditEventType.DEBT_ACCEPTED, accepted),
        (AuditEventType.DEBT_REJECTED, rejected),
        (AuditEventType.DEBT_CANCELLED, cancelled),
        (AuditEventType.DEBT_EXPIRED, expired),
    )
    forbidden = {
        "reason": "private reason",
        "phone": "+998901234567",
        "raw_form": "private form",
    }
    for event_type, payload in cases:
        actor_kind = (
            AuditActorKind.SYSTEM
            if event_type is AuditEventType.DEBT_EXPIRED
            else AuditActorKind.USER
        )
        actor_user_id = None if actor_kind is AuditActorKind.SYSTEM else uuid4()
        metadata = {**payload.as_candidate_metadata(), **forbidden}
        redacted = redact_audit_payload(
            AuditEvent(
                event_type=event_type,
                actor_kind=actor_kind,
                actor_user_id=actor_user_id,
                object_type=AuditObjectType.DEBT,
                object_id=uuid4(),
                occurred_at=datetime(2026, 5, 4, tzinfo=UTC),
                candidate_metadata=metadata,
            )
        )
        assert redacted == payload.as_candidate_metadata()
        assert set(redacted).isdisjoint(forbidden)


def test_debt_expired_is_only_new_system_audit_event_and_errors_are_catalogued() -> (
    None
):
    event = AuditEvent(
        event_type=AuditEventType.DEBT_EXPIRED,
        actor_kind=AuditActorKind.SYSTEM,
        actor_user_id=None,
        object_type=AuditObjectType.DEBT,
        object_id=uuid4(),
        occurred_at=datetime(2026, 5, 4, tzinfo=UTC),
        candidate_metadata=DebtExpiredAuditPayload(
            DebtExpirySource.BATCH
        ).as_candidate_metadata(),
    )
    assert event.object_type is AuditObjectType.DEBT
    with pytest.raises(ValueError, match="Debt expiry audit actor"):
        AuditEvent(
            event_type=AuditEventType.DEBT_EXPIRED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=uuid4(),
            object_type=AuditObjectType.DEBT,
            object_id=uuid4(),
            occurred_at=datetime(2026, 5, 4, tzinfo=UTC),
            candidate_metadata={},
        )
    assert {
        ErrorCode.CUSTOMER_NOT_ACTIVE,
        ErrorCode.CUSTOMER_BLACKLISTED,
        ErrorCode.CUSTOMER_RATING_BLOCKED,
        ErrorCode.CREDIT_LIMIT_EXCEEDED,
        ErrorCode.MAX_OPEN_DEBTS,
        ErrorCode.DEBT_UNAVAILABLE,
        ErrorCode.DEBT_NOT_PENDING,
        ErrorCode.DEBT_EXPIRED,
        ErrorCode.IDEMPOTENCY_CONFLICT,
    }.issubset(ERROR_CATALOG)
