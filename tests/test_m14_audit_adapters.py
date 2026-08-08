from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtPaidAuditPayload,
    PaymentRecordedAuditPayload,
)
from app.audit.redaction import redact_audit_payload
from app.audit.repository import (
    append_debt_paid_audit,
    append_payment_recorded_audit,
)
from app.debt.enums import DebtStatus
from app.debt.values import DebtRevision
from app.payment.enums import PaymentMethod
from app.payment.values import PaymentAmountUZS

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
ACTOR_ID = UUID("11111111-1111-4111-8111-111111111111")
PAYMENT_ID = UUID("22222222-2222-4222-8222-222222222222")
DEBT_ID = UUID("33333333-3333-4333-8333-333333333333")


def _payment_payload() -> PaymentRecordedAuditPayload:
    return PaymentRecordedAuditPayload(
        amount=PaymentAmountUZS(Decimal("250000")),
        method=PaymentMethod.TRANSFER,
        from_status=DebtStatus.ACTIVE,
        to_status=DebtStatus.PAID,
        debt_revision_after=DebtRevision(4),
    )


def _event(
    *,
    event_type: AuditEventType,
    object_type: AuditObjectType,
    object_id: UUID,
    metadata: dict[str, object],
) -> AuditEvent:
    return AuditEvent(
        event_type=event_type,
        actor_kind=AuditActorKind.USER,
        actor_user_id=ACTOR_ID,
        object_type=object_type,
        object_id=object_id,
        occurred_at=NOW,
        candidate_metadata=metadata,
    )


def test_m14_redaction_emits_only_the_two_exact_allow_list_payloads() -> None:
    payment = redact_audit_payload(
        _event(
            event_type=AuditEventType.PAYMENT_RECORDED,
            object_type=AuditObjectType.PAYMENT,
            object_id=PAYMENT_ID,
            metadata=dict(_payment_payload().as_candidate_metadata()),
        )
    )
    debt_paid = redact_audit_payload(
        _event(
            event_type=AuditEventType.DEBT_PAID,
            object_type=AuditObjectType.DEBT,
            object_id=DEBT_ID,
            metadata=dict(
                DebtPaidAuditPayload(
                    debt_revision_after=DebtRevision(4)
                ).as_candidate_metadata()
            ),
        )
    )

    assert payment == {
        "amount_uzs": 250_000,
        "method": "transfer",
        "from_status": "active",
        "to_status": "paid",
        "debt_revision_after": 4,
    }
    assert debt_paid == {"source": "payment", "debt_revision_after": 4}
    assert str(PAYMENT_ID) not in repr(payment)
    assert str(DEBT_ID) not in repr(debt_paid)


@pytest.mark.parametrize(
    ("event_type", "object_type", "object_id", "metadata"),
    (
        (
            AuditEventType.PAYMENT_RECORDED,
            AuditObjectType.PAYMENT,
            PAYMENT_ID,
            {
                **dict(_payment_payload().as_candidate_metadata()),
                "idempotency_key": "private-key",
                "request_hash": "a" * 64,
                "phone": "+998901234567",
                "staff_role": "cashier",
            },
        ),
        (
            AuditEventType.DEBT_PAID,
            AuditObjectType.DEBT,
            DEBT_ID,
            {
                "source": "payment",
                "debt_revision_after": 4,
                "payment_id": str(PAYMENT_ID),
            },
        ),
    ),
)
def test_m14_redaction_rejects_unknown_and_sensitive_payload_fields(
    event_type: AuditEventType,
    object_type: AuditObjectType,
    object_id: UUID,
    metadata: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="unexpected metadata"):
        redact_audit_payload(
            _event(
                event_type=event_type,
                object_type=object_type,
                object_id=object_id,
                metadata=metadata,
            )
        )


