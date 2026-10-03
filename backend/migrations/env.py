"""Alembic environment.

Wired for online migrations against its own async engine (built
from the settings in `src.configuration.settings`). Imports every
model so `autogenerate` can see them: the core ``Base`` from
``ws_core.db.base`` and the domain models from
``ws_reservation.models`` (the full migration set ships inside the
plugin). The initial migration is hand-written (the partial unique
index's ``postgresql_where`` historically doesn't render reliably in
autogenerate output).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the app's settings module registers the concrete
# Settings with ws-core (the module calls ws_core.config.configure
# at import time), so ws_core.config.get_settings() below resolves.
import src.configuration.settings  # noqa: F401  # registers settings with ws-core
from ws_core.config import get_settings
from ws_core.db.base import Base  # noqa: F401  # import for metadata side effects
from ws_reservation.models import (  # noqa: F401
    IdempotencyKey,
    Organization,
    OrganizationFollow,
    OrganizationMembership,
    Reservation,
    Review,
    User,
    WaitlistEntry,
    Workshop,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a connection)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations against an open connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
