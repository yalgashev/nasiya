from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID


class CustomerDocumentStatus(StrEnum):
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"


class UnattachedObjectCompensationStatus(StrEnum):
    CLAIMED = "CLAIMED"
    NOOP = "NOOP"


def parse_customer_document_status(value: str) -> CustomerDocumentStatus:
    try:
        return CustomerDocumentStatus(value)
    except (TypeError, ValueError):
        raise ValueError("Unknown customer document status") from None


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDocumentSubmissionId:
    value: UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.value, field_name="submission_id")

    def __repr__(self) -> str:
        return "CustomerDocumentSubmissionId(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ExpectedCurrentCustomerDocument:
    document_id: UUID | None = field(repr=False)

    def __post_init__(self) -> None:
        if self.document_id is not None:
            _require_uuid(self.document_id, field_name="expected_current_document_id")

    def __repr__(self) -> str:
        return "ExpectedCurrentCustomerDocument(document_id=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDocumentActor:
    user_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.user_id, field_name="actor_user_id")

    def __repr__(self) -> str:
        return "CustomerDocumentActor(user_id=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class UploadOwnCustomerDocument:
    actor: CustomerDocumentActor
    submission_id: CustomerDocumentSubmissionId = field(repr=False)
    expected_current: ExpectedCurrentCustomerDocument = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.actor, CustomerDocumentActor):
            raise ValueError("Customer document actor is invalid")
        if not isinstance(self.submission_id, CustomerDocumentSubmissionId):
            raise ValueError("Customer document submission ID is invalid")
        if not isinstance(self.expected_current, ExpectedCurrentCustomerDocument):
            raise ValueError("Expected current customer document is invalid")

    def __repr__(self) -> str:
        return (
            "UploadOwnCustomerDocument(actor=<redacted>, "
            "submission_id=<redacted>, expected_current=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDocumentAttachment:
    id: UUID = field(repr=False)
    customer_id: UUID = field(repr=False)
    object_file_id: UUID = field(repr=False)
    submission_id: CustomerDocumentSubmissionId = field(repr=False)
    status: CustomerDocumentStatus
    attached_by_user_id: UUID = field(repr=False)
    attached_at: datetime
    superseded_by_document_id: UUID | None = field(default=None, repr=False)
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, field_name="document_id")
        _require_uuid(self.customer_id, field_name="customer_id")
        _require_uuid(self.object_file_id, field_name="object_file_id")
        if not isinstance(self.submission_id, CustomerDocumentSubmissionId):
            raise ValueError("Customer document submission ID is invalid")
        if not isinstance(self.status, CustomerDocumentStatus):
            raise ValueError("Customer document status is invalid")
        _require_uuid(self.attached_by_user_id, field_name="attached_by_user_id")
        object.__setattr__(
            self,
            "attached_at",
            _as_utc(self.attached_at, field_name="attached_at"),
        )
        supersede_values = (
            self.superseded_by_document_id,
            self.superseded_at,
        )
        supersede_is_empty = all(value is None for value in supersede_values)
        supersede_is_complete = all(value is not None for value in supersede_values)
        if self.status is CustomerDocumentStatus.CURRENT and not supersede_is_empty:
            raise ValueError("Current document cannot have supersede metadata")
        if self.status is CustomerDocumentStatus.SUPERSEDED and not (
            supersede_is_complete
        ):
            raise ValueError("Superseded document requires replacement metadata")
        if supersede_is_complete:
            _require_uuid(
                self.superseded_by_document_id,
                field_name="superseded_by_document_id",
            )
            if self.superseded_by_document_id == self.id:
                raise ValueError("Customer document cannot supersede itself")
            superseded_at = _as_utc(
                self.superseded_at,
                field_name="superseded_at",
            )
            if superseded_at < self.attached_at:
                raise ValueError("Customer document supersede time is invalid")
            object.__setattr__(self, "superseded_at", superseded_at)

    def __repr__(self) -> str:
        return (
            "CustomerDocumentAttachment("
            "id=<redacted>, customer_id=<redacted>, "
            "object_file_id=<redacted>, submission_id=<redacted>, "
            f"status={self.status.value!r}, attached_by_user_id=<redacted>, "
            f"attached_at={self.attached_at!r}, "
            "superseded_by_document_id=<redacted>, "
            f"superseded_at={self.superseded_at!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDocumentAttachmentResult:
    document_id: UUID = field(repr=False)
    status: CustomerDocumentStatus
    submission_replayed: bool
    superseded_document_id: UUID | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.document_id, field_name="document_id")
        if not isinstance(self.status, CustomerDocumentStatus):
            raise ValueError("Customer document status is invalid")
        if not isinstance(self.submission_replayed, bool):
            raise ValueError("Submission replay marker must be a boolean")
        if (
            not self.submission_replayed
            and self.status is not CustomerDocumentStatus.CURRENT
        ):
            raise ValueError("New attachment result must resolve to current document")
        if self.superseded_document_id is not None:
            _require_uuid(
                self.superseded_document_id,
                field_name="superseded_document_id",
            )
            if self.superseded_document_id == self.document_id:
                raise ValueError("Customer document cannot supersede itself")
        if self.submission_replayed and self.superseded_document_id is not None:
            raise ValueError("Replay result cannot report a new replacement")

    def __repr__(self) -> str:
        return (
            "CustomerDocumentAttachmentResult("
            "document_id=<redacted>, "
            f"status={self.status.value!r}, "
            f"submission_replayed={self.submission_replayed!r}, "
            "superseded_document_id=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class CustomerDocumentAccessParentRequest:
    customer_id: UUID = field(repr=False)
    document_id: UUID = field(repr=False)
    object_file_id: UUID = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.customer_id, field_name="customer_id")
        _require_uuid(self.document_id, field_name="document_id")
        _require_uuid(self.object_file_id, field_name="object_file_id")

    def __repr__(self) -> str:
        return (
            "CustomerDocumentAccessParentRequest("
            "customer_id=<redacted>, document_id=<redacted>, "
            "object_file_id=<redacted>)"
        )


@runtime_checkable
class HasCurrentCustomerIdentityDocument(Protocol):
    def __call__(self, *, customer_id: UUID) -> bool: ...


@runtime_checkable
class CustomerDocumentRepository(Protocol):
    def lock_current_documents(
        self,
        *,
        customer_id: UUID,
    ) -> tuple[CustomerDocumentAttachment, ...]: ...

    def load_submission_replay(
        self,
        *,
        customer_id: UUID,
        submission_id: CustomerDocumentSubmissionId,
    ) -> CustomerDocumentAttachment | None: ...

    def load_object_attachment(
        self,
        *,
        object_file_id: UUID,
    ) -> CustomerDocumentAttachment | None: ...

    def attach_current_document(
        self,
        *,
        attachment: CustomerDocumentAttachment,
        expected_current: ExpectedCurrentCustomerDocument,
    ) -> CustomerDocumentAttachmentResult: ...

    def resolve_access_parent(
        self,
        *,
        customer_id: UUID,
    ) -> CustomerDocumentAccessParentRequest | None: ...


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _require_uuid(value: object, *, field_name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{field_name} must be a UUID")
