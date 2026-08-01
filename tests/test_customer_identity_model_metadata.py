from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.customer_identity.models import CustomerIdentity


def test_customer_identity_model_is_exact_one_to_one_encrypted_shape() -> None:
    table = CustomerIdentity.__table__

    assert table.name == "customer_identities"
    assert tuple(table.columns) == (
        table.c.customer_id,
        table.c.ciphertext,
        table.c.nonce,
        table.c.key_id,
        table.c.schema_version,
        table.c.jshshir_blind_index,
        table.c.revision,
        table.c.created_at,
        table.c.updated_at,
    )
    assert isinstance(table.c.customer_id.type, PostgresUUID)
    assert table.c.customer_id.primary_key is True
    assert isinstance(table.c.ciphertext.type, BYTEA)
    assert isinstance(table.c.nonce.type, BYTEA)
    assert table.c.key_id.type.length == 64
    assert table.c.schema_version.server_default.arg.text == "1"
    assert isinstance(table.c.jshshir_blind_index.type, BYTEA)
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert not {
        "first_name",
        "last_name",
        "middle_name",
        "jshshir",
        "document_number",
    } & set(table.columns.keys())


def test_customer_identity_constraints_are_named_and_exact() -> None:
    table = CustomerIdentity.__table__
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert set(checks) == {
        "ck_customer_identities_ciphertext_minimum_length",
        "ck_customer_identities_nonce_length",
        "ck_customer_identities_key_id_format",
        "ck_customer_identities_schema_version_supported",
        "ck_customer_identities_blind_index_length",
        "ck_customer_identities_revision_positive",
        "ck_customer_identities_timestamp_order",
    }
    assert "octet_length(ciphertext) >= 16" in checks.values()
    assert "octet_length(nonce) = 12" in checks.values()
    assert "octet_length(jshshir_blind_index) = 32" in checks.values()
    assert uniques == {
        "uq_customer_identities_jshshir_blind_index": ("jshshir_blind_index",)
    }
    foreign_key = next(iter(table.c.customer_id.foreign_keys))
    assert foreign_key.constraint.name == (
        "fk_customer_identities_customer_id_customers_id"
    )
    assert foreign_key.target_fullname == "customers.id"
    assert foreign_key.ondelete == "RESTRICT"


def test_customer_identity_repr_redacts_all_sensitive_values() -> None:
    customer_id = UUID("11111111-1111-1111-1111-111111111111")
    model = CustomerIdentity(
        customer_id=customer_id,
        ciphertext=b"C" * 32,
        nonce=b"N" * 12,
        key_id="identity-v1",
        schema_version=1,
        jshshir_blind_index=b"I" * 32,
        revision=1,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    rendered = repr(model)

    for sensitive in (
        str(customer_id),
        (b"C" * 32).hex(),
        (b"N" * 12).hex(),
        "identity-v1",
        (b"I" * 32).hex(),
    ):
        assert sensitive not in rendered
