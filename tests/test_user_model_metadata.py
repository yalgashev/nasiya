from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.auth.models import User
from app.db import Base


def test_users_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["users"] is User.__table__


def test_users_table_has_required_columns() -> None:
    columns = User.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "phone",
        "password_hash",
        "is_active",
        "created_at",
        "updated_at",
    }
    assert isinstance(columns["id"].type, PostgresUUID)
    assert columns["id"].primary_key is True
    assert columns["id"].nullable is False
    assert isinstance(columns["phone"].type, String)
    assert columns["phone"].nullable is False
    assert isinstance(columns["password_hash"].type, Text)
    assert columns["password_hash"].nullable is True
    assert isinstance(columns["is_active"].type, Boolean)
    assert columns["is_active"].nullable is False
    assert isinstance(columns["created_at"].type, DateTime)
    assert columns["created_at"].type.timezone is True
    assert columns["created_at"].nullable is False
    assert isinstance(columns["updated_at"].type, DateTime)
    assert columns["updated_at"].type.timezone is True
    assert columns["updated_at"].nullable is False


def test_users_phone_is_unique_and_indexed() -> None:
    phone_column = User.__table__.columns["phone"]

    assert phone_column.unique is True
    assert phone_column.index is True


def test_users_table_has_no_raw_password_column() -> None:
    assert "password" not in User.__table__.columns
