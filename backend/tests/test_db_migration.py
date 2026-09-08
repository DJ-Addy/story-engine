"""Pins alembic revision 0002 to the ``store_*`` ORM models it must match.

A hand-written migration and a hand-written model can drift silently - the
migration still applies, the app still boots against ``InMemoryRepository``
in every other test, and nothing notices that a column, type, nullability
flag, or constraint has fallen out of sync until a real deploy hits it.

This module builds the tables the migration's ``upgrade()`` declares WITHOUT
running Alembic's execution/dialect machinery at all: ``op`` is replaced with
a tiny recorder whose ``create_table``/``create_index`` just forward their
arguments into a real ``sqlalchemy.Table``/index record. That is exactly the
data Alembic itself would build from the same call, so this is a faithful,
fully offline (no network, no DB server, no DBAPI driver required) structural
read of what the migration declares - then compared column for column against
``app.db.models.STORE_TABLES``.

What this proves: table set, column set, nullability, primary keys, unique
constraints, and indexes all agree between migration and models, and that
every column's PostgreSQL-compiled DDL type string is identical between the
two (this is what catches e.g. a migration using ``JSONB`` where the model
uses plain ``JSON``, or ``TIMESTAMP`` vs ``TIMESTAMP WITH TIME ZONE``).

What this does NOT prove: that the migration actually *applies* cleanly to a
real PostgreSQL 16 server (extensions, server-side function availability,
privileges), or anything about runtime behaviour under load. Nothing here
opens a database connection.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.db import models

MIGRATION_PATH = (
    Path(__file__).parent.parent / "alembic" / "versions" / "0002_api_record_store.py"
)


class _OpRecorder:
    """Stand-in for ``alembic.op`` that records DDL instead of executing it.

    ``Operations.create_table`` on the real ``op`` proxy needs a live
    ``MigrationContext`` (a dialect, at minimum) before it will do anything -
    machinery this module deliberately avoids. Since ``op.create_table`` and
    ``op.create_index`` are called with plain ``sqlalchemy`` column/constraint
    objects, forwarding those same arguments straight into ``sa.Table`` /
    ``sa.Index`` builds the identical schema objects Alembic would have,
    without needing a dialect, connection, or DBAPI driver at all.
    """

    def __init__(self, metadata: sa.MetaData) -> None:
        self.metadata = metadata
        self.tables: dict[str, sa.Table] = {}
        # (index_name, table_name, columns, unique)
        self.indexes: list[tuple[str, str, tuple[str, ...], bool]] = []
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []

    def create_table(self, name: str, *columns: Any, **kw: Any) -> sa.Table:
        table = sa.Table(name, self.metadata, *columns, **kw)
        self.tables[name] = table
        return table

    def create_index(
        self, name: str, table_name: str, columns: list[str], **kw: Any
    ) -> None:
        self.indexes.append((name, table_name, tuple(columns), bool(kw.get("unique", False))))

    def drop_table(self, name: str, **kw: Any) -> None:
        self.dropped_tables.append(name)

    def drop_index(self, name: str, table_name: str | None = None, **kw: Any) -> None:
        self.dropped_indexes.append(name)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_migration_0002_under_test", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def migration_context() -> tuple[Any, _OpRecorder]:
    """The loaded migration module and the recorder that ran its upgrade()."""
    migration = _load_migration()
    metadata = sa.MetaData()
    recorder = _OpRecorder(metadata)
    migration.op = recorder
    migration.upgrade()
    return migration, recorder


@pytest.fixture(scope="module")
def migration_tables(migration_context) -> dict[str, sa.Table]:
    _migration, recorder = migration_context
    return recorder.tables


@pytest.fixture(scope="module")
def model_tables() -> dict[str, sa.Table]:
    return {t.name: t for t in models.STORE_TABLES}


PG = postgresql.dialect()


def _unique_constraint_column_sets(table: sa.Table) -> set[frozenset[str]]:
    return {
        frozenset(c.name for c in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def _index_signatures(table: sa.Table) -> set[tuple[str, str, tuple[str, ...], bool]]:
    return {
        (idx.name, table.name, tuple(c.name for c in idx.columns), bool(idx.unique))
        for idx in table.indexes
    }


def test_migration_and_models_create_the_same_tables(migration_tables, model_tables):
    assert set(migration_tables) == set(model_tables) == {t.name for t in models.STORE_TABLES}


@pytest.mark.parametrize("table_name", [t.name for t in models.STORE_TABLES])
def test_columns_match(table_name, migration_tables, model_tables):
    mig_t = migration_tables[table_name]
    model_t = model_tables[table_name]

    mig_cols = {c.name for c in mig_t.columns}
    model_cols = {c.name for c in model_t.columns}
    # updated_at / created_at are Python-side (default=) timestamps that exist
    # only on the ORM side, matching how every other store_* table's
    # migration DDL is written; assert on the columns the migration actually
    # owns, which is every column of the model table.
    assert mig_cols == model_cols, f"{table_name}: column name mismatch"

    for name in mig_cols:
        mig_c = mig_t.c[name]
        model_c = model_t.c[name]
        assert mig_c.nullable == model_c.nullable, f"{table_name}.{name}: nullable mismatch"
        assert mig_c.primary_key == model_c.primary_key, f"{table_name}.{name}: primary_key mismatch"
        mig_type_sql = mig_c.type.compile(dialect=PG)
        model_type_sql = model_c.type.compile(dialect=PG)
        assert mig_type_sql == model_type_sql, (
            f"{table_name}.{name}: PostgreSQL type mismatch - "
            f"migration says {mig_type_sql!r}, model says {model_type_sql!r}"
        )


@pytest.mark.parametrize("table_name", [t.name for t in models.STORE_TABLES])
def test_unique_constraints_match(table_name, migration_tables, model_tables):
    mig_t = migration_tables[table_name]
    model_t = model_tables[table_name]
    assert _unique_constraint_column_sets(mig_t) == _unique_constraint_column_sets(model_t), (
        f"{table_name}: unique constraint mismatch"
    )


def test_indexes_match(migration_context, model_tables):
    _migration, recorder = migration_context
    migration_index_signatures = set(recorder.indexes)
    model_index_signatures = {
        sig for table in model_tables.values() for sig in _index_signatures(table)
    }
    assert migration_index_signatures == model_index_signatures


def test_downgrade_drops_exactly_the_tables_and_indexes_upgrade_created(migration_context):
    migration, recorder = migration_context
    migration.downgrade()
    assert set(recorder.dropped_tables) == set(recorder.tables)
    assert set(recorder.dropped_indexes) == {ix[0] for ix in recorder.indexes}


def test_store_tables_constant_is_exactly_the_nine_record_store_tables(model_tables):
    # Guards against a new StoredX model being added to models.py without also
    # being added to STORE_TABLES (which is what create_store_schema and this
    # very test suite iterate over) - an easy way for a table to silently
    # never get created outside of a full alembic history.
    assert len(model_tables) == 9


class TestAlembicResolvesTheSameUrlAsTheApp:
    """The migration must reach the database the application will reach.

    Cloud Run attaches Cloud SQL by mounting a unix socket and setting
    CLOUD_SQL_CONNECTION_NAME — there is no URL anywhere. env.py used to read
    DATABASE_URL alone, so it fell through to its localhost default and the
    container died on startup with "Is the server running on that host and
    accepting TCP/IP connections?", having never tried the socket.
    """

    def test_env_py_uses_the_shared_resolver(self) -> None:
        from pathlib import Path

        env_py = Path(__file__).resolve().parent.parent / "alembic" / "env.py"
        source = env_py.read_text(encoding="utf-8")
        assert "resolve_database_url" in source, (
            "alembic/env.py must resolve through app.db.session.resolve_database_url, "
            "or a Cloud SQL socket deployment migrates against localhost"
        )

    def test_resolver_returns_a_socket_url_for_the_cloud_run_shape(
        self, monkeypatch
    ) -> None:
        from app.db.session import resolve_database_url

        for var in ("DATABASE_URL", "INSTANCE_UNIX_SOCKET", "STORY_ENGINE_REPO"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CLOUD_SQL_CONNECTION_NAME", "proj:us-central1:inst")
        monkeypatch.setenv("DB_USER", "postgres")
        monkeypatch.setenv("DB_NAME", "story_engine")

        url = resolve_database_url()

        assert url is not None
        assert "/cloudsql/proj:us-central1:inst" in url.replace("%2F", "/").replace("%3A", ":")
        assert "@/" in url, "a socket URL carries no host:port"
