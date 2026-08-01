from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
)
from app.audit.repository import SqlAlchemyAuditWriter
from app.customer_document.authorization import (
    OwnCurrentCustomerDocumentAuthorizer,
    resolve_own_current_document_parent,
)
from app.customer_document.contracts import (
    CustomerDocumentActor,
    CustomerDocumentStatus,
)
from app.customer_document.models import CustomerDocument
from app.settings import Settings
from app.storage.contracts import (
    ObjectReadAuthorizationRequest,
    ObjectStorageService,
    PresignedObjectUrl,
)
from app.storage.errors import StorageAccessDeniedError
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.service import create_authorized_presigned_get_url

_ALLOWED_DOCUMENT_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def has_current_customer_identity_document(
    session: Session,
    *,
    customer_id: UUID,
) -> bool:
    if not isinstance(customer_id, UUID):
        return False
    statement = (
        select(CustomerDocument.id)
        .join(ObjectFile, ObjectFile.id == CustomerDocument.object_file_id)
        .where(
            CustomerDocument.customer_id == customer_id,
            CustomerDocument.status == CustomerDocumentStatus.CURRENT.value,
            ObjectFile.status == ObjectFileStatus.AVAILABLE.value,
            ObjectFile.content_type.in_(_ALLOWED_DOCUMENT_CONTENT_TYPES),
        )
    )
    return len(tuple(session.scalars(statement))) == 1


class CustomerDocumentCompletenessService:
    def __init__(self, *, session: Session) -> None:
        self._session = session

    def __call__(self, *, customer_id: UUID) -> bool:
        return has_current_customer_identity_document(
            self._session,
            customer_id=customer_id,
        )


def create_own_current_customer_document_url(
    session_factory: sessionmaker[Session],
    *,
    actor: CustomerDocumentActor,
    storage: ObjectStorageService,
    settings: Settings,
    now: datetime,
) -> PresignedObjectUrl:
    """Authorize own current parent, presign with M8, then audit safely."""
    if not isinstance(actor, CustomerDocumentActor):
        raise TypeError("Customer document actor is invalid")
    current_time = _as_utc(now)
    authorizer = OwnCurrentCustomerDocumentAuthorizer(
        session_factory,
        actor=actor,
    )
    parent = authorizer.resolve_current_parent()
    if parent is None:
        raise StorageAccessDeniedError
    request = ObjectReadAuthorizationRequest(
        actor_user_id=actor.user_id,
        object_file_id=parent.object_file_id,
        domain_parent_reference=parent,
    )
    url = create_authorized_presigned_get_url(
        session_factory,
        request=request,
        authorizer=authorizer,
        storage=storage,
        settings=settings,
    )
    ttl_seconds = settings.require_object_storage_config().presigned_ttl_seconds
    try:
        with session_factory.begin() as session:
            current_parent = resolve_own_current_document_parent(
                session,
                actor=actor,
            )
            if current_parent != parent:
                raise StorageAccessDeniedError
            SqlAlchemyAuditWriter(session).append(
                event=AuditEvent(
                    event_type=AuditEventType.CUSTOMER_DOCUMENT_ACCESS_GRANTED,
                    actor_kind=AuditActorKind.USER,
                    actor_user_id=actor.user_id,
                    object_type=AuditObjectType.CUSTOMER_DOCUMENT,
                    object_id=parent.document_id,
                    occurred_at=current_time,
                    candidate_metadata={"ttl_seconds": ttl_seconds},
                )
            )
    except StorageAccessDeniedError:
        raise
    except SQLAlchemyError:
        raise StorageAccessDeniedError from None
    return url


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Customer document access timestamp must be timezone-aware")
    return value.astimezone(UTC)
