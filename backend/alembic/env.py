"""Alembic environment.

The connection URL is not read from alembic.ini. It comes from `db.get_engine()`,
which is the same place the application gets it — including the
`postgresql://` → `postgresql+psycopg://` normalisation that docker-compose's
DATABASE_URL needs. One decision about how to connect, not two that can drift.
"""

from logging.config import fileConfig

import db
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = db.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it — `alembic upgrade head --sql`."""
    context.configure(
        url=str(db.get_engine().url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = db.get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Without this, a changed column type is silently missed by
            # --autogenerate and the migration looks like a no-op.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
