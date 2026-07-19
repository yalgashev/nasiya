from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID

from app.auth.models import AuthRateLimit, User
from app.auth.models import Session as AuthSession
from app.db import Base


def test_users_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["users"] is User.__table__


def test_sessions_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["sessions"] is AuthSession.__table__


def test_auth_rate_limits_table_is_registered_in_base_metadata() -> None:
    assert Base.metadata.tables["auth_rate_limits"] is AuthRateLimit.__table__


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


def test_sessions_table_has_required_columns() -> None:
    columns = AuthSession.__table__.columns

    assert set(columns.keys()) == {
        "id",
        "user_id",
        "token_hash",
        "csrf_secret",
        "user_agent",
        "created_at",
        "last_seen_at",
        "expires_at",
        "revoked_at",
    }
    assert isinstance(columns["id"].type, PostgresUUID)
    assert columns["id"].primary_key is True
    assert columns["id"].nullable is False
    assert isinstance(columns["user_id"].type, PostgresUUID)
    assert columns["user_id"].nullable is True
    assert isinstance(columns["token_hash"].type, String)
    assert columns["token_hash"].type.length == 64
    assert columns["token_hash"].nullable is False
    assert isinstance(columns["csrf_secret"].type, String)
    assert columns["csrf_secret"].type.length == 128
    assert columns["csrf_secret"].nullable is False
    assert isinstance(columns["user_agent"].type, String)
    assert columns["user_agent"].type.length == 512
    assert columns["user_agent"].nullable is True
    for column_name in ("created_at", "last_seen_at", "expires_at", "revoked_at"):
        assert isinstance(columns[column_name].type, DateTime)
        assert columns[column_name].type.timezone is True
    assert columns["created_at"].nullable is False
    assert columns["last_seen_at"].nullable is False
    assert columns["expires_at"].nullable is False
    assert columns["revoked_at"].nullable is True


def test_sessions_token_hash_is_unique_indexed_and_hex_shaped() -> None:
    token_hash_column = AuthSession.__table__.columns["token_hash"]
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in AuthSession.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert token_hash_column.unique is True
    assert token_hash_column.index is True
    assert check_constraints["ck_sessions_token_hash_sha256_hex"] == (
        "token_hash ~ '^[0-9a-f]{64}$'"
    )


def test_sessions_expiration_is_indexed() -> None:
    assert AuthSession.__table__.columns["expires_at"].index is True


def test_sessions_user_foreign_key_cascades_on_user_delete() -> None:
    user_id_column = AuthSession.__table__.columns["user_id"]
    foreign_key = next(iter(user_id_column.foreign_keys))

    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "CASCADE"


def test_sessions_table_has_no_raw_token_cookie_password_or_business_columns() -> None:
    forbidden_columns = {
        "session_token",
        "raw_session_token",
        "token",
        "cookie",
        "cookie_value",
        "password",
        "password_hash",
        "role",
        "mode",
        "shop",
        "customer",
    }

    assert forbidden_columns.isdisjoint(AuthSession.__table__.columns.keys())


def test_auth_rate_limits_table_has_required_columns() -> None:
    columns = AuthRateLimit.__table__.columns

    assert set(columns.keys()) == {
        "scope",
        "key_hash",
        "window_started_at",
        "attempt_count",
        "updated_at",
    }
    assert isinstance(columns["scope"].type, String)
    assert columns["scope"].type.length == 64
    assert columns["scope"].primary_key is True
    assert columns["scope"].nullable is False
    assert isinstance(columns["key_hash"].type, String)
    assert columns["key_hash"].type.length == 64
    assert columns["key_hash"].primary_key is True
    assert columns["key_hash"].nullable is False
    assert isinstance(columns["window_started_at"].type, DateTime)
    assert columns["window_started_at"].type.timezone is True
    assert columns["window_started_at"].nullable is False
    assert isinstance(columns["attempt_count"].type, Integer)
    assert columns["attempt_count"].nullable is False
    assert isinstance(columns["updated_at"].type, DateTime)
    assert columns["updated_at"].type.timezone is True
    assert columns["updated_at"].nullable is False


def test_auth_rate_limits_key_hash_and_attempt_count_are_constrained() -> None:
    primary_key_columns = {
        column.name for column in AuthRateLimit.__table__.primary_key.columns
    }
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in AuthRateLimit.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert primary_key_columns == {"scope", "key_hash"}
    assert check_constraints["ck_auth_rate_limits_key_hash_hmac_sha256_hex"] == (
        "key_hash ~ '^[0-9a-f]{64}$'"
    )
    assert (
        check_constraints["ck_auth_rate_limits_attempt_count_positive"]
        == "attempt_count > 0"
    )


def test_auth_rate_limits_table_has_no_raw_identifier_or_secret_columns() -> None:
    forbidden_columns = {
        "phone",
        "raw_phone",
        "ip",
        "raw_ip",
        "ip_address",
        "password",
        "password_hash",
        "session_token",
        "raw_session_token",
        "token",
        "cookie",
    }

    assert forbidden_columns.isdisjoint(AuthRateLimit.__table__.columns.keys())
