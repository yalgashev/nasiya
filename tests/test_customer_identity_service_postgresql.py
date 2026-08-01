from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_identity.contracts import (
    CustomerIdentityActor,
    SaveCustomerIdentity,
)
from app.customer_identity.crypto import (
    CustomerIdentityAesKey,
    CustomerIdentityBlindIndexKey,
    CustomerIdentityCryptoConfig,
    CustomerIdentityKeyId,
)
from app.customer_identity.models import CustomerIdentity
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.service import (
    CustomerIdentityCompletenessService,
    CustomerIdentityServiceError,
    get_own_customer_identity_view,
    save_own_customer_identity,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 16, 0, tzinfo=UTC)


def _config() -> CustomerIdentityCryptoConfig:
    key_id = CustomerIdentityKeyId("identity-v1")
    return CustomerIdentityCryptoConfig(
        active_key_id=key_id,
        encryption_keys={key_id: CustomerIdentityAesKey.from_bytes(bytes(range(32)))},
        blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(
            bytes(reversed(range(32)))
        ),
    )


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


def _command(
    actor_id: UUID,
    *,
    expected_revision: int,
    jshshir: str,
    first_name: str = "Synthetic",
) -> SaveCustomerIdentity:
    return SaveCustomerIdentity(
        actor=CustomerIdentityActor(actor_id),
        expected_revision=expected_revision,
        first_name=first_name,
        last_name="Specimen",
        middle_name=None,
        jshshir=jshshir,
        document_type="ID_CARD",
        document_number="ID 12345",
    )


def test_identity_service_create_read_update_stale_and_audit(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, customer = _user_customer(session, phone="+998900001401")
        repository = SqlAlchemyCustomerIdentityRepository(session)
        audit = SqlAlchemyAuditWriter(session)
        created = save_own_customer_identity(
            repository=repository,
            audit_writer=audit,
            crypto_config=_config(),
            command=_command(
                user.id,
                expected_revision=0,
                jshshir="12345678901234",
            ),
            now=NOW,
        )
        assert created.revision.value == 1
        assert created.masked_jshshir == "**********1234"
        assert created.masked_document_number == "****2345"
        read = get_own_customer_identity_view(
            repository=repository,
            crypto_config=_config(),
            actor=CustomerIdentityActor(user.id),
        )
        assert read == created

        updated = save_own_customer_identity(
            repository=repository,
            audit_writer=audit,
            crypto_config=_config(),
            command=_command(
                user.id,
                expected_revision=1,
                jshshir="12345678901234",
                first_name="Updated",
            ),
            now=NOW,
        )
        assert updated.revision.value == 2
        assert updated.first_name == "Updated"

        with pytest.raises(CustomerIdentityServiceError) as caught:
            save_own_customer_identity(
                repository=repository,
                audit_writer=audit,
                crypto_config=_config(),
                command=_command(
                    user.id,
                    expected_revision=1,
                    jshshir="12345678901234",
                ),
                now=NOW,
            )
        assert caught.value.code is ErrorCode.CUSTOMER_IDENTITY_CHANGED
        assert session.scalar(select(1)) == 1
        assert repository.get_identity(customer_id=customer.id).revision.value == 2

    with Session(m2_test_database) as verification:
        events = tuple(verification.scalars(select(AuditLog)))
        assert len(events) == 2
        outcomes = {
            event.payload["revision"]: event.payload["created_or_updated"]
            for event in events
        }
        assert outcomes == {1: "created", 2: "updated"}
        assert all(
            set(event.payload)
            == {
                "revision",
                "created_or_updated",
                "document_type",
            }
            for event in events
        )


def test_duplicate_other_customer_is_safe_and_cross_user_cannot_select_target(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        first_user, first_customer = _user_customer(
            session,
            phone="+998900001402",
        )
        second_user, second_customer = _user_customer(
            session,
            phone="+998900001403",
        )
        repository = SqlAlchemyCustomerIdentityRepository(session)
        audit = SqlAlchemyAuditWriter(session)
        save_own_customer_identity(
            repository=repository,
            audit_writer=audit,
            crypto_config=_config(),
            command=_command(
                first_user.id,
                expected_revision=0,
                jshshir="22345678901234",
            ),
            now=NOW,
        )

        with pytest.raises(CustomerIdentityServiceError) as caught:
            save_own_customer_identity(
                repository=repository,
                audit_writer=audit,
                crypto_config=_config(),
                command=_command(
                    second_user.id,
                    expected_revision=0,
                    jshshir="22345678901234",
                ),
                now=NOW,
            )
        assert caught.value.code is ErrorCode.DUPLICATE_JSHSHIR
        assert caught.value.__cause__ is None
        assert repository.get_identity(customer_id=first_customer.id) is not None
        assert repository.get_identity(customer_id=second_customer.id) is None
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert session.scalar(select(1)) == 1


def test_audit_failure_and_outer_failure_roll_back_identity_mutation(
    m2_test_database: Engine,
) -> None:
    class FailingAuditWriter:
        def append(self, *, event) -> None:
            _ = event
            raise RuntimeError("synthetic audit failure")

    user_id: UUID
    customer_id: UUID
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        with Session(m2_test_database) as session, session.begin():
            user, customer = _user_customer(session, phone="+998900001404")
            user_id = user.id
            customer_id = customer.id
            save_own_customer_identity(
                repository=SqlAlchemyCustomerIdentityRepository(session),
                audit_writer=FailingAuditWriter(),
                crypto_config=_config(),
                command=_command(
                    user.id,
                    expected_revision=0,
                    jshshir="32345678901234",
                ),
                now=NOW,
            )

    with Session(m2_test_database) as verification:
        assert verification.get(User, user_id) is None
        assert verification.get(Customer, customer_id) is None
        assert (
            verification.scalar(select(func.count()).select_from(CustomerIdentity)) == 0
        )
        assert verification.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_completeness_fails_closed_for_ciphertext_and_blind_tamper(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, customer = _user_customer(session, phone="+998900001405")
        repository = SqlAlchemyCustomerIdentityRepository(session)
        save_own_customer_identity(
            repository=repository,
            audit_writer=SqlAlchemyAuditWriter(session),
            crypto_config=_config(),
            command=_command(
                user.id,
                expected_revision=0,
                jshshir="42345678901234",
            ),
            now=NOW,
        )
        assert CustomerIdentityCompletenessService(
            repository=repository,
            crypto_config=_config(),
        )(customer_id=customer.id)
        customer_id = customer.id

    with Session(m2_test_database) as session, session.begin():
        row = session.get(CustomerIdentity, customer_id)
        row.ciphertext = row.ciphertext[:-1] + bytes((row.ciphertext[-1] ^ 1,))

    with Session(m2_test_database) as session:
        assert not CustomerIdentityCompletenessService(
            repository=SqlAlchemyCustomerIdentityRepository(session),
            crypto_config=_config(),
        )(customer_id=customer_id)

    with Session(m2_test_database) as session, session.begin():
        session.execute(
            update(CustomerIdentity)
            .where(CustomerIdentity.customer_id == customer_id)
            .values(jshshir_blind_index=b"Z" * 32)
        )
    with Session(m2_test_database) as session:
        assert not CustomerIdentityCompletenessService(
            repository=SqlAlchemyCustomerIdentityRepository(session),
            crypto_config=_config(),
        )(customer_id=customer_id)
