from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.contracts import (
    AuditActorKind,
    AuditEvent,
    AuditEventType,
    AuditObjectType,
)
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.error_codes import ErrorCode
from app.customer.repository import load_existing_own_customer_draft_for_update
from app.customer_document.contracts import (
    CustomerDocumentAttachment,
    CustomerDocumentAttachmentResult,
    CustomerDocumentStatus,
    UploadOwnCustomerDocument,
)
from app.customer_document.repository import (
    CustomerDocumentPersistenceConflict,
    SqlAlchemyCustomerDocumentRepository,
    claim_unattached_object_for_compensation,
)
from app.settings import Settings
from app.storage.contracts import ObjectStorageService
from app.storage.errors import StorageUploadError
from app.storage.image import AsyncImageSource
from app.storage.models import ObjectFileStatus
from app.storage.rate_limit import check_storage_upload_rate_limit
from app.storage.repository import load_object_file_for_update
from app.storage.service import IngestedImageResult, ingest_sanitized_image
from app.telegram.client_ip import ResolvedClientIp


class CustomerDocumentServiceError(RuntimeError):
    def __init__(self, code: ErrorCode) -> None:
        if code not in {
            ErrorCode.CUSTOMER_DRAFT_REQUIRED,
            ErrorCode.CUSTOMER_DOCUMENT_CHANGED,
            ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE,
        }:
            raise ValueError("Unsupported customer document error code")
        self.code = code
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"CustomerDocumentServiceError(code={self.code.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class _DocumentPreflight:
    customer_id: UUID
    replay: CustomerDocumentAttachmentResult | None

    def __repr__(self) -> str:
        return "_DocumentPreflight(customer_id=<redacted>, replay=<redacted>)"


async def upload_and_attach_own_customer_document(
    session_factory: sessionmaker[Session],
    *,
    command: UploadOwnCustomerDocument,
    source: AsyncImageSource,
    client_ip: ResolvedClientIp,
    now: datetime,
    settings: Settings,
    storage: ObjectStorageService,
) -> CustomerDocumentAttachmentResult:
    """Compose M8 ingest with short CR-M10-01-owned database phases."""
    if not isinstance(command, UploadOwnCustomerDocument):
        raise TypeError("Customer document upload command is invalid")
    if not isinstance(client_ip, ResolvedClientIp):
        raise TypeError("Customer document client IP is invalid")
    if not isinstance(settings, Settings):
        raise TypeError("Customer document settings are invalid")
    if not isinstance(storage, ObjectStorageService):
        raise TypeError("Customer document storage provider is invalid")
    current_time = _as_utc(now)

    _check_upload_rate_limit(
        session_factory,
        actor_user_id=command.actor.user_id,
        client_ip=client_ip,
        now=current_time,
        settings=settings,
    )
    preflight = _preflight_attachment(session_factory, command=command)
    if preflight.replay is not None:
        return preflight.replay

    ingested = await ingest_sanitized_image(
        session_factory,
        source=source,
        actor_user_id=command.actor.user_id,
        client_ip=client_ip,
        now=current_time,
        settings=settings,
        storage=storage,
    )

    try:
        result = _attach_ingested_document(
            session_factory,
            command=command,
            expected_customer_id=preflight.customer_id,
            ingested=ingested,
            now=current_time,
        )
    except Exception:
        _claim_compensation_preserving_failure(
            session_factory,
            object_file_id=ingested.object_file_id,
            now=current_time,
        )
        raise

    if result.submission_replayed:
        try:
            _claim_compensation(
                session_factory,
                object_file_id=ingested.object_file_id,
                now=current_time,
            )
        except SQLAlchemyError:
            raise CustomerDocumentServiceError(
                ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE
            ) from None
    return result


def _check_upload_rate_limit(
    session_factory: sessionmaker[Session],
    *,
    actor_user_id: UUID,
    client_ip: ResolvedClientIp,
    now: datetime,
    settings: Settings,
) -> None:
    try:
        with session_factory.begin() as session:
            result = check_storage_upload_rate_limit(
                session,
                settings,
                actor_user_id,
                client_ip,
                now,
            )
    except SQLAlchemyError:
        raise StorageUploadError(ErrorCode.FILE_STORAGE_ERROR) from None
    if not result.allowed:
        raise StorageUploadError(ErrorCode.RATE_LIMITED)


def _preflight_attachment(
    session_factory: sessionmaker[Session],
    *,
    command: UploadOwnCustomerDocument,
) -> _DocumentPreflight:
    try:
        with session_factory.begin() as session:
            own_customer = load_existing_own_customer_draft_for_update(
                session,
                actor_user_id=command.actor.user_id,
            )
            if own_customer is None:
                raise CustomerDocumentServiceError(ErrorCode.CUSTOMER_DRAFT_REQUIRED)
            repository = SqlAlchemyCustomerDocumentRepository(session)
            current = repository.lock_current_documents(customer_id=own_customer.id)
            if len(current) > 1:
                raise CustomerDocumentServiceError(
                    ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE
                )
            replay = repository.load_submission_replay(
                customer_id=own_customer.id,
                submission_id=command.submission_id,
            )
            if replay is not None:
                return _DocumentPreflight(
                    customer_id=own_customer.id,
                    replay=_replay_result(replay),
                )
            current_id = current[0].id if current else None
            if current_id != command.expected_current.document_id:
                raise CustomerDocumentServiceError(ErrorCode.CUSTOMER_DOCUMENT_CHANGED)
            return _DocumentPreflight(customer_id=own_customer.id, replay=None)
    except CustomerDocumentServiceError:
        raise
    except SQLAlchemyError:
        raise CustomerDocumentServiceError(
            ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE
        ) from None


