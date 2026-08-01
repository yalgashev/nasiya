from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.customer_document.contracts import (
    CustomerDocumentAccessParentRequest,
    CustomerDocumentAttachment,
    CustomerDocumentAttachmentResult,
    CustomerDocumentStatus,
    CustomerDocumentSubmissionId,
    ExpectedCurrentCustomerDocument,
    HasCurrentCustomerIdentityDocument,
)

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
CUSTOMER_ID = UUID("22222222-2222-2222-2222-222222222222")
OBJECT_FILE_ID = UUID("33333333-3333-3333-3333-333333333333")
SUBMISSION_ID = UUID("44444444-4444-4444-4444-444444444444")
ACTOR_ID = UUID("55555555-5555-5555-5555-555555555555")
REPLACEMENT_ID = UUID("66666666-6666-6666-6666-666666666666")
NOW = datetime(2026, 8, 1, 8, 0, tzinfo=UTC)


def _attachment(
    *,
    status: CustomerDocumentStatus = CustomerDocumentStatus.CURRENT,
    replacement_id: UUID | None = None,
    superseded_at: datetime | None = None,
) -> CustomerDocumentAttachment:
    return CustomerDocumentAttachment(
        id=DOCUMENT_ID,
        customer_id=CUSTOMER_ID,
        object_file_id=OBJECT_FILE_ID,
        submission_id=CustomerDocumentSubmissionId(SUBMISSION_ID),
        status=status,
        attached_by_user_id=ACTOR_ID,
        attached_at=NOW,
        superseded_by_document_id=replacement_id,
        superseded_at=superseded_at,
    )


def test_current_attachment_has_exact_metadata_and_redacted_repr() -> None:
    attachment = _attachment()
    rendered = repr(attachment)

    assert attachment.status is CustomerDocumentStatus.CURRENT
    assert attachment.superseded_by_document_id is None
    assert attachment.superseded_at is None
    for identifier in (
        DOCUMENT_ID,
        CUSTOMER_ID,
        OBJECT_FILE_ID,
        SUBMISSION_ID,
        ACTOR_ID,
    ):
        assert str(identifier) not in rendered
    with pytest.raises(FrozenInstanceError):
        attachment.status = CustomerDocumentStatus.SUPERSEDED  # type: ignore[misc]


def test_superseded_attachment_requires_complete_non_self_metadata() -> None:
    attachment = _attachment(
        status=CustomerDocumentStatus.SUPERSEDED,
        replacement_id=REPLACEMENT_ID,
        superseded_at=NOW + timedelta(seconds=1),
    )
    assert attachment.superseded_by_document_id == REPLACEMENT_ID

    invalid_cases = (
        (CustomerDocumentStatus.CURRENT, REPLACEMENT_ID, NOW),
        (CustomerDocumentStatus.SUPERSEDED, None, NOW),
        (CustomerDocumentStatus.SUPERSEDED, REPLACEMENT_ID, None),
        (CustomerDocumentStatus.SUPERSEDED, DOCUMENT_ID, NOW),
        (
            CustomerDocumentStatus.SUPERSEDED,
            REPLACEMENT_ID,
            NOW - timedelta(seconds=1),
        ),
    )
    for status, replacement_id, superseded_at in invalid_cases:
        with pytest.raises(ValueError):
            _attachment(
                status=status,
                replacement_id=replacement_id,
                superseded_at=superseded_at,
            )


def test_submission_snapshot_result_and_access_parent_redact_identifiers() -> None:
    submission = CustomerDocumentSubmissionId(SUBMISSION_ID)
    expected = ExpectedCurrentCustomerDocument(DOCUMENT_ID)
    result = CustomerDocumentAttachmentResult(
        document_id=REPLACEMENT_ID,
        status=CustomerDocumentStatus.CURRENT,
        submission_replayed=False,
        superseded_document_id=DOCUMENT_ID,
    )
    parent = CustomerDocumentAccessParentRequest(
        customer_id=CUSTOMER_ID,
        document_id=REPLACEMENT_ID,
    )

    rendered = f"{submission!r} {expected!r} {result!r} {parent!r}"
    for identifier in (
        SUBMISSION_ID,
        DOCUMENT_ID,
        REPLACEMENT_ID,
        CUSTOMER_ID,
    ):
        assert str(identifier) not in rendered


def test_attachment_result_rejects_self_replacement_and_replay_mutation() -> None:
    with pytest.raises(ValueError, match="supersede itself"):
        CustomerDocumentAttachmentResult(
            document_id=DOCUMENT_ID,
            status=CustomerDocumentStatus.CURRENT,
            submission_replayed=False,
            superseded_document_id=DOCUMENT_ID,
        )
    with pytest.raises(ValueError, match="Replay result"):
        CustomerDocumentAttachmentResult(
            document_id=REPLACEMENT_ID,
            status=CustomerDocumentStatus.CURRENT,
            submission_replayed=True,
            superseded_document_id=DOCUMENT_ID,
        )


def test_document_completeness_protocol_is_runtime_checkable_and_narrow() -> None:
    class Complete:
        def __call__(self, *, customer_id: UUID) -> bool:
            return customer_id == CUSTOMER_ID

    implementation = Complete()
    assert isinstance(implementation, HasCurrentCustomerIdentityDocument)
    assert implementation(customer_id=CUSTOMER_ID) is True
