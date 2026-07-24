from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.customer.models import Customer
from app.db import Base


def test_customers_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["customers"] is Customer.__table__


def test_customers_table_has_only_draft_foundation_columns() -> None:
    columns = Customer.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "onboarding_status",
        "created_at",
        "updated_at",
    }


def test_customers_id_is_uuid_primary_key() -> None:
    id_column = Customer.__table__.columns["id"]

    assert isinstance(id_column.type, PostgresUUID)
    assert id_column.primary_key is True
    assert id_column.nullable is False


def test_customers_user_id_is_required_users_foreign_key_with_restrict() -> None:
    user_id_column = Customer.__table__.columns["user_id"]
    foreign_key = next(iter(user_id_column.foreign_keys))

    assert isinstance(user_id_column.type, PostgresUUID)
    assert user_id_column.nullable is False
    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "RESTRICT"


def test_customers_user_id_unique_constraint_is_named() -> None:
    unique_constraints = {
        constraint.name: {column.name for column in constraint.columns}
        for constraint in Customer.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert unique_constraints["uq_customers_user_id"] == {"user_id"}


def test_customers_onboarding_status_is_draft_only() -> None:
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in Customer.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert (
        check_constraints["ck_customers_onboarding_status_draft_only"]
        == "onboarding_status = 'draft'"
    )


def test_customers_timestamps_are_timezone_aware() -> None:
    columns = Customer.__table__.columns

    for column_name in ("created_at", "updated_at"):
        assert isinstance(columns[column_name].type, DateTime)
        assert columns[column_name].type.timezone is True
        assert columns[column_name].nullable is False


def test_customers_table_has_no_pii_activation_or_shop_columns() -> None:
    forbidden_column_markers = {
        "phone",
        "name",
        "fio",
        "fish",
        "jshshir",
        "pinfl",
        "passport",
        "document",
        "telegram",
        "offer",
        "shop",
        "is_active",
    }
    customer_columns = {
        column_name.casefold() for column_name in Customer.__table__.columns.keys()
    }

    assert all(
        marker not in column_name
        for column_name in customer_columns
        for marker in forbidden_column_markers
    )
