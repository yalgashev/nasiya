from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_document.contracts import (
    CustomerDocumentAttachment,
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
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.repository import load_object_file_for_update

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _setup(engine: Engine) -> tuple[UUID, UUID, tuple[UUID, UUID]]:
    with Session(engine) as session, session.begin():
        user = User(
            phone="+998900001202",
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
        object_ids = (uuid4(), uuid4())
        for index, object_id in enumerate(object_ids):
            session.add(
                ObjectFile(
                    id=object_id,
                    bucket="nasiya-private-test",
                    object_key=f"v1/objects/{object_id.hex}.png",
                    content_type="image/png",
                    size_bytes=128 + index,
                    checksum_sha256=("a" if index == 0 else "b") * 64,
                    width_px=8,
                    height_px=6,
                    status=ObjectFileStatus.AVAILABLE.value,
                    created_by_user_id=user.id,
                    failure_code=None,
                    created_at=NOW,
                    updated_at=NOW,
                    available_at=NOW,
                    terminal_at=None,
                    deleted_at=None,
                )
            )
        session.flush()
        return user.id, customer.id, object_ids


def test_parallel_first_document_candidates_have_one_winner(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id, object_ids = _setup(m2_test_database)
    barrier = Barrier(2)

    def worker(object_file_id: UUID) -> str:
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=10)
            assert (
                SqlAlchemyCustomerIdentityRepository(session).lock_own_customer_draft(
                    actor_user_id=actor_id
                )
                is not None
            )
            assert (
                load_object_file_for_update(
                    session,
                    object_file_id=object_file_id,
                )
                is not None
            )
            repository = SqlAlchemyCustomerDocumentRepository(session)
            try:
                repository.attach_current_document(
                    attachment=CustomerDocumentAttachment(
                        id=uuid4(),
                        customer_id=customer_id,
                        object_file_id=object_file_id,
                        submission_id=CustomerDocumentSubmissionId(uuid4()),
                        status=CustomerDocumentStatus.CURRENT,
                        attached_by_user_id=actor_id,
                        attached_at=NOW,
                    ),
                    expected_current=ExpectedCurrentCustomerDocument(None),
                )
            except CustomerDocumentPersistenceConflict:
                return "changed"
            return "attached"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(worker, object_ids))

    assert sorted(outcomes) == ["attached", "changed"]
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(CustomerDocument)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(CustomerDocument)
                .where(CustomerDocument.status == CustomerDocumentStatus.CURRENT.value)
            )
            == 1
        )


def test_parallel_stale_replacements_have_one_winner_and_one_zero_write(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id, object_ids = _setup(m2_test_database)
    initial_object_id = uuid4()
    initial_document_id = uuid4()
    with Session(m2_test_database) as session, session.begin():
        session.add(
            ObjectFile(
                id=initial_object_id,
                bucket="nasiya-private-test",
                object_key=f"v1/objects/{initial_object_id.hex}.png",
                content_type="image/png",
                size_bytes=130,
                checksum_sha256="c" * 64,
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
        )
        session.flush()
        assert (
            load_object_file_for_update(
                session,
                object_file_id=initial_object_id,
            )
            is not None
        )
        SqlAlchemyCustomerDocumentRepository(session).attach_current_document(
            attachment=CustomerDocumentAttachment(
                id=initial_document_id,
                customer_id=customer_id,
                object_file_id=initial_object_id,
                submission_id=CustomerDocumentSubmissionId(uuid4()),
                status=CustomerDocumentStatus.CURRENT,
                attached_by_user_id=actor_id,
                attached_at=NOW,
            ),
            expected_current=ExpectedCurrentCustomerDocument(None),
        )

    barrier = Barrier(2)

    def worker(object_file_id: UUID) -> str:
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=10)
            assert (
                SqlAlchemyCustomerIdentityRepository(session).lock_own_customer_draft(
                    actor_user_id=actor_id
                )
                is not None
            )
            assert (
                load_object_file_for_update(
                    session,
                    object_file_id=object_file_id,
                )
                is not None
            )
            try:
                SqlAlchemyCustomerDocumentRepository(session).attach_current_document(
                    attachment=CustomerDocumentAttachment(
                        id=uuid4(),
                        customer_id=customer_id,
                        object_file_id=object_file_id,
                        submission_id=CustomerDocumentSubmissionId(uuid4()),
                        status=CustomerDocumentStatus.CURRENT,
                        attached_by_user_id=actor_id,
                        attached_at=NOW,
                    ),
                    expected_current=ExpectedCurrentCustomerDocument(
                        initial_document_id
                    ),
                )
            except CustomerDocumentPersistenceConflict:
                return "changed"
            return "attached"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(worker, object_ids))

    assert sorted(outcomes) == ["attached", "changed"]
    with Session(m2_test_database) as session:
        documents = tuple(session.scalars(select(CustomerDocument)))
        assert len(documents) == 2
        assert sum(document.status == "CURRENT" for document in documents) == 1
        initial = session.get(CustomerDocument, initial_document_id)
        assert initial is not None
        assert initial.status == "SUPERSEDED"
        attached_object_ids = {document.object_file_id for document in documents}
        assert len(set(object_ids) - attached_object_ids) == 1