def _attach_ingested_document(
    session_factory: sessionmaker[Session],
    *,
    command: UploadOwnCustomerDocument,
    expected_customer_id: UUID,
    ingested: IngestedImageResult,
    now: datetime,
) -> CustomerDocumentAttachmentResult:
    try:
        with session_factory.begin() as session:
            own_customer = load_existing_own_customer_draft_for_update(
                session,
                actor_user_id=command.actor.user_id,
            )
            if own_customer is None or own_customer.id != expected_customer_id:
                raise CustomerDocumentServiceError(ErrorCode.CUSTOMER_DRAFT_REQUIRED)
            object_file = load_object_file_for_update(
                session,
                object_file_id=ingested.object_file_id,
            )
            if (
                object_file is None
                or object_file.status != ObjectFileStatus.AVAILABLE.value
                or object_file.created_by_user_id != command.actor.user_id
            ):
                raise CustomerDocumentServiceError(ErrorCode.CUSTOMER_DOCUMENT_CHANGED)

            repository = SqlAlchemyCustomerDocumentRepository(session)
            if (
                repository.load_object_attachment(
                    object_file_id=ingested.object_file_id
                )
                is not None
            ):
                raise CustomerDocumentServiceError(ErrorCode.CUSTOMER_DOCUMENT_CHANGED)
            current = repository.lock_current_documents(customer_id=own_customer.id)
            if len(current) > 1:
                raise CustomerDocumentServiceError(
                    ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE
                )
            replay = repository.load_submission_replay(
                customer_id=own_customer.id,
                submission_id=command.submission_id,
            )
            if replay is not None:
                return _replay_result(replay)
            current_id = current[0].id if current else None
            if current_id != command.expected_current.document_id:
                raise CustomerDocumentServiceError(ErrorCode.CUSTOMER_DOCUMENT_CHANGED)

            attachment = CustomerDocumentAttachment(
                id=uuid4(),
                customer_id=own_customer.id,
                object_file_id=ingested.object_file_id,
                submission_id=command.submission_id,
                status=CustomerDocumentStatus.CURRENT,
                attached_by_user_id=command.actor.user_id,
                attached_at=now,
            )
            try:
                result = repository.attach_current_document(
                    attachment=attachment,
                    expected_current=command.expected_current,
                )
            except CustomerDocumentPersistenceConflict:
                raise CustomerDocumentServiceError(
                    ErrorCode.CUSTOMER_DOCUMENT_CHANGED
                ) from None
            if result.submission_replayed:
                return result
            _append_attachment_audits(
                session=session,
                result=result,
                actor_user_id=command.actor.user_id,
                now=now,
            )
            return result
    except CustomerDocumentServiceError:
        raise
    except SQLAlchemyError:
        raise CustomerDocumentServiceError(
            ErrorCode.CUSTOMER_DOCUMENT_UNAVAILABLE
        ) from None


def _append_attachment_audits(
    *,
    session: Session,
    result: CustomerDocumentAttachmentResult,
    actor_user_id: UUID,
    now: datetime,
) -> None:
    writer = SqlAlchemyAuditWriter(session)
    writer.append(
        event=AuditEvent(
            event_type=AuditEventType.CUSTOMER_DOCUMENT_ATTACHED,
            actor_kind=AuditActorKind.USER,
            actor_user_id=actor_user_id,
            object_type=AuditObjectType.CUSTOMER_DOCUMENT,
            object_id=result.document_id,
            occurred_at=now,
            candidate_metadata={
                "status": CustomerDocumentStatus.CURRENT,
                "submission_replayed": False,
            },
        )
    )
    if result.superseded_document_id is not None:
        writer.append(
            event=AuditEvent(
                event_type=AuditEventType.CUSTOMER_DOCUMENT_SUPERSEDED,
                actor_kind=AuditActorKind.USER,
                actor_user_id=actor_user_id,
                object_type=AuditObjectType.CUSTOMER_DOCUMENT,
                object_id=result.superseded_document_id,
                occurred_at=now,
                candidate_metadata={
                    "replacement_document_id": result.document_id,
                },
            )
        )


def _claim_compensation(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> None:
    with session_factory.begin() as session:
        claim_unattached_object_for_compensation(
            session,
            object_file_id=object_file_id,
            now=now,
        )


def _claim_compensation_preserving_failure(
    session_factory: sessionmaker[Session],
    *,
    object_file_id: UUID,
    now: datetime,
) -> None:
    try:
        _claim_compensation(
            session_factory,
            object_file_id=object_file_id,
            now=now,
        )
    except Exception:
        pass


def _replay_result(
    attachment: CustomerDocumentAttachment,
) -> CustomerDocumentAttachmentResult:
    return CustomerDocumentAttachmentResult(
        document_id=attachment.id,
        status=attachment.status,
        submission_replayed=True,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Customer document timestamp must be timezone-aware")
    return value.astimezone(UTC)
