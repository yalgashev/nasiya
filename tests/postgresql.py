from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import URL, Engine, make_url

M2_CLEANUP_TABLE_NAMES = (
    "auth_rate_limits",
    "sessions",
    "users",
)


def validate_test_database_url(raw_url: str | None) -> URL:
    if raw_url is None or not raw_url.strip():
        raise ValueError("TEST_DATABASE_URL is required for PostgreSQL tests")

    url = make_url(raw_url)
    driver_name = url.drivername
    if driver_name.startswith("sqlite"):
        raise ValueError("SQLite is not allowed for PostgreSQL tests")
    if not driver_name.startswith("postgresql"):
        raise ValueError("TEST_DATABASE_URL must use a PostgreSQL driver")
    if url.database is None or not url.database.endswith("_test"):
        raise ValueError("TEST_DATABASE_URL database name must end with _test")
    return url


def get_alembic_head() -> str:
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    script = ScriptDirectory.from_config(Config(str(config_path)))
    head = script.get_current_head()
    if head is None:
        raise ValueError("Alembic head revision is required")
    return head


def get_m2_cleanup_tables(engine: Engine) -> list[str]:
    existing_tables = set(inspect(engine).get_table_names())
    return [
        table_name
        for table_name in M2_CLEANUP_TABLE_NAMES
        if table_name in existing_tables
    ]


def cleanup_m2_tables(engine: Engine) -> None:
    table_names = get_m2_cleanup_tables(engine)
    if not table_names:
        return

    with engine.begin() as connection:
        for table_name in table_names:
            connection.exec_driver_sql(f'DELETE FROM "{table_name}"')
