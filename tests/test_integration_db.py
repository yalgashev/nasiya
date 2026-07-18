import os

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.integration
def test_postgresql_select_one_via_integration_url() -> None:
    db_url = os.getenv("TEST_DATABASE_URL")
    if not db_url:
        pytest.skip(
            "TEST_DATABASE_URL is not set. Set it (for example: "
            "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/"
            "nasiya_test) to run M1 integration DB test."
        )
    if not (db_url.startswith("postgresql://") or db_url.startswith("postgresql+psycopg://")):
        pytest.fail(
            "TEST_DATABASE_URL must point to PostgreSQL for this test "
            "(example: "
            "postgresql+psycopg://nasiya:dev_pass@127.0.0.1:5432/nasiya_test). "
            "SQLite is not supported for integration run."
        )

    engine = create_engine(db_url)
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
    finally:
        engine.dispose()
