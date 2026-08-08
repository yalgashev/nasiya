from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
    DebtPaidAuditPayload,
    PaymentRecordedAuditPayload,
)
from app.audit.models import AuditLog
from app.audit.redaction import redact_audit_payload

__all__ = [
    "SqlAlchemyAuditWriter",
    "append_audit_event",
]


class SqlAlchemyAuditWriter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, *, event: AuditEvent) -> None:
        append_audit_event(self._session, event)


def append_audit_event(session: Session, event: AuditEvent) -> None:
    payload = redact_audit_payload(event)
    model = AuditLog(
        occurred_at=event.occurred_at,
        event_type=event.event_type.value,
        actor_kind=event.actor_kind.value,
        actor_user_id=event.actor_user_id,
        object_type=event.object_type.value,
        object_id=event.object_id,
        payload=payload,
    )
    session.add(model)
    session.flush()


def append_payment_recorded_audit(
    session: Session,
    *,
    payment_id: UUID,
    actor_user_id: UUID,
    occurred_at: datetime,
    payload: PaymentRecordedAuditPayload,
) -> None:
    if not isinstance(payment_id, UUID) or not isinstance(actor_user_id, UUID):
        raise ValueError("Payment audit identifiers are invalid")
    if not isinstance(payload, PaymentRecordedAuditPayload):
        raise ValueError("Payment audit payload is invalid")
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.PAYMENT_RECORDED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_user_id,
            object_type=AuditObjectType.PAYMENT,
            object_id=payment_id,
            occurred_at=occurred_at,
            candidate_metadata=payload.as_candidate_metadata(),
        ),
    )


def append_debt_paid_audit(
    session: Session,
    *,
    debt_id: UUID,
    actor_user_id: UUID,
    occurred_at: datetime,
    payload: DebtPaidAuditPayload,
) -> None:
    if not isinstance(debt_id, UUID) or not isinstance(actor_user_id, UUID):
        raise ValueError("Debt paid audit identifiers are invalid")
    if not isinstance(payload, DebtPaidAuditPayload):
        raise ValueError("Debt paid audit payload is invalid")
    append_audit_event(
        session,
        AuditEvent(
            event_type=AuditEventType.DEBT_PAID,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_user_id,
            object_type=AuditObjectType.DEBT,
            object_id=debt_id,
            occurred_at=occurred_at,
            candidate_metadata=payload.as_candidate_metadata(),
        ),
    )
