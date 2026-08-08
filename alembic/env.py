import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.audit import models as _audit_models  # noqa: F401
from app.auth import models as _auth_models  # noqa: F401
from app.customer import models as _customer_models  # noqa: F401
from app.customer_document import models as _customer_document_models  # noqa: F401
from app.customer_identity import models as _customer_identity_models  # noqa: F401
from app.db import Base
from app.debt import models as _debt_models  # noqa: F401
from app.idempotency import models as _idempotency_models  # noqa: F401
from app.offers import models as _offer_models  # noqa: F401
from app.otp import models as _otp_models  # noqa: F401
from app.settings import Settings
from app.shop import models as _shop_models  # noqa: F401
from app.shop_customer import models as _shop_customer_models  # noqa: F401
from app.storage import models as _storage_models  # noqa: F401
from app.telegram import models as _telegram_models  # noqa: F401

# This is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if database_url is not None:
        return database_url

    env_file = Path(__file__).resolve().parents[1] / ".env"
    settings = Settings(_env_file=env_file)
    return settings.database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
