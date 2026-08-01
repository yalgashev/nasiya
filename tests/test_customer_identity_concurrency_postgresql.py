from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_identity.contracts import (
    EncryptedCustomerIdentityRecord,
    IdentityRevision,
)
from app.customer_identity.crypto import (
    CustomerIdentityEnvelope,
    CustomerIdentityKeyId,
    JshshirBlindIndex,
)
from app.customer_identity.models import CustomerIdentity
from app.customer_identity.repository import (
    CustomerIdentityRevisionConflict,
    SqlAlchemyCustomerIdentityRepository,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 11, 0, tzinfo=UTC)


def _setup_customer(engine: Engine) -> tuple[UUID, UUID]:
    with Session(engine) as session, session.begin():
        user = User(
            phone="+998900001201",
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
        return user.id, customer.id


def _record(customer_id: UUID, marker: bytes) -> EncryptedCustomerIdentityRecord:
    return EncryptedCustomerIdentityRecord(
        customer_id=customer_id,
        envelope=CustomerIdentityEnvelope(
            ciphertext=marker * 32,
            nonce=marker * 12,
            key_id=CustomerIdentityKeyId("identity-v1"),
            schema_version=1,
        ),
        jshshir_blind_index=JshshirBlindIndex(marker * 32),
        revision=IdentityRevision(1),
    )


def test_parallel_first_identity_candidates_have_one_winner(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id = _setup_customer(m2_test_database)
    barrier = Barrier(2)

    def worker(marker: bytes) -> str:
        with Session(m2_test_database) as session, session.begin():
            repository = SqlAlchemyCustomerIdentityRepository(session)
            barrier.wait(timeout=10)
            assert (
                repository.lock_own_customer_draft(actor_user_id=actor_id) is not None
            )
            try:
                repository.save_identity(
                    record=_record(customer_id, marker),
                    expected_revision=0,
                )
            except CustomerIdentityRevisionConflict:
                return "changed"
            return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(worker, (b"A", b"B")))

    assert sorted(outcomes) == ["changed", "saved"]
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(CustomerIdentity)) == 1
        stored = session.scalar(select(CustomerIdentity))
        assert stored is not None
        assert stored.revision == 1
