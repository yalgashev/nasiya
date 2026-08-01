from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
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
)
from app.customer_document.models import CustomerDocument
from app.customer_document.repository import (
    CustomerDocumentPersistenceConflict,
    SqlAlchemyCustomerDocumentRepository,
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
