"""Alembic environment for Story Engine.

Resolves the database URL through :func:`app.db.session.resolve_database_url`,
so the migration reaches the same database the application will. Reading
DATABASE_URL alone was not enough: Cloud Run attaches Cloud SQL by mounting a
unix socket and setting CLOUD_SQL_CONNECTION_NAME, with no URL anywhere, and a
migration that only knew DATABASE_URL fell through to its localhost default and
died trying to open TCP to a server that was never there.

Falls back to DATABASE_URL and then to a local default so a developer running
`alembic upgrade head` by hand behaves exactly as before.
"""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import Base
from app.db import models  # noqa: F401  (ensure all tables are registered)
from app.db.session import resolve_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = (
    resolve_database_url()
    or os.environ.get("DATABASE_URL", "").strip()
    or "postgresql+psycopg://localhost/story_engine"
)
# ConfigParser reads % as interpolation syntax, and a Cloud SQL socket URL is
# largely percent-encoding (host=%2Fcloudsql%2F...), so storing it raw raises
# before a connection is ever attempted. Doubling escapes it; configparser
# turns %% back into % when engine_from_config reads the section below.
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to the database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
