"""``app.db.session``: URL resolution, engine construction, schema helpers.

Everything here is offline and driver-free. ``resolve_database_url`` and
``normalize_url`` are pure string/env logic (``sqlalchemy.engine.make_url``
only parses a URL string - it never imports a DBAPI driver), and
``create_engine_from_url``'s PostgreSQL branch is exercised by monkeypatching
``sqlalchemy.create_engine`` itself so the pool configuration can be asserted
without psycopg being installed (it is not, in this environment, despite
being a listed dependency - which is itself a small proof that
``app.db.session`` stays importable and testable without it, exactly as its
module docstring claims). The SQLite branch is exercised for real, since
SQLite needs no external driver and never touches the network.
"""

from __future__ import annotations

import pytest
import sqlalchemy
from sqlalchemy import text

from app.db import models
from app.db.session import (
    create_engine_from_url,
    create_store_schema,
    normalize_url,
    resolve_database_url,
    should_create_schema,
)

ENV_VARS = (
    "STORY_ENGINE_REPO",
    "DATABASE_URL",
    "INSTANCE_UNIX_SOCKET",
    "CLOUD_SQL_CONNECTION_NAME",
    "DB_USER",
    "DB_PASS",
    "DB_NAME",
    "STORY_ENGINE_DB_CREATE_ALL",
)


