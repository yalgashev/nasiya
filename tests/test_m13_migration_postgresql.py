from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
M12_REVISION = "e3f4a5b6c7d8"
M13_REVISION = "f4a5b6c7d8e"
M14_REVISION = "a5b6c7d8e9f0"


def _config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def test_m13_is_the_single_linear_child_of_m12() -> None:
    scripts = ScriptDirectory.from_config(_config())
    revision = scripts.get_revision(M13_REVISION)

    assert scripts.get_heads() == [M14_REVISION]
    assert revision is not None
    assert revision.down_revision == M12_REVISION


@pytest.mark.integration
def test_m13_empty_downgrade_restores_m12_and_reupgrades(
    m2_test_database: Engine,
) -> None:
    config = _config()
    try:
        command.upgrade(config, M13_REVISION)
        inspector = inspect(m2_test_database)
        assert {"debts", "idempotency_keys"} <= set(inspector.get_table_names())
        assert "debt_id" in {
            column["name"] for column in inspector.get_columns("offer_acceptances")
        }
        command.downgrade(config, M12_REVISION)
        inspector = inspect(m2_test_database)
        assert {"debts", "idempotency_keys"}.isdisjoint(inspector.get_table_names())
        assert "debt_id" not in {
            column["name"] for column in inspector.get_columns("offer_acceptances")
        }
        with m2_test_database.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == M12_REVISION
            )
    finally:
        command.upgrade(config, M14_REVISION)
