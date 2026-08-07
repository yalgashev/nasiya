from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Numeric, SmallInteger, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.db import Base
from app.shop_customer.models import ShopCustomer


def test_shop_customer_is_one_registered_pii_free_tenant_table() -> None:
    table = ShopCustomer.__table__

    assert Base.metadata.tables["shop_customers"] is table
    assert tuple(table.columns.keys()) == (
        "id",
        "shop_id",
        "customer_id",
        "credit_limit_uzs",
        "max_open_debts",
        "list_status",
        "revision",
        "created_by_user_id",
        "created_at",
        "updated_at",
    )
    assert not {
        "phone",
        "name",
        "first_name",
        "last_name",
        "jshshir",
        "document_number",
        "telegram_id",
        "mute",
        "debt",
        "deleted_at",
        "unlinked_at",
        "reason",
        "payload",
        "metadata",
    } & set(table.columns)


def test_shop_customer_column_types_defaults_and_timestamps_are_exact() -> None:
    table = ShopCustomer.__table__

    for column_name in ("id", "shop_id", "customer_id", "created_by_user_id"):
        assert isinstance(table.c[column_name].type, PostgresUUID)
        assert table.c[column_name].nullable is False
    assert isinstance(table.c.credit_limit_uzs.type, Numeric)
    assert table.c.credit_limit_uzs.type.precision == 18
    assert table.c.credit_limit_uzs.type.scale == 0
    assert isinstance(table.c.max_open_debts.type, SmallInteger)
    assert isinstance(table.c.list_status.type, Text)
    assert table.c.list_status.default is not None
    assert table.c.list_status.default.arg == "normal"
    assert table.c.list_status.server_default is not None
    assert str(table.c.list_status.server_default.arg) == "'normal'"
    assert table.c.revision.default is not None
    assert table.c.revision.default.arg == 1
    assert table.c.revision.server_default is not None
    assert str(table.c.revision.server_default.arg) == "1"
    for column_name in ("created_at", "updated_at"):
        assert table.c[column_name].type.timezone is True
        assert table.c[column_name].server_default is not None
        assert str(table.c[column_name].server_default.arg) == "CURRENT_TIMESTAMP"


def test_shop_customer_constraints_indexes_and_fks_are_exact() -> None:
    table = ShopCustomer.__table__
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
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    foreign_keys = {
        foreign_key.constraint.name: (
            foreign_key.target_fullname,
            foreign_key.ondelete,
        )
        for column in table.columns
        for foreign_key in column.foreign_keys
    }

    assert checks == {
        "ck_shop_customers_credit_limit_uzs_bounds": (
            "credit_limit_uzs BETWEEN 0 AND 1000000000000"
        ),
        "ck_shop_customers_max_open_debts_bounds": "max_open_debts BETWEEN 1 AND 100",
        "ck_shop_customers_list_status_allowed": (
            "list_status IN ('normal', 'whitelisted', 'blacklisted')"
        ),
        "ck_shop_customers_revision_positive": "revision > 0",
        "ck_shop_customers_timestamp_order": "updated_at >= created_at",
    }
    assert uniques == {
        "uq_shop_customers_shop_id_customer_id": ("shop_id", "customer_id")
    }
    assert indexes == {
        "ix_shop_customers_shop_id_created_at_id": (
            "shop_id",
            "created_at",
            "id",
        ),
        "ix_shop_customers_customer_id_created_at_id": (
            "customer_id",
            "created_at",
            "id",
        ),
    }
    assert foreign_keys == {
        "fk_shop_customers_shop_id_shops_id": ("shops.id", "RESTRICT"),
        "fk_shop_customers_customer_id_customers_id": (
            "customers.id",
            "RESTRICT",
        ),
        "fk_shop_customers_created_by_user_id_users_id": ("users.id", "RESTRICT"),
    }


def test_shop_customer_repr_redacts_identifiers_and_internal_values() -> None:
    identifiers = [UUID(int=value) for value in range(1, 5)]
    created_at = datetime(2026, 8, 7, 9, 10, tzinfo=UTC)
    updated_at = datetime(2026, 8, 7, 9, 11, tzinfo=UTC)
    model = ShopCustomer(
        id=identifiers[0],
        shop_id=identifiers[1],
        customer_id=identifiers[2],
        credit_limit_uzs=Decimal("987654321"),
        max_open_debts=37,
        list_status="blacklisted",
        revision=47,
        created_by_user_id=identifiers[3],
        created_at=created_at,
        updated_at=updated_at,
    )
    rendered = repr(model)

    for value in (
        *(str(identifier) for identifier in identifiers),
        "987654321",
        "37",
        "blacklisted",
        "47",
        repr(created_at),
        repr(updated_at),
    ):
        assert value not in rendered
    assert rendered == (
        "ShopCustomer(id=<redacted>, shop_id=<redacted>, customer_id=<redacted>, "
        "credit_limit_uzs=<redacted>, max_open_debts=<redacted>, "
        "list_status=<redacted>, revision=<redacted>, "
        "created_by_user_id=<redacted>, created_at=<redacted>, "
        "updated_at=<redacted>)"
    )
