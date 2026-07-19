from sqlalchemy import inspect
from sqlalchemy.engine import Engine


def test_auth_rate_limits_table_exists_with_expected_sql_shape(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)

    assert "auth_rate_limits" in inspector.get_table_names()

    columns = {
        column["name"]: column
        for column in inspector.get_columns("auth_rate_limits")
    }
    assert set(columns) == {
        "scope",
        "key_hash",
        "window_started_at",
        "attempt_count",
        "updated_at",
    }
    assert getattr(columns["scope"]["type"], "length", None) == 64
    assert columns["scope"]["nullable"] is False
    assert getattr(columns["key_hash"]["type"], "length", None) == 64
    assert columns["key_hash"]["nullable"] is False
    assert columns["window_started_at"]["type"].timezone is True
    assert columns["window_started_at"]["nullable"] is False
    assert columns["attempt_count"]["nullable"] is False
    assert columns["updated_at"]["type"].timezone is True
    assert columns["updated_at"]["nullable"] is False


def test_auth_rate_limits_sql_constraints_are_present(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)

    primary_key = inspector.get_pk_constraint("auth_rate_limits")
    check_constraints = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("auth_rate_limits")
    }

    assert primary_key["constrained_columns"] == ["scope", "key_hash"]
    key_hash_constraint = check_constraints[
        "ck_auth_rate_limits_key_hash_hmac_sha256_hex"
    ]
    assert "key_hash" in key_hash_constraint
    assert "~" in key_hash_constraint
    assert "'^[0-9a-f]{64}$'" in check_constraints[
        "ck_auth_rate_limits_key_hash_hmac_sha256_hex"
    ]
    assert (
        check_constraints["ck_auth_rate_limits_attempt_count_positive"]
        == "attempt_count > 0"
    )


def test_auth_rate_limits_table_has_no_raw_identifier_or_secret_sql_columns(
    m2_test_database: Engine,
) -> None:
    inspector = inspect(m2_test_database)
    column_names = {
        column["name"]
        for column in inspector.get_columns("auth_rate_limits")
    }

    assert {
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
    }.isdisjoint(column_names)
