import importlib
import sys
from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import (
    create_database_session_dependency,
    create_database_session_factory,
)

PROBE_TABLE_NAME = "m2_dependency_probe"


@pytest.fixture
def dependency_probe_table(
    m2_test_database: Engine,
) -> Generator[Engine, None, None]:
    assert m2_test_database.url.database is not None
    assert m2_test_database.url.database.endswith("_test")
    assert m2_test_database.url.database != "nasiya"

    with m2_test_database.begin() as connection:
        connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{PROBE_TABLE_NAME}"')
        connection.exec_driver_sql(
            f"""
            CREATE TABLE "{PROBE_TABLE_NAME}" (
                id integer PRIMARY KEY,
                value text NOT NULL
            )
            """
        )

    try:
        yield m2_test_database
    finally:
        with m2_test_database.begin() as connection:
            connection.exec_driver_sql(
                f'DROP TABLE IF EXISTS "{PROBE_TABLE_NAME}"'
            )


def count_probe_rows(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text(f'SELECT count(*) FROM "{PROBE_TABLE_NAME}"')
        ).scalar_one()


def finish_dependency_success(
    session_generator: Generator[Session, None, None],
) -> None:
    with pytest.raises(StopIteration):
        next(session_generator)


@pytest.mark.integration
def test_request_scoped_database_dependency_commits_successful_operations(
    dependency_probe_table: Engine,
) -> None:
    session_factory = create_database_session_factory(dependency_probe_table)
    dependency = create_database_session_dependency(session_factory)
    session_generator = dependency()
    session = next(session_generator)

    session.execute(
        text(f'INSERT INTO "{PROBE_TABLE_NAME}" (id, value) VALUES (1, :value)'),
        {"value": "committed"},
    )
    finish_dependency_success(session_generator)

    assert count_probe_rows(dependency_probe_table) == 1


@pytest.mark.integration
def test_request_scoped_database_dependency_rolls_back_on_exception(
    dependency_probe_table: Engine,
) -> None:
    session_factory = create_database_session_factory(dependency_probe_table)
    dependency = create_database_session_dependency(session_factory)
    session_generator = dependency()
    session = next(session_generator)
    error = RuntimeError("rollback probe")

    session.execute(
        text(f'INSERT INTO "{PROBE_TABLE_NAME}" (id, value) VALUES (1, :value)'),
        {"value": "rolled-back"},
    )

    with pytest.raises(RuntimeError, match="rollback probe"):
        session_generator.throw(error)

    assert count_probe_rows(dependency_probe_table) == 0


@pytest.mark.integration
def test_request_scoped_database_dependency_closes_session(
    dependency_probe_table: Engine,
    monkeypatch,
) -> None:
    session_factory = create_database_session_factory(dependency_probe_table)
    dependency = create_database_session_dependency(session_factory)
    session_generator = dependency()
    session = next(session_generator)
    close_called = False
    original_close = session.close

    def tracking_close() -> None:
        nonlocal close_called
        close_called = True
        original_close()

    monkeypatch.setattr(session, "close", tracking_close)

    finish_dependency_success(session_generator)

    assert close_called is True


def test_app_import_does_not_depend_on_database_existence() -> None:
    sys.modules.pop("app.main", None)

    with patch.object(Engine, "connect") as connect_mock:
        importlib.import_module("app.main")

    connect_mock.assert_not_called()


@pytest.mark.integration
def test_test_database_guard_confirms_development_database_is_not_used(
    m2_test_database: Engine,
) -> None:
    with m2_test_database.connect() as connection:
        current_database = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()

    assert current_database == m2_test_database.url.database
    assert current_database.endswith("_test")
    assert current_database != "nasiya"
