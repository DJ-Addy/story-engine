"""Database URL resolution and engine construction.

Kept free of any driver import at module scope: ``create_engine`` is what pulls
in psycopg, and it only runs once a URL has actually been configured. That is
what lets ``app.api.deps`` import this module without breaking offline test
collection on a machine with no Postgres driver installed.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from sqlalchemy.engine import URL, make_url

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.engine import Engine

# psycopg 3 is the driver in pyproject; normalize the shorthand schemes people
# actually paste (Cloud SQL consoles and 12-factor tooling emit "postgres://"
# and "postgresql://") onto it so nobody silently ends up on psycopg2.
_PG_SCHEMES = {"postgres", "postgresql", "postgresql+psycopg2"}
_PG_DRIVER = "postgresql+psycopg"


def normalize_url(raw: str) -> str:
    """Canonicalize a database URL onto an installed driver."""
    url = make_url(raw.strip())
    if url.drivername in _PG_SCHEMES:
        url = url.set(drivername=_PG_DRIVER)
    return url.render_as_string(hide_password=False)


def _cloud_sql_socket() -> str | None:
    """The unix socket directory Cloud Run mounts for an attached instance.

    Cloud Run mounts Cloud SQL at ``/cloudsql/<project>:<region>:<instance>``.
    Deployments set either ``INSTANCE_UNIX_SOCKET`` (the full path, the name
    Google's own samples use) or ``CLOUD_SQL_CONNECTION_NAME`` (the bare
    connection name), so accept both.
    """
    socket = os.environ.get("INSTANCE_UNIX_SOCKET", "").strip()
    if socket:
        return socket
    connection_name = os.environ.get("CLOUD_SQL_CONNECTION_NAME", "").strip()
    if connection_name:
        return f"/cloudsql/{connection_name}"
    return None


def resolve_database_url() -> str | None:
    """The configured database URL, or ``None`` to stay in memory.

    Resolution order:

    1. ``STORY_ENGINE_REPO=memory`` forces the in-memory repository, whatever
       else is set. The escape hatch exists because ``backend/.env`` is merged
       into the environment on import, so a developer with a real
       ``DATABASE_URL`` in it would otherwise point local runs at Postgres by
       accident.
    2. ``DATABASE_URL`` verbatim (scheme normalized). A Cloud SQL socket URL of
       the form ``postgresql://user:pass@/dbname?host=/cloudsql/inst`` passes
       through unchanged - the query parameter is how libpq is told to use a
       unix socket instead of TCP.
    3. ``INSTANCE_UNIX_SOCKET`` / ``CLOUD_SQL_CONNECTION_NAME`` plus
       ``DB_USER`` / ``DB_PASS`` / ``DB_NAME``, assembled into that same socket
       URL. This is the shape Cloud Run injects when a Cloud SQL instance is
       attached without anyone hand-writing a URL.
    4. Nothing configured -> ``None``.
    """
    if os.environ.get("STORY_ENGINE_REPO", "").strip().lower() == "memory":
        return None

    raw = os.environ.get("DATABASE_URL", "").strip()
    if raw:
        return normalize_url(raw)

    socket = _cloud_sql_socket()
    database = os.environ.get("DB_NAME", "").strip()
    user = os.environ.get("DB_USER", "").strip()
    if not (socket and database and user):
        return None

    return URL.create(
        drivername=_PG_DRIVER,
        username=user,
        password=os.environ.get("DB_PASS") or None,
        database=database,
        # No host/port: libpq switches to the unix socket named by ?host=.
        query={"host": socket},
    ).render_as_string(hide_password=False)


def create_engine_from_url(url: str, *, echo: bool = False) -> Engine:
    """Build an engine tuned for a small Cloud Run service.

    Cloud Run scales to many short-lived instances against one Cloud SQL
    instance, so the pool is deliberately small and ``pool_pre_ping`` is on:
    connections idle across a scale-to-zero pause get recycled instead of
    surfacing as a 500 on the first request after a cold start.
    """
    from sqlalchemy import create_engine  # local: imports the DBAPI driver

    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return create_engine(url, echo=echo, future=True)

    return create_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_recycle=1800,
    )


def create_store_schema(engine: Engine) -> None:
    """Create the ``store_*`` tables if they are missing.

    Alembic (``alembic upgrade head``) is the supported way to build the schema;
    this is the opt-in belt-and-braces path for a deployment that has not run
    migrations yet, enabled with ``STORY_ENGINE_DB_CREATE_ALL=1``. It touches
    only the record store - the normalized PRD tables need pg extensions and
    enum types that only the migration creates.
    """
    from app.db.base import Base
    from app.db.models import STORE_TABLES

    Base.metadata.create_all(engine, tables=STORE_TABLES, checkfirst=True)


def should_create_schema() -> bool:
    return os.environ.get("STORY_ENGINE_DB_CREATE_ALL", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
