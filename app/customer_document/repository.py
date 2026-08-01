from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.customer_document.contracts import (
    CustomerDocumentAccessParentRequest,
    CustomerDocumentAttachment,
    CustomerDocumentAttachmentResult,
    CustomerDocumentRepository,
    CustomerDocumentStatus,
    CustomerDocumentSubmissionId,
    ExpectedCurrentCustomerDocument,
    UnattachedObjectCompensationStatus,
)
from app.customer_document.models import CustomerDocument
from app.storage.models import ObjectFileStatus
from app.storage.repository import (
    load_object_file_for_update,
    mark_object_file_delete_pending,
)

_OBJECT_CONSTRAINT = "uq_customer_documents_object_file_id"
_SUBMISSION_CONSTRAINT = "uq_customer_documents_customer_id_submission_id"
_CURRENT_CONSTRAINT = "uq_customer_documents_current_customer_id"


class CustomerDocumentPersistenceConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Customer document state changed")


class SqlAlchemyCustomerDocumentRepository(CustomerDocumentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_current_documents(
        self,
        *,
        customer_id: UUID,
    ) -> tuple[CustomerDocumentAttachment, ...]:
        statement = (
            select(CustomerDocument)
            .where(
                CustomerDocument.customer_id == customer_id,
                CustomerDocument.status == CustomerDocumentStatus.CURRENT.value,
            )
            .order_by(CustomerDocument.id)
            .with_for_update()
        )
        return tuple(
            _to_attachment(model) for model in self._session.scalars(statement)
        )

    def load_submission_replay(
        self,
        *,
        customer_id: UUID,
        submission_id: CustomerDocumentSubmissionId,
    ) -> CustomerDocumentAttachment | None:
        if not isinstance(submission_id, CustomerDocumentSubmissionId):
            raise TypeError("Customer document submission id is invalid")
        statement = select(CustomerDocument).where(
            CustomerDocument.customer_id == customer_id,
            CustomerDocument.submission_id == submission_id.value,
        )
        model = self._session.scalar(statement)
        return None if model is None else _to_attachment(model)

    def load_object_attachment(
        self,
        *,
        object_file_id: UUID,
    ) -> CustomerDocumentAttachment | None:
        statement = select(CustomerDocument).where(
            CustomerDocument.object_file_id == object_file_id,
        )
        model = self._session.scalar(statement)
        return None if model is None else _to_attachment(model)

    def attach_current_document(
        self,
        *,
        attachment: CustomerDocumentAttachment,
        expected_current: ExpectedCurrentCustomerDocument,
    ) -> CustomerDocumentAttachmentResult:
        if not isinstance(attachment, CustomerDocumentAttachment):
            raise TypeError("Customer document attachment is invalid")
        if attachment.status is not CustomerDocumentStatus.CURRENT:
            raise ValueError("New customer document must be current")
        if not isinstance(expected_current, ExpectedCurrentCustomerDocument):
            raise TypeError("Expected current customer document is invalid")

        current_rows = self._lock_current_models(attachment.customer_id)
        if len(current_rows) > 1:
            raise CustomerDocumentPersistenceConflict() from None

        replay = self.load_submission_replay(
            customer_id=attachment.customer_id,
            submission_id=attachment.submission_id,
        )
        if replay is not None:
            return CustomerDocumentAttachmentResult(
                document_id=replay.id,
                status=replay.status,
                submission_replayed=True,
            )
        if (
            self.load_object_attachment(object_file_id=attachment.object_file_id)
            is not None
        ):
            raise CustomerDocumentPersistenceConflict() from None

        current = current_rows[0] if current_rows else None
        current_id = current.id if current is not None else None
        if current_id != expected_current.document_id:
            raise CustomerDocumentPersistenceConflict() from None

        new_model = _to_model(attachment)
        try:
            with self._session.begin_nested():
                if current is not None:
                    current.status = CustomerDocumentStatus.SUPERSEDED.value
                    current.superseded_by_document_id = attachment.id
                    current.superseded_at = attachment.attached_at
                self._session.add(new_model)
                self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) in {
                _OBJECT_CONSTRAINT,
                _SUBMISSION_CONSTRAINT,
                _CURRENT_CONSTRAINT,
            }:
                raise CustomerDocumentPersistenceConflict() from None
            raise
        return CustomerDocumentAttachmentResult(
            document_id=attachment.id,
            status=CustomerDocumentStatus.CURRENT,
            submission_replayed=False,
            superseded_document_id=current_id,
        )

    def resolve_access_parent(
        self,
        *,
        customer_id: UUID,
    ) -> CustomerDocumentAccessParentRequest | None:
        statement = select(CustomerDocument).where(
            CustomerDocument.customer_id == customer_id,
            CustomerDocument.status == CustomerDocumentStatus.CURRENT.value,
        )
        documents = tuple(self._session.scalars(statement))
        if len(documents) != 1:
            return None
        document = documents[0]
        return CustomerDocumentAccessParentRequest(
            customer_id=customer_id,
            document_id=document.id,
            object_file_id=document.object_file_id,
        )

    def _lock_current_models(
        self,
        customer_id: UUID,
    ) -> tuple[CustomerDocument, ...]:
        statement = (
            select(CustomerDocument)
            .where(
                CustomerDocument.customer_id == customer_id,
                CustomerDocument.status == CustomerDocumentStatus.CURRENT.value,
            )
            .order_by(CustomerDocument.id)
            .with_for_update()
        )
        return tuple(self._session.scalars(statement))


def _to_model(attachment: CustomerDocumentAttachment) -> CustomerDocument:
    return CustomerDocument(
        id=attachment.id,
        customer_id=attachment.customer_id,
        object_file_id=attachment.object_file_id,
        submission_id=attachment.submission_id.value,
        status=attachment.status.value,
        attached_by_user_id=attachment.attached_by_user_id,
        attached_at=attachment.attached_at,
        superseded_by_document_id=attachment.superseded_by_document_id,
        superseded_at=attachment.superseded_at,
    )


def _to_attachment(model: CustomerDocument) -> CustomerDocumentAttachment:
    return CustomerDocumentAttachment(
        id=model.id,
        customer_id=model.customer_id,
        object_file_id=model.object_file_id,
        submission_id=CustomerDocumentSubmissionId(model.submission_id),
        status=CustomerDocumentStatus(model.status),
        attached_by_user_id=model.attached_by_user_id,
        attached_at=model.attached_at,
        superseded_by_document_id=model.superseded_by_document_id,
        superseded_at=model.superseded_at,
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(exc.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def claim_unattached_object_for_compensation(
    session: Session,
    *,
    object_file_id: UUID,
    now: datetime,
) -> UnattachedObjectCompensationStatus:
    """Claim a detached AVAILABLE object for existing M8 reconciliation."""
    object_file = load_object_file_for_update(
        session,
        object_file_id=object_file_id,
    )
    if object_file is None or object_file.status != ObjectFileStatus.AVAILABLE.value:
        return UnattachedObjectCompensationStatus.NOOP
    attachment_id = session.scalar(
        select(CustomerDocument.id).where(
            CustomerDocument.object_file_id == object_file_id
        )
    )
    if attachment_id is not None:
        return UnattachedObjectCompensationStatus.NOOP
    mark_object_file_delete_pending(
        session,
        object_file_id=object_file_id,
        failure_code=None,
        now=now,
    )
    return UnattachedObjectCompensationStatus.CLAIMED
