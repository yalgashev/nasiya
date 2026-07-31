from sqlalchemy.orm import Session

from app.audit.contracts import AuditEvent
from app.audit.models import AuditLog
from app.audit.redaction import redact_audit_payload

__all__ = ["SqlAlchemyAuditWriter", "append_audit_event"]


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
