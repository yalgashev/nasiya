import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine


@pytest.mark.integration
def test_postgresql_select_one_via_integration_url(
    m2_test_database: Engine,
) -> None:
    with m2_test_database.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