@pytest.mark.parametrize(
    ("metadata", "message"),
    (
        (
            {
                "amount_uzs": 0,
                "method": "cash",
                "from_status": "active",
                "to_status": "active",
                "debt_revision_after": 1,
            },
            "amount",
        ),
        (
            {
                "amount_uzs": 1,
                "method": "crypto",
                "from_status": "active",
                "to_status": "active",
                "debt_revision_after": 1,
            },
            "method",
        ),
        (
            {
                "amount_uzs": 1,
                "method": "cash",
                "from_status": "pending",
                "to_status": "active",
                "debt_revision_after": 1,
            },
            "source status",
        ),
        (
            {
                "amount_uzs": 1,
                "method": "cash",
                "from_status": "active",
                "to_status": "cancelled",
                "debt_revision_after": 1,
            },
            "target status",
        ),
    ),
)
def test_payment_audit_redaction_rejects_invalid_frozen_values(
    metadata: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        redact_audit_payload(
            _event(
                event_type=AuditEventType.PAYMENT_RECORDED,
                object_type=AuditObjectType.PAYMENT,
                object_id=PAYMENT_ID,
                metadata=metadata,
            )
        )


def test_m14_audit_adapters_use_dedicated_object_id_and_borrowed_session() -> None:
    session = _RecordingSession()

    append_payment_recorded_audit(
        session,  # type: ignore[arg-type]
        payment_id=PAYMENT_ID,
        actor_user_id=ACTOR_ID,
        occurred_at=NOW,
        payload=_payment_payload(),
    )
    append_debt_paid_audit(
        session,  # type: ignore[arg-type]
        debt_id=DEBT_ID,
        actor_user_id=ACTOR_ID,
        occurred_at=NOW,
        payload=DebtPaidAuditPayload(debt_revision_after=DebtRevision(4)),
    )

    assert session.flushes == 2
    assert [
        (item.event_type, item.object_type, item.object_id) for item in session.added
    ] == [
        ("payment.recorded", "payment", PAYMENT_ID),
        ("debt.paid", "debt", DEBT_ID),
    ]
    assert session.added[0].payload == {
        "amount_uzs": 250_000,
        "method": "transfer",
        "from_status": "active",
        "to_status": "paid",
        "debt_revision_after": 4,
    }
    assert session.added[1].payload == {"source": "payment", "debt_revision_after": 4}


def test_m14_audit_adapters_reject_wrong_identifier_or_payload_before_write() -> None:
    session = _RecordingSession()

    with pytest.raises(ValueError, match="identifiers"):
        append_payment_recorded_audit(
            session,  # type: ignore[arg-type]
            payment_id="not-a-uuid",  # type: ignore[arg-type]
            actor_user_id=ACTOR_ID,
            occurred_at=NOW,
            payload=_payment_payload(),
        )
    with pytest.raises(ValueError, match="payload"):
        append_debt_paid_audit(
            session,  # type: ignore[arg-type]
            debt_id=DEBT_ID,
            actor_user_id=ACTOR_ID,
            occurred_at=NOW,
            payload=_payment_payload(),  # type: ignore[arg-type]
        )

    assert session.added == []
    assert session.flushes == 0


def test_m14_audit_contract_rejects_unknown_event_and_wrong_object() -> None:
    with pytest.raises(ValueError, match="event type is invalid"):
        AuditEvent(
            event_type="payment.voided",  # type: ignore[arg-type]
            actor_kind=AuditActorKind.USER,
            actor_user_id=ACTOR_ID,
            object_type=AuditObjectType.PAYMENT,
            object_id=PAYMENT_ID,
            occurred_at=NOW,
            candidate_metadata={},
        )
    with pytest.raises(ValueError, match="event object type is invalid"):
        _event(
            event_type=AuditEventType.PAYMENT_RECORDED,
            object_type=AuditObjectType.DEBT,
            object_id=DEBT_ID,
            metadata=dict(_payment_payload().as_candidate_metadata()),
        )


class _RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushes = 0

    def add(self, item: object) -> None:
        self.added.append(item)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        raise AssertionError("Audit adapter must not commit a borrowed session")
