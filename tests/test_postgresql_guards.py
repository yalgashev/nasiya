from unittest.mock import Mock

import pytest

from tests import postgresql
from tests.postgresql import (
    get_alembic_head,
    get_m2_cleanup_tables,
    validate_test_database_url,
)


@pytest.mark.parametrize("raw_url", [None, ""])
def test_test_database_url_is_required(raw_url: str | None) -> None:
    with pytest.raises(ValueError, match="TEST_DATABASE_URL is required"):
        validate_test_database_url(raw_url)


def test_sqlite_test_database_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="SQLite is not allowed"):
        validate_test_database_url("sqlite:///test.db")


def test_non_postgresql_test_database_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="must use a PostgreSQL driver"):
        validate_test_database_url("mysql://nasiya:pass@127.0.0.1/nasiya_test")


def test_database_name_must_end_with_test() -> None:
    with pytest.raises(ValueError, match="database name must end with _test"):
        validate_test_database_url(
            "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya"
        )


def test_valid_postgresql_test_database_url_is_accepted() -> None:
    url = validate_test_database_url(
        "postgresql+psycopg://nasiya:pass@127.0.0.1:5432/nasiya_test"
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.database == "nasiya_test"


def test_alembic_head_exists() -> None:
    assert get_alembic_head()


def test_m2_cleanup_tables_use_allowlist(monkeypatch) -> None:
    inspector = Mock()
    inspector.get_table_names.return_value = [
        "debt",
        "auth_rate_limits",
        "sessions",
        "users",
    ]
    monkeypatch.setattr(postgresql, "inspect", lambda _: inspector)

    assert get_m2_cleanup_tables(Mock()) == [
        "auth_rate_limits",
        "sessions",
        "users",
    ]


def test_cleanup_is_noop_when_no_m2_tables_are_present(monkeypatch) -> None:
    engine = Mock()
    monkeypatch.setattr(postgresql, "get_m2_cleanup_tables", lambda _: [])

    postgresql.cleanup_m2_tables(engine)

    engine.begin.assert_not_called()
