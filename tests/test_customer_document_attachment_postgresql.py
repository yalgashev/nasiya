import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer_document.repository as document_repository_module
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_document.contracts import (
    CustomerDocumentAttachment,
    CustomerDocumentRepository,
    CustomerDocumentStatus,
    CustomerDocumentSubmissionId,
    ExpectedCurrentCustomerDocument,
    UnattachedObjectCompensationStatus,
)
from app.customer_document.models import CustomerDocument
from app.customer_document.repository import (
    CustomerDocumentPersistenceConflict,
    SqlAlchemyCustomerDocumentRepository,
    claim_unattached_object_for_compensation,
)
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.repository import load_object_file_for_update

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def _seed_owner(session: Session, *, phone: str) -> tuple[User, Customer]:
    user = User(
        phone=phone,
        password_hash=None,
        is_active=True,
        is_platform_admin=False,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(user)
    session.flush()
    customer = Customer(
        user_id=user.id,
        onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(customer)
    session.flush()
    return user, customer


def _available_object(session: Session, *, actor_id: UUID) -> ObjectFile:
    object_id = uuid4()
    model = ObjectFile(
        id=object_id,
        bucket="nasiya-private-test",
        object_key=f"v1/objects/{object_id.hex}.png",
        content_type="image/png",
        size_bytes=128,
        checksum_sha256="a" * 64,
        width_px=8,
        height_px=6,
        status=ObjectFileStatus.AVAILABLE.value,
        created_by_user_id=actor_id,
        failure_code=None,
        created_at=NOW,
        updated_at=NOW,
        available_at=NOW,
        terminal_at=None,
        deleted_at=None,
    )
    session.add(model)
    session.flush()
    return model


def _attachment(
    *,
    customer_id: UUID,
    object_file_id: UUID,
    actor_id: UUID,
    submission_id: UUID,
    document_id: UUID | None = None,
    attached_at: datetime = NOW,
) -> CustomerDocumentAttachment:
    return CustomerDocumentAttachment(
        id=document_id or uuid4(),
        customer_id=customer_id,
        object_file_id=object_file_id,
        submission_id=CustomerDocumentSubmissionId(submission_id),
        status=CustomerDocumentStatus.CURRENT,
        attached_by_user_id=actor_id,
        attached_at=attached_at,
    )


def test_document_repository_attach_replay_replace_and_access_parent(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, customer = _seed_owner(session, phone="+998900001101")
        first_object = _available_object(session, actor_id=user.id)
        second_object = _available_object(session, actor_id=user.id)
        repository = SqlAlchemyCustomerDocumentRepository(session)
        assert isinstance(repository, CustomerDocumentRepository)

        assert (
            load_object_file_for_update(
                session,
                object_file_id=first_object.id,
            )
            is not None
        )
        first = _attachment(
            customer_id=customer.id,
            object_file_id=first_object.id,
            actor_id=user.id,
            submission_id=uuid4(),
        )
        first_result = repository.attach_current_document(
            attachment=first,
            expected_current=ExpectedCurrentCustomerDocument(None),
        )
        assert first_result.document_id == first.id
        assert first_result.submission_replayed is False

        replay = repository.attach_current_document(
            attachment=first,
            expected_current=ExpectedCurrentCustomerDocument(None),
        )
        assert replay.document_id == first.id
        assert replay.submission_replayed is True

        assert (
            load_object_file_for_update(
                session,
                object_file_id=second_object.id,
            )
            is not None
        )
        second = _attachment(
            customer_id=customer.id,
            object_file_id=second_object.id,
            actor_id=user.id,
            submission_id=uuid4(),
            attached_at=NOW + timedelta(seconds=1),
        )
        second_result = repository.attach_current_document(
            attachment=second,
            expected_current=ExpectedCurrentCustomerDocument(first.id),
        )
        assert second_result.superseded_document_id == first.id
        current = repository.lock_current_documents(customer_id=customer.id)
        assert tuple(document.id for document in current) == (second.id,)
        historical_replay = repository.load_submission_replay(
            customer_id=customer.id,
            submission_id=first.submission_id,
        )
        assert historical_replay is not None
        assert historical_replay.status is CustomerDocumentStatus.SUPERSEDED
        replay_result = repository.attach_current_document(
            attachment=first,
            expected_current=ExpectedCurrentCustomerDocument(second.id),
        )
        assert replay_result.status is CustomerDocumentStatus.SUPERSEDED
        assert replay_result.submission_replayed is True
        assert repository.resolve_access_parent(customer_id=customer.id) == (
            repository.resolve_access_parent(customer_id=customer.id)
        )
        assert repository.resolve_access_parent(
            customer_id=customer.id
        ).document_id == (second.id)


def test_document_stale_snapshot_and_object_reuse_are_zero_write(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, customer = _seed_owner(session, phone="+998900001102")
        object_file = _available_object(session, actor_id=user.id)
        load_object_file_for_update(session, object_file_id=object_file.id)
        repository = SqlAlchemyCustomerDocumentRepository(session)
        candidate = _attachment(
            customer_id=customer.id,
            object_file_id=object_file.id,
            actor_id=user.id,
            submission_id=uuid4(),
        )

        with pytest.raises(CustomerDocumentPersistenceConflict):
            repository.attach_current_document(
                attachment=candidate,
                expected_current=ExpectedCurrentCustomerDocument(uuid4()),
            )
        assert session.scalar(select(func.count()).select_from(CustomerDocument)) == 0

        repository.attach_current_document(
            attachment=candidate,
            expected_current=ExpectedCurrentCustomerDocument(None),
        )
        other_candidate = _attachment(
            customer_id=customer.id,
            object_file_id=object_file.id,
            actor_id=user.id,
            submission_id=uuid4(),
        )
        with pytest.raises(CustomerDocumentPersistenceConflict):
            repository.attach_current_document(
                attachment=other_candidate,
                expected_current=ExpectedCurrentCustomerDocument(candidate.id),
            )
        assert session.scalar(select(func.count()).select_from(CustomerDocument)) == 1
        assert session.scalar(select(1)) == 1


def test_document_repository_follows_outer_rollback(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session:
        user, customer = _seed_owner(session, phone="+998900001103")
        object_file = _available_object(session, actor_id=user.id)
        load_object_file_for_update(session, object_file_id=object_file.id)
        SqlAlchemyCustomerDocumentRepository(session).attach_current_document(
            attachment=_attachment(
                customer_id=customer.id,
                object_file_id=object_file.id,
                actor_id=user.id,
                submission_id=uuid4(),
            ),
            expected_current=ExpectedCurrentCustomerDocument(None),
        )
        session.rollback()

    with Session(m2_test_database) as verification:
        assert (
            verification.scalar(select(func.count()).select_from(CustomerDocument)) == 0
        )


def test_compensation_claim_is_atomic_and_attached_object_is_always_noop(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, customer = _seed_owner(session, phone="+998900001104")
        attached_object = _available_object(session, actor_id=user.id)
        orphan_object = _available_object(session, actor_id=user.id)
        load_object_file_for_update(session, object_file_id=attached_object.id)
        SqlAlchemyCustomerDocumentRepository(session).attach_current_document(
            attachment=_attachment(
                customer_id=customer.id,
                object_file_id=attached_object.id,
                actor_id=user.id,
                submission_id=uuid4(),
            ),
            expected_current=ExpectedCurrentCustomerDocument(None),
        )

        attached_outcome = claim_unattached_object_for_compensation(
            session,
            object_file_id=attached_object.id,
            now=NOW,
        )
        orphan_outcome = claim_unattached_object_for_compensation(
            session,
            object_file_id=orphan_object.id,
            now=NOW,
        )

        assert attached_outcome is UnattachedObjectCompensationStatus.NOOP
        assert orphan_outcome is UnattachedObjectCompensationStatus.CLAIMED
        assert attached_object.status == ObjectFileStatus.AVAILABLE.value
        assert orphan_object.status == ObjectFileStatus.DELETE_PENDING.value


def test_document_repository_source_has_no_storage_io_or_transaction_owner() -> None:
    source = inspect.getsource(document_repository_module)

    for forbidden in (
        ".commit(",
        ".rollback(",
        ".close(",
        "boto",
        "httpx",
        "presigned",
        "bucket",
        "object_key",
        "checksum",
        "filename",
        "logger",
        "print(",
    ):
        assert forbidden not in source
