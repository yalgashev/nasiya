import ast
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT
    / "alembic"
    / "versions"
    / "d4e5f6a7b8c9_create_m6_telegram_polling_tables.py"
)


def test_polling_migration_is_single_linear_child_of_verified_m5_head() -> None:
    script = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    revision = script.get_revision("d4e5f6a7b8c9")

    assert revision is not None
    assert revision.down_revision == "a6b4c2d8e9f1"


def test_polling_migration_imports_and_has_only_upgrade_downgrade_entrypoints() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    function_names = {
        node.name for node in module.body if isinstance(node, ast.FunctionDef)
    }

    assert function_names == {"upgrade", "downgrade"}
    assert "create_all" not in source
    assert "DROP DATABASE" not in source
    assert "down -v" not in source


@pytest.mark.integration
def test_polling_migration_downgrade_and_reupgrade_real_postgresql(
    test_database_engine: Engine,
) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    operational_tables = {
        "telegram_polling_state",
        "telegram_update_failures",
    }

    try:
        command.downgrade(config, "a6b4c2d8e9f1")
        assert operational_tables.isdisjoint(
            inspect(test_database_engine).get_table_names()
        )
        command.upgrade(config, "d4e5f6a7b8c9")

        assert operational_tables.issubset(
            inspect(test_database_engine).get_table_names()
        )
    finally:
        command.upgrade(config, "head")