@pytest.fixture(autouse=True)
def clean_db_env(monkeypatch: pytest.MonkeyPatch):
    """Every test starts from a blank slate for every env var this module reads."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# normalize_url
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_drivername",
    [
        ("postgres://user:pw@host/db", "postgresql+psycopg"),
        ("postgresql://user:pw@host/db", "postgresql+psycopg"),
        ("postgresql+psycopg2://user:pw@host/db", "postgresql+psycopg"),
        ("postgresql+psycopg://user:pw@host/db", "postgresql+psycopg"),
    ],
)
def test_normalize_url_canonicalizes_postgres_schemes(raw, expected_drivername):
    normalized = normalize_url(raw)
    assert sqlalchemy.make_url(normalized).drivername == expected_drivername


def test_normalize_url_leaves_non_postgres_schemes_alone():
    assert normalize_url("sqlite:///:memory:") == "sqlite:///:memory:"


def test_normalize_url_strips_surrounding_whitespace():
    normalized = normalize_url("  postgres://user:pw@host/db  ")
    assert sqlalchemy.make_url(normalized).drivername == "postgresql+psycopg"


# --------------------------------------------------------------------------
# resolve_database_url
# --------------------------------------------------------------------------


def test_resolve_database_url_none_when_nothing_configured():
    assert resolve_database_url() is None


def test_resolve_database_url_story_engine_repo_memory_forces_none(monkeypatch):
    monkeypatch.setenv("STORY_ENGINE_REPO", "memory")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    assert resolve_database_url() is None


def test_resolve_database_url_story_engine_repo_memory_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("STORY_ENGINE_REPO", "MEMORY")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
    assert resolve_database_url() is None


def test_resolve_database_url_uses_database_url_verbatim_when_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")
    resolved = resolve_database_url()
    assert resolved is not None
    assert sqlalchemy.make_url(resolved).drivername == "postgresql+psycopg"


def test_resolve_database_url_assembles_cloud_sql_socket_url(monkeypatch):
    monkeypatch.setenv("INSTANCE_UNIX_SOCKET", "/cloudsql/proj:region:instance")
    monkeypatch.setenv("DB_USER", "svc")
    monkeypatch.setenv("DB_PASS", "secret")
    monkeypatch.setenv("DB_NAME", "story_engine")

    resolved = resolve_database_url()
    assert resolved is not None
    url = sqlalchemy.make_url(resolved)
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "svc"
    assert url.password == "secret"
    assert url.database == "story_engine"
    assert url.host is None  # unix socket, not TCP
    assert url.query.get("host") == "/cloudsql/proj:region:instance"


def test_resolve_database_url_cloud_sql_connection_name_builds_socket_path(monkeypatch):
    monkeypatch.setenv("CLOUD_SQL_CONNECTION_NAME", "proj:region:instance")
    monkeypatch.setenv("DB_USER", "svc")
    monkeypatch.setenv("DB_NAME", "story_engine")

    resolved = resolve_database_url()
    assert resolved is not None
    assert sqlalchemy.make_url(resolved).query.get("host") == "/cloudsql/proj:region:instance"


def test_resolve_database_url_instance_unix_socket_takes_priority(monkeypatch):
    monkeypatch.setenv("INSTANCE_UNIX_SOCKET", "/cloudsql/explicit")
    monkeypatch.setenv("CLOUD_SQL_CONNECTION_NAME", "proj:region:instance")
    monkeypatch.setenv("DB_USER", "svc")
    monkeypatch.setenv("DB_NAME", "story_engine")

    resolved = resolve_database_url()
    assert sqlalchemy.make_url(resolved).query.get("host") == "/cloudsql/explicit"


@pytest.mark.parametrize(
    "missing",
    ["DB_USER", "DB_NAME"],
)
def test_resolve_database_url_none_when_cloud_sql_socket_incomplete(monkeypatch, missing):
    values = {
        "INSTANCE_UNIX_SOCKET": "/cloudsql/proj:region:instance",
        "DB_USER": "svc",
        "DB_NAME": "story_engine",
    }
    values.pop(missing)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert resolve_database_url() is None


def test_resolve_database_url_prefers_database_url_over_cloud_sql_vars(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://explicit/db")
    monkeypatch.setenv("INSTANCE_UNIX_SOCKET", "/cloudsql/ignored")
    monkeypatch.setenv("DB_USER", "svc")
    monkeypatch.setenv("DB_NAME", "story_engine")

    resolved = resolve_database_url()
    assert sqlalchemy.make_url(resolved).database == "db"


# --------------------------------------------------------------------------
# create_engine_from_url
# --------------------------------------------------------------------------


def test_create_engine_from_url_sqlite_is_actually_usable():
    engine = create_engine_from_url("sqlite:///:memory:")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()


def test_create_engine_from_url_postgres_branch_uses_a_small_tuned_pool(monkeypatch):
    """Exercised by stubbing ``sqlalchemy.create_engine`` itself, since the
    psycopg driver is not installed in this environment - proving the
    lazy-import discipline (``create_engine_from_url`` only imports
    ``create_engine`` inside the function body) holds even when the branch
    that needs a real driver is taken."""
    captured: dict = {}

    def _fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return "sentinel-engine"

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)

    result = create_engine_from_url("postgresql+psycopg://user:pw@/db?host=/cloudsql/x")

    assert result == "sentinel-engine"
    assert captured["kwargs"]["pool_pre_ping"] is True
    assert captured["kwargs"]["pool_size"] == 5
    assert captured["kwargs"]["max_overflow"] == 2
    assert captured["kwargs"]["pool_recycle"] == 1800


def test_create_engine_from_url_sqlite_branch_skips_postgres_pool_kwargs(monkeypatch):
    captured: dict = {}

    def _fake_create_engine(url, **kwargs):
        captured["kwargs"] = kwargs
        return "sentinel-engine"

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)

    create_engine_from_url("sqlite:///:memory:")

    assert "pool_pre_ping" not in captured["kwargs"]
    assert "pool_size" not in captured["kwargs"]


# --------------------------------------------------------------------------
# should_create_schema / create_store_schema
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES"])
def test_should_create_schema_true_values(monkeypatch, value):
    monkeypatch.setenv("STORY_ENGINE_DB_CREATE_ALL", value)
    assert should_create_schema() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "nah"])
def test_should_create_schema_false_values(monkeypatch, value):
    monkeypatch.setenv("STORY_ENGINE_DB_CREATE_ALL", value)
    assert should_create_schema() is False


def test_should_create_schema_false_when_unset():
    assert should_create_schema() is False


def test_create_store_schema_creates_exactly_the_store_tables():
    engine = create_engine_from_url("sqlite:///:memory:")
    try:
        create_store_schema(engine)
        inspector = sqlalchemy.inspect(engine)
        table_names = set(inspector.get_table_names())
        expected = {t.name for t in models.STORE_TABLES}
        assert expected <= table_names
        # The normalized PRD tables use PostgreSQL-only types and must never
        # be attempted here.
        assert "users" not in table_names
        assert "projects" not in table_names
    finally:
        engine.dispose()


def test_create_store_schema_is_idempotent():
    engine = create_engine_from_url("sqlite:///:memory:")
    try:
        create_store_schema(engine)
        create_store_schema(engine)  # must not raise ("already exists")
    finally:
        engine.dispose()
