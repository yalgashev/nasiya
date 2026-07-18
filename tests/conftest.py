import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from tests.postgresql import (
    cleanup_m2_tables,
    get_alembic_head,
    validate_test_database_url,
)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    raw_url = os.getenv("TEST_DATABASE_URL")
    try:
        url = validate_test_database_url(raw_url)
        get_alembic_head()
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def test_database_engine(test_database_url: str) -> Generator[Engine, None, None]:
    engine = create_engine(test_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def m2_test_database(test_database_engine: Engine) -> Generator[Engine, None, None]:
    cleanup_m2_tables(test_database_engine)
    try:
        yield test_database_engine
    finally:
        cleanup_m2_tables(test_database_engine)
