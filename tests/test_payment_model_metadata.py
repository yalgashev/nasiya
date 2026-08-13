from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.schema import UniqueConstraint

from app.db import Base
from app.payment.models import Payment, PaymentVoid

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_payment_model_is_registered_for_runtime_and_alembic() -> None:
    db_source = (PROJECT_ROOT / "app/db.py").read_text(encoding="utf-8")
    env_source = (PROJECT_ROOT / "alembic/env.py").read_text(encoding="utf-8")

    for source in (db_source, env_source):
        assert (
            "from app.payment import models as _payment_models  # noqa: F401" in source
        )


def test_payment_is_one_registered_append_only_m14_table() -> None:
    table = Payment.__table__

    assert Base.metadata.tables["payments"] is table
    assert tuple(table.columns.keys()) == (
        "id",
        "debt_id",
        "recorded_by_user_id",
        "amount_uzs",
        "method",
        "debt_revision_after",
        "created_at",
    )
    assert not {
        "updated_at",
        "status",
        "void",
        "voided_at",
        "note",
        "reference",
        "balance",
        "remaining_due",
        "exposure",
    } & set(table.columns)


def test_payment_columns_have_exact_types_nullability_and_no_defaults() -> None:
    table = Payment.__table__

    for column_name in ("id", "debt_id", "recorded_by_user_id"):
        assert isinstance(table.c[column_name].type, PostgresUUID)
        assert table.c[column_name].nullable is False
    assert isinstance(table.c.amount_uzs.type, Numeric)
    assert table.c.amount_uzs.type.precision == 18
    assert table.c.amount_uzs.type.scale == 0
    assert isinstance(table.c.method.type, Text)
    assert isinstance(table.c.debt_revision_after.type, Integer)
    assert table.c.created_at.type.timezone is True
    assert all(column.default is None for column in table.columns)
    assert all(column.server_default is None for column in table.columns)


def test_payment_constraints_foreign_keys_and_index_policy_are_exact() -> None:
    table = Payment.__table__
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
    primary_key = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint)
    )
    foreign_keys = {
        foreign_key.constraint.name: (foreign_key.target_fullname, foreign_key.ondelete)
        for column in table.columns
        for foreign_key in column.foreign_keys
    }

    assert checks == {
        "ck_payments_amount_uzs_bounds": "amount_uzs BETWEEN 1 AND 1000000000000",
        "ck_payments_method_allowed": (
            "method IN ('cash', 'card', 'transfer', 'other')"
        ),
        "ck_payments_debt_revision_after_positive": "debt_revision_after > 0",
    }
    assert uniques == {
        "uq_payments_debt_id_debt_revision_after": (
            "debt_id",
            "debt_revision_after",
        ),
        "uq_payments_id_debt_id_debt_revision_after": (
            "id",
            "debt_id",
            "debt_revision_after",
        ),
    }
    assert primary_key.name == "pk_payments"
    assert foreign_keys == {
        "fk_payments_debt_id_debts_id": ("debts.id", "RESTRICT"),
        "fk_payments_recorded_by_user_id_users_id": ("users.id", "RESTRICT"),
    }
    assert not table.indexes


def test_payment_repr_redacts_all_persisted_values() -> None:
    identifiers = [UUID(int=value) for value in range(1, 4)]
    created_at = datetime(2026, 8, 9, 10, 11, tzinfo=UTC)
    model = Payment(
        id=identifiers[0],
        debt_id=identifiers[1],
        recorded_by_user_id=identifiers[2],
        amount_uzs=Decimal("987654321"),
        method="transfer",
        debt_revision_after=5,
        created_at=created_at,
    )

    rendered = repr(model)

    for value in (
        *(str(identifier) for identifier in identifiers),
        "987654321",
        "transfer",
        "5",
        repr(created_at),
    ):
        assert value not in rendered
    assert rendered == "Payment(<redacted>)"


def test_payment_void_mapping_matches_exact_m18_child_and_redacts() -> None:
    table = PaymentVoid.__table__
    assert Base.metadata.tables["payment_voids"] is table
    assert tuple(table.columns) == tuple(
        table.c[name]
        for name in (
            "id",
            "payment_id",
            "debt_id",
            "shop_customer_id",
            "source_payment_revision",
            "debt_revision_after",
            "voided_by_user_id",
            "reason",
            "voided_at",
        )
    )
    uniques = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert uniques == {
        "uq_payment_voids_payment_id": ("payment_id",),
        "uq_payment_voids_debt_id_debt_revision_after": (
            "debt_id",
            "debt_revision_after",
        ),
    }
    foreign_keys = {
        constraint.name: (
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys == {
        "fk_payment_voids_payment_debt_revision": (
            ("payments.id", "payments.debt_id", "payments.debt_revision_after"),
            "RESTRICT",
        ),
        "fk_payment_voids_debt_shop_customer": (
            ("debts.id", "debts.shop_customer_id"),
            "RESTRICT",
        ),
        "fk_payment_voids_voided_by_user_id_users_id": (
            ("users.id",),
            "RESTRICT",
        ),
    }
    assert {index.name for index in table.indexes} == {
        "ix_payment_voids_shop_customer_voided_at_id"
    }
    assert repr(PaymentVoid()) == "PaymentVoid(<redacted>)"
