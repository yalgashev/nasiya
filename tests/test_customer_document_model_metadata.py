from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.customer_document.models import CustomerDocument


def test_customer_document_model_has_exact_concrete_attachment_shape() -> None:
    table = CustomerDocument.__table__

    assert table.name == "customer_documents"
    assert tuple(table.columns.keys()) == (
        "id",
        "customer_id",
        "object_file_id",
        "submission_id",
        "status",
        "attached_by_user_id",
        "attached_at",
        "superseded_by_document_id",
        "superseded_at",
    )
    for column_name in (
        "id",
        "customer_id",
        "object_file_id",
        "submission_id",
        "attached_by_user_id",
        "superseded_by_document_id",
    ):
        assert isinstance(table.c[column_name].type, PostgresUUID)
    assert table.c.status.type.length == 16
    assert table.c.attached_at.type.timezone is True
    assert table.c.superseded_at.type.timezone is True


def test_customer_document_constraints_indexes_and_fks_are_exact() -> None:
    table = CustomerDocument.__table__
    checks = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert checks == {
        "ck_customer_documents_status_allowed",
        "ck_customer_documents_supersede_metadata_matches_status",
        "ck_customer_documents_no_self_replacement",
        "ck_customer_documents_timestamp_order",
    }
    assert uniques == {
        "uq_customer_documents_object_file_id": ("object_file_id",),
        "uq_customer_documents_customer_id_submission_id": (
            "customer_id",
            "submission_id",
        ),
    }
    assert {index.name for index in table.indexes} == {
        "uq_customer_documents_current_customer_id"
    }
    index = next(iter(table.indexes))
    assert index.unique is True
    assert str(index.dialect_options["postgresql"]["where"]) == ("status = 'CURRENT'")
    foreign_keys = {
        foreign_key.constraint.name: (
            foreign_key.target_fullname,
            foreign_key.ondelete,
        )
        for column in table.columns
        for foreign_key in column.foreign_keys
    }
    assert foreign_keys == {
        "fk_customer_documents_customer_id_customers_id": (
            "customers.id",
            "RESTRICT",
        ),
        "fk_customer_documents_object_file_id_object_files_id": (
            "object_files.id",
            "RESTRICT",
        ),
        "fk_customer_documents_attached_by_user_id_users_id": (
            "users.id",
            "RESTRICT",
        ),
        "fk_customer_documents_superseded_by_id_customer_documents": (
            "customer_documents.id",
            "RESTRICT",
        ),
    }
    replacement_fk = next(
        foreign_key for foreign_key in table.c.superseded_by_document_id.foreign_keys
    )
    assert replacement_fk.constraint.deferrable is True
    assert replacement_fk.constraint.initially == "DEFERRED"


def test_customer_document_repr_redacts_every_identity_and_object_uuid() -> None:
    identifiers = [UUID(int=value) for value in range(1, 7)]
    model = CustomerDocument(
        id=identifiers[0],
        customer_id=identifiers[1],
        object_file_id=identifiers[2],
        submission_id=identifiers[3],
        status="SUPERSEDED",
        attached_by_user_id=identifiers[4],
        attached_at=datetime(2026, 8, 1, tzinfo=UTC),
        superseded_by_document_id=identifiers[5],
        superseded_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    rendered = repr(model)

    assert not any(str(identifier) in rendered for identifier in identifiers)
