import inspect
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.customer_identity.repository as identity_repository_module
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_identity.contracts import (
    CustomerIdentityRepository,
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
    CustomerIdentityBlindIndexConflict,
    CustomerIdentityRevisionConflict,
    SqlAlchemyCustomerIdentityRepository,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _user_customer(
    session: Session,
    *,
    phone: str,
) -> tuple[User, Customer]:
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


def _record(
    customer_id: UUID,
    *,
    revision: int,
    blind_byte: bytes,
    ciphertext_byte: bytes = b"C",
) -> EncryptedCustomerIdentityRecord:
    return EncryptedCustomerIdentityRecord(
        customer_id=customer_id,
        envelope=CustomerIdentityEnvelope(
            ciphertext=ciphertext_byte * 32,
            nonce=b"N" * 12,
            key_id=CustomerIdentityKeyId("identity-v1"),
            schema_version=1,
        ),
        jshshir_blind_index=JshshirBlindIndex(blind_byte * 32),
        revision=IdentityRevision(revision),
    )


def test_identity_repository_create_update_stale_and_outer_ownership(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session:
        user, customer = _user_customer(session, phone="+998900001001")
        repository = SqlAlchemyCustomerIdentityRepository(session)
        assert isinstance(repository, CustomerIdentityRepository)
        own = repository.lock_own_customer_draft(actor_user_id=user.id)
        assert own is not None
        assert own.customer_id == customer.id

        created = repository.save_identity(
            record=_record(customer.id, revision=1, blind_byte=b"A"),
            expected_revision=0,
        )
        assert created.revision == IdentityRevision(1)
        assert repository.get_identity(customer_id=customer.id) == created
        assert repository.lock_identity(customer_id=customer.id) == created

        updated = repository.save_identity(
            record=_record(
                customer.id,
                revision=2,
                blind_byte=b"A",
                ciphertext_byte=b"D",
            ),
            expected_revision=1,
        )
        assert updated.revision == IdentityRevision(2)
        with pytest.raises(CustomerIdentityRevisionConflict):
            repository.save_identity(
                record=_record(
                    customer.id,
                    revision=2,
                    blind_byte=b"A",
                    ciphertext_byte=b"E",
                ),
                expected_revision=1,
            )
        assert repository.get_identity(customer_id=customer.id) == updated
        session.rollback()

    with Session(m2_test_database) as verification:
        assert verification.scalar(select(func.count()).select_from(User)) == 0
        assert verification.scalar(select(func.count()).select_from(Customer)) == 0
        assert (
            verification.scalar(select(func.count()).select_from(CustomerIdentity)) == 0
        )


def test_duplicate_blind_index_uses_savepoint_and_keeps_session_usable(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        _, first_customer = _user_customer(session, phone="+998900001002")
        _, second_customer = _user_customer(session, phone="+998900001003")
        repository = SqlAlchemyCustomerIdentityRepository(session)
        repository.save_identity(
            record=_record(first_customer.id, revision=1, blind_byte=b"B"),
            expected_revision=0,
        )

        with pytest.raises(CustomerIdentityBlindIndexConflict) as caught:
            repository.save_identity(
                record=_record(second_customer.id, revision=1, blind_byte=b"B"),
                expected_revision=0,
            )

        assert caught.value.__cause__ is None
        assert session.scalar(select(1)) == 1
        assert repository.get_identity(customer_id=second_customer.id) is None
        assert session.scalar(select(func.count()).select_from(CustomerIdentity)) == 1


def test_identity_repository_resolves_only_existing_exact_own_draft(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        first_user, first_customer = _user_customer(
            session,
            phone="+998900001004",
        )
        second_user, second_customer = _user_customer(
            session,
            phone="+998900001005",
        )
        repository = SqlAlchemyCustomerIdentityRepository(session)

        resolved = repository.lock_own_customer_draft(
            actor_user_id=first_user.id,
        )
        assert resolved is not None
        assert resolved.customer_id == first_customer.id
        assert resolved.customer_id != second_customer.id
        assert repository.lock_own_customer_draft(actor_user_id=uuid4()) is None
        assert second_user.id != first_user.id


def test_identity_repository_source_has_no_transaction_or_plaintext_api() -> None:
    source = inspect.getsource(identity_repository_module)

    for forbidden in (
        ".commit(",
        ".rollback(",
        ".close(",
        "first_name",
        "last_name",
        "middle_name",
        "document_number",
        "httpx",
        "boto",
        "logger",
        "print(",
    ):
        assert forbidden not in source
