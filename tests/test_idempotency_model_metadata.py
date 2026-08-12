from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CHAR, CheckConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.db import Base
from app.idempotency.models import IdempotencyKey


def test_idempotency_key_is_one_registered_raw_key_free_m14_table() -> None:
    table = IdempotencyKey.__table__

    assert Base.metadata.tables["idempotency_keys"] is table
    assert tuple(table.columns.keys()) == (
        "id",
        "actor_user_id",
        "endpoint",
        "key_digest",
        "request_hash",
        "result_object_type",
        "result_object_id",
        "created_at",
    )
    assert not {
        "key",
        "raw_key",
        "idempotency_key",
        "request_body",
        "payload",
        "updated_at",
        "deleted_at",
        "expires_at",
        "purged_at",
    } & set(table.columns)


def test_idempotency_key_columns_and_aware_timestamp_are_exact() -> None:
    table = IdempotencyKey.__table__

    for column_name in ("id", "actor_user_id", "result_object_id"):
        assert isinstance(table.c[column_name].type, PostgresUUID)
        assert table.c[column_name].nullable is False
    assert isinstance(table.c.endpoint.type, String)
    assert table.c.endpoint.type.length == 100
    for column_name in ("key_digest", "request_hash"):
        assert isinstance(table.c[column_name].type, CHAR)
        assert table.c[column_name].type.length == 64
    assert isinstance(table.c.result_object_type.type, String)
    assert table.c.result_object_type.type.length == 32
    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.server_default is not None
    assert str(table.c.created_at.server_default.arg) == "CURRENT_TIMESTAMP"


def test_idempotency_key_identity_checks_and_actor_foreign_key_are_exact() -> None:
    table = IdempotencyKey.__table__
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
    foreign_key = next(iter(table.c.actor_user_id.foreign_keys))

    assert checks == {
        "ck_idempotency_keys_endpoint_result_pair_allowed": (
            "(endpoint = 'shop.debts.create' AND result_object_type = 'debt') "
            "OR (endpoint = 'shop.debt_payments.create' "
            "AND result_object_type = 'payment') "
            "OR (endpoint = 'shop.risk_band_disclosures.create' "
            "AND result_object_type = 'disclosure_view')"
        ),
        "ck_idempotency_keys_key_digest_sha256_hex": ("key_digest ~ '^[0-9a-f]{64}$'"),
        "ck_idempotency_keys_request_hash_sha256_hex": (
            "request_hash ~ '^[0-9a-f]{64}$'"
        ),
    }
    assert uniques == {
        "uq_idempotency_keys_actor_user_id_endpoint_key_digest": (
            "actor_user_id",
            "endpoint",
            "key_digest",
        )
    }
    assert foreign_key.constraint.name == "fk_idempotency_keys_actor_user_id_users_id"
    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "RESTRICT"
    assert not hasattr(IdempotencyKey, "update")
    assert not hasattr(IdempotencyKey, "delete")


def test_idempotency_pairwise_check_allows_only_the_two_frozen_pairs() -> None:
    pair_check = next(
        str(constraint.sqltext)
        for constraint in IdempotencyKey.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_idempotency_keys_endpoint_result_pair_allowed"
    )

    assert "(endpoint = 'shop.debts.create' AND result_object_type = 'debt')" in (
        pair_check
    )
    assert (
        "(endpoint = 'shop.debt_payments.create' AND result_object_type = 'payment')"
        in pair_check
    )
    for crossed_or_unknown in (
        "(endpoint = 'shop.debts.create' AND result_object_type = 'payment')",
        "(endpoint = 'shop.debt_payments.create' AND result_object_type = 'debt')",
        "shop.payments.create",
        "result_object_type = 'unknown'",
    ):
        assert crossed_or_unknown not in pair_check


def test_idempotency_key_repr_redacts_identity_digest_hash_and_result() -> None:
    identifiers = [UUID(int=value) for value in range(1, 4)]
    model = IdempotencyKey(
        id=identifiers[0],
        actor_user_id=identifiers[1],
        endpoint="shop.debts.create",
        key_digest="a" * 64,
        request_hash="b" * 64,
        result_object_type="debt",
        result_object_id=identifiers[2],
        created_at=datetime(2026, 8, 7, 9, 10, tzinfo=UTC),
    )

    rendered = repr(model)

    for value in (
        *(str(identifier) for identifier in identifiers),
        "shop.debts.create",
        "a" * 64,
        "b" * 64,
        "debt",
    ):
        assert value not in rendered
    assert "<redacted>" in rendered
