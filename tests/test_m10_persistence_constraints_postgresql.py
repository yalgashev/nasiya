from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.customer.models import CUSTOMER_ONBOARDING_STATUS_DRAFT, Customer
from app.customer_document.models import CustomerDocument
from app.customer_identity.models import CustomerIdentity
from app.storage.models import ObjectFile, ObjectFileStatus

pytestmark = pytest.mark.integration
NOW = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)


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


def _object(session: Session, *, actor_id: UUID, marker: str) -> ObjectFile:
    object_id = uuid4()
    model = ObjectFile(
        id=object_id,
        bucket="nasiya-private-test",
        object_key=f"v1/objects/{object_id.hex}.png",
        content_type="image/png",
        size_bytes=128,
        checksum_sha256=marker * 64,
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


def _identity(customer_id: UUID, **overrides) -> CustomerIdentity:
    values = {
        "customer_id": customer_id,
        "ciphertext": b"C" * 32,
        "nonce": b"N" * 12,
        "key_id": "identity-v1",
        "schema_version": 1,
        "jshshir_blind_index": b"I" * 32,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerIdentity(**values)


@pytest.mark.parametrize(
    ("overrides", "constraint_name"),
    [
        (
            {"ciphertext": b"short"},
            "ck_customer_identities_ciphertext_minimum_length",
        ),
        ({"nonce": b"N" * 11}, "ck_customer_identities_nonce_length"),
        ({"key_id": "bad/key"}, "ck_customer_identities_key_id_format"),
        (
            {"schema_version": 2},
            "ck_customer_identities_schema_version_supported",
        ),
        (
            {"jshshir_blind_index": b"I" * 31},
            "ck_customer_identities_blind_index_length",
        ),
        ({"revision": 0}, "ck_customer_identities_revision_positive"),
        (
            {"updated_at": NOW - timedelta(seconds=1)},
            "ck_customer_identities_timestamp_order",
        ),
    ],
)
def test_identity_envelope_constraints_fail_and_savepoint_recovers(
    m2_test_database: Engine,
    overrides: dict[str, object],
    constraint_name: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        _, customer = _user_customer(session, phone="+998900001301")
        with pytest.raises(IntegrityError) as caught:
            with session.begin_nested():
                session.add(_identity(customer.id, **overrides))
                session.flush()

        assert caught.value.orig.diag.constraint_name == constraint_name
        assert session.scalar(select(1)) == 1


def _document(
    *,
    customer_id: UUID,
    object_file_id: UUID,
    actor_id: UUID,
    submission_id: UUID | None = None,
    status: str = "CURRENT",
    document_id: UUID | None = None,
    superseded_by_document_id: UUID | None = None,
    superseded_at: datetime | None = None,
) -> CustomerDocument:
    return CustomerDocument(
        id=document_id or uuid4(),
        customer_id=customer_id,
        object_file_id=object_file_id,
        submission_id=submission_id or uuid4(),
        status=status,
        attached_by_user_id=actor_id,
        attached_at=NOW,
        superseded_by_document_id=superseded_by_document_id,
        superseded_at=superseded_at,
    )


@pytest.mark.parametrize(
    ("builder", "constraint_name"),
    [
        (
            lambda values: _document(**values, status="UNKNOWN"),
            "ck_customer_documents_status_allowed",
        ),
        (
            lambda values: _document(
                **values,
                status="CURRENT",
                superseded_by_document_id=uuid4(),
                superseded_at=NOW,
            ),
            "ck_customer_documents_supersede_metadata_matches_status",
        ),
        (
            lambda values: _document(**values, status="SUPERSEDED"),
            "ck_customer_documents_supersede_metadata_matches_status",
        ),
    ],
)
def test_document_lifecycle_constraints_fail_and_savepoint_recovers(
    m2_test_database: Engine,
    builder,
    constraint_name: str,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, customer = _user_customer(session, phone="+998900001302")
        object_file = _object(session, actor_id=user.id, marker="a")
        values = {
            "customer_id": customer.id,
            "object_file_id": object_file.id,
            "actor_id": user.id,
        }
        with pytest.raises(IntegrityError) as caught:
            with session.begin_nested():
                session.add(builder(values))
                session.flush()

        assert caught.value.orig.diag.constraint_name == constraint_name
        assert session.scalar(select(1)) == 1


def test_document_unique_object_submission_current_and_restrictive_fks(
    m2_test_database: Engine,
) -> None:
    with Session(m2_test_database) as session, session.begin():
        user, customer = _user_customer(session, phone="+998900001303")
        _, other_customer = _user_customer(session, phone="+998900001304")
        attached_actor = User(
            phone="+998900001305",
            password_hash=None,
            is_active=True,
            is_platform_admin=False,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(attached_actor)
        session.flush()
        first_object = _object(session, actor_id=user.id, marker="a")
        second_object = _object(session, actor_id=user.id, marker="b")
        third_object = _object(session, actor_id=user.id, marker="c")
        submission_id = uuid4()
        first = _document(
            customer_id=customer.id,
            object_file_id=first_object.id,
            actor_id=attached_actor.id,
            submission_id=submission_id,
        )
        session.add(first)
        session.flush()

        cases = (
            (
                _document(
                    customer_id=other_customer.id,
                    object_file_id=first_object.id,
                    actor_id=attached_actor.id,
                ),
                "uq_customer_documents_object_file_id",
            ),
            (
                _document(
                    customer_id=customer.id,
                    object_file_id=second_object.id,
                    actor_id=attached_actor.id,
                    submission_id=submission_id,
                    status="SUPERSEDED",
                    superseded_by_document_id=first.id,
                    superseded_at=NOW,
                ),
                "uq_customer_documents_customer_id_submission_id",
            ),
            (
                _document(
                    customer_id=customer.id,
                    object_file_id=third_object.id,
                    actor_id=attached_actor.id,
                ),
                "uq_customer_documents_current_customer_id",
            ),
        )
        for candidate, constraint_name in cases:
            with pytest.raises(IntegrityError) as caught:
                with session.begin_nested():
                    session.add(candidate)
                    session.flush()
            assert caught.value.orig.diag.constraint_name == constraint_name
            assert session.scalar(select(1)) == 1

        for statement, expected_constraint in (
            (
                delete(ObjectFile).where(ObjectFile.id == first_object.id),
                "fk_customer_documents_object_file_id_object_files_id",
            ),
            (
                delete(Customer).where(Customer.id == customer.id),
                "fk_customer_documents_customer_id_customers_id",
            ),
            (
                delete(User).where(User.id == attached_actor.id),
                "fk_customer_documents_attached_by_user_id_users_id",
            ),
        ):
            with pytest.raises(IntegrityError) as caught:
                with session.begin_nested():
                    session.execute(statement)
                    session.flush()
            assert caught.value.orig.diag.constraint_name == expected_constraint
            assert session.scalar(select(1)) == 1
