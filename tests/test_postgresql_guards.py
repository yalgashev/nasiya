from unittest.mock import MagicMock, Mock

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


def test_m2_cleanup_tables_use_allowlist_with_m13_child_first_order(
    monkeypatch,
) -> None:
    inspector = Mock()
    inspector.get_table_names.return_value = [
        "debt",
        "offer_texts",
        "audit_log",
        "offer_versions",
        "offer_acceptances",
        "object_files",
        "otp_challenges",
        "otp_challenge_events",
        "otp_dispatches",
        "otp_dispatcher_state",
        "shops",
        "telegram_links",
        "shop_status_events",
        "telegram_link_tokens",
        "shop_staff",
        "telegram_link_events",
        "auth_rate_limits",
        "sessions",
        "users",
        "customers",
        "shop_staff_events",
    ]
    monkeypatch.setattr(postgresql, "inspect", lambda _: inspector)

    assert get_m2_cleanup_tables(Mock()) == [
        "otp_challenge_events",
        "otp_dispatches",
        "otp_challenges",
        "offer_acceptances",
        "audit_log",
        "offer_texts",
        "offer_versions",
        "object_files",
        "otp_dispatcher_state",
        "telegram_link_events",
        "telegram_link_tokens",
        "telegram_links",
        "customers",
        "auth_rate_limits",
        "sessions",
        "shop_staff_events",
        "shop_status_events",
        "shop_staff",
        "shops",
        "users",
    ]


def test_cleanup_deletes_object_files_before_users(monkeypatch) -> None:
    engine = MagicMock()
    connection = Mock()
    engine.begin.return_value.__enter__.return_value = connection
    monkeypatch.setattr(
        postgresql,
        "get_m2_cleanup_tables",
        lambda _: ["object_files", "users"],
    )

    postgresql.cleanup_m2_tables(engine)

    assert [call.args[0] for call in connection.exec_driver_sql.call_args_list] == [
        'DELETE FROM "object_files"',
        'DELETE FROM "users"',
    ]


def test_cleanup_is_noop_when_no_m2_tables_are_present(monkeypatch) -> None:
    engine = Mock()
    monkeypatch.setattr(postgresql, "get_m2_cleanup_tables", lambda _: [])

    postgresql.cleanup_m2_tables(engine)

    engine.begin.assert_not_called()