def test_attach_winner_serializes_compensation_to_noop(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id, object_ids = _setup(m2_test_database)
    object_file_id = object_ids[0]
    object_locked = Event()
    claim_started = Event()
    allow_attach_commit = Event()

    def attach() -> str:
        with Session(m2_test_database) as session, session.begin():
            assert (
                SqlAlchemyCustomerIdentityRepository(session).lock_own_customer_draft(
                    actor_user_id=actor_id
                )
                is not None
            )
            object_file = load_object_file_for_update(
                session,
                object_file_id=object_file_id,
            )
            assert object_file is not None
            assert object_file.status == ObjectFileStatus.AVAILABLE.value
            object_locked.set()
            assert allow_attach_commit.wait(timeout=10)
            SqlAlchemyCustomerDocumentRepository(session).attach_current_document(
                attachment=CustomerDocumentAttachment(
                    id=uuid4(),
                    customer_id=customer_id,
                    object_file_id=object_file_id,
                    submission_id=CustomerDocumentSubmissionId(uuid4()),
                    status=CustomerDocumentStatus.CURRENT,
                    attached_by_user_id=actor_id,
                    attached_at=NOW,
                ),
                expected_current=ExpectedCurrentCustomerDocument(None),
            )
        return "attached"

    def claim() -> UnattachedObjectCompensationStatus:
        assert object_locked.wait(timeout=10)
        with Session(m2_test_database) as session, session.begin():
            claim_started.set()
            return claim_unattached_object_for_compensation(
                session,
                object_file_id=object_file_id,
                now=NOW,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        attached_future = executor.submit(attach)
        claimed_future = executor.submit(claim)
        assert claim_started.wait(timeout=10)
        allow_attach_commit.set()
        assert attached_future.result(timeout=10) == "attached"
        assert (
            claimed_future.result(timeout=10) is UnattachedObjectCompensationStatus.NOOP
        )

    with Session(m2_test_database) as session:
        object_file = session.get(ObjectFile, object_file_id)
        assert object_file is not None
        assert object_file.status == ObjectFileStatus.AVAILABLE.value
        assert (
            session.scalar(
                select(func.count())
                .select_from(CustomerDocument)
                .where(CustomerDocument.object_file_id == object_file_id)
            )
            == 1
        )


def test_compensation_winner_blocks_attachment_with_zero_write(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id, object_ids = _setup(m2_test_database)
    object_file_id = object_ids[0]
    object_locked = Event()
    attach_started = Event()
    allow_claim_commit = Event()

    def claim() -> UnattachedObjectCompensationStatus:
        with Session(m2_test_database) as session, session.begin():
            object_file = load_object_file_for_update(
                session,
                object_file_id=object_file_id,
            )
            assert object_file is not None
            object_locked.set()
            assert allow_claim_commit.wait(timeout=10)
            return claim_unattached_object_for_compensation(
                session,
                object_file_id=object_file_id,
                now=NOW,
            )

    def attach() -> str:
        assert object_locked.wait(timeout=10)
        with Session(m2_test_database) as session, session.begin():
            assert (
                SqlAlchemyCustomerIdentityRepository(session).lock_own_customer_draft(
                    actor_user_id=actor_id
                )
                is not None
            )
            attach_started.set()
            object_file = load_object_file_for_update(
                session,
                object_file_id=object_file_id,
            )
            assert object_file is not None
            if object_file.status != ObjectFileStatus.AVAILABLE.value:
                return "changed"
            SqlAlchemyCustomerDocumentRepository(session).attach_current_document(
                attachment=CustomerDocumentAttachment(
                    id=uuid4(),
                    customer_id=customer_id,
                    object_file_id=object_file_id,
                    submission_id=CustomerDocumentSubmissionId(uuid4()),
                    status=CustomerDocumentStatus.CURRENT,
                    attached_by_user_id=actor_id,
                    attached_at=NOW,
                ),
                expected_current=ExpectedCurrentCustomerDocument(None),
            )
        return "attached"

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_future = executor.submit(claim)
        attached_future = executor.submit(attach)
        assert attach_started.wait(timeout=10)
        allow_claim_commit.set()
        assert (
            claimed_future.result(timeout=10)
            is UnattachedObjectCompensationStatus.CLAIMED
        )
        assert attached_future.result(timeout=10) == "changed"

    with Session(m2_test_database) as session:
        object_file = session.get(ObjectFile, object_file_id)
        assert object_file is not None
        assert object_file.status == ObjectFileStatus.DELETE_PENDING.value
        assert (
            session.scalar(
                select(func.count())
                .select_from(CustomerDocument)
                .where(CustomerDocument.object_file_id == object_file_id)
            )
            == 0
        )
