from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.audit.repository import SqlAlchemyAuditWriter
from app.auth.error_codes import ErrorCode
from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_identity.contracts import CustomerIdentityActor, SaveCustomerIdentity
from app.customer_identity.crypto import (
    CustomerIdentityAesKey,
    CustomerIdentityBlindIndexKey,
    CustomerIdentityCryptoConfig,
    CustomerIdentityKeyId,
)
from app.customer_identity.models import CustomerIdentity
from app.customer_identity.repository import SqlAlchemyCustomerIdentityRepository
from app.customer_identity.service import (
    CustomerIdentityServiceError,
    save_own_customer_identity,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 17, 0, tzinfo=UTC)


def _config() -> CustomerIdentityCryptoConfig:
    key_id = CustomerIdentityKeyId("identity-v1")
    return CustomerIdentityCryptoConfig(
        active_key_id=key_id,
        encryption_keys={key_id: CustomerIdentityAesKey.from_bytes(bytes(range(32)))},
        blind_index_key=CustomerIdentityBlindIndexKey.from_bytes(
            bytes(reversed(range(32)))
        ),
    )


def _setup(engine: Engine) -> tuple[UUID, UUID]:
    with Session(engine) as session, session.begin():
        user = User(
            phone="+998900001406",
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


def _command(
    actor_id: UUID,
    *,
    expected_revision: int,
    first_name: str,
) -> SaveCustomerIdentity:
    return SaveCustomerIdentity(
        actor=CustomerIdentityActor(actor_id),
        expected_revision=expected_revision,
        first_name=first_name,
        last_name="Specimen",
        middle_name=None,
        jshshir="52345678901234",
        document_type="PASSPORT",
        document_number="AA 12345",
    )


def test_parallel_create_and_update_each_have_one_audited_winner(
    m2_test_database: Engine,
) -> None:
    actor_id, customer_id = _setup(m2_test_database)

    def race(*, expected_revision: int, names: tuple[str, str]) -> tuple[str, str]:
        barrier = Barrier(2)

        def worker(first_name: str) -> str:
            with Session(m2_test_database) as session, session.begin():
                barrier.wait(timeout=10)
                try:
                    save_own_customer_identity(
                        repository=SqlAlchemyCustomerIdentityRepository(session),
                        audit_writer=SqlAlchemyAuditWriter(session),
                        crypto_config=_config(),
                        command=_command(
                            actor_id,
                            expected_revision=expected_revision,
                            first_name=first_name,
                        ),
                        now=NOW,
                    )
                except CustomerIdentityServiceError as exc:
                    assert exc.code is ErrorCode.CUSTOMER_IDENTITY_CHANGED
                    return "changed"
                return "saved"

        with ThreadPoolExecutor(max_workers=2) as executor:
            return tuple(executor.map(worker, names))

    assert sorted(race(expected_revision=0, names=("First", "Second"))) == [
        "changed",
        "saved",
    ]
    assert sorted(race(expected_revision=1, names=("Third", "Fourth"))) == [
        "changed",
        "saved",
    ]

    with Session(m2_test_database) as session:
        row = session.get(CustomerIdentity, customer_id)
        assert row is not None
        assert row.revision == 2
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 2


def test_parallel_cross_customer_jshshir_duplicate_has_one_safe_winner(
    m2_test_database: Engine,
) -> None:
    actor_ids: list[UUID] = []
    with Session(m2_test_database) as session, session.begin():
        for phone in ("+998900001407", "+998900001408"):
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
            session.add(
                Customer(
                    user_id=user.id,
                    onboarding_status=CUSTOMER_ONBOARDING_STATUS_DRAFT,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            actor_ids.append(user.id)

    barrier = Barrier(2)

    def worker(actor_id: UUID) -> str:
        with Session(m2_test_database) as session, session.begin():
            barrier.wait(timeout=10)
            try:
                save_own_customer_identity(
                    repository=SqlAlchemyCustomerIdentityRepository(session),
                    audit_writer=SqlAlchemyAuditWriter(session),
                    crypto_config=_config(),
                    command=_command(
                        actor_id,
                        expected_revision=0,
                        first_name="Parallel",
                    ),
                    now=NOW,
                )
            except CustomerIdentityServiceError as exc:
                assert exc.code is ErrorCode.DUPLICATE_JSHSHIR
                assert exc.__cause__ is None
                assert session.scalar(select(1)) == 1
                return "duplicate"
            return "saved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(worker, actor_ids))

    assert sorted(outcomes) == ["duplicate", "saved"]
    with Session(m2_test_database) as session:
        assert session.scalar(select(func.count()).select_from(CustomerIdentity)) == 1
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 1
