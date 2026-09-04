"""DDL for the Story Engine event tables, derived from the event models.

There is no Alembic here on purpose: ClickHouse is not the operational store
(that is Postgres, under ``app/db/``) and these tables are append-only fact
tables with no foreign keys and no back-fill. The migration is therefore a
short, idempotent list of ``CREATE ... IF NOT EXISTS`` statements that is safe
to run on every boot of the demo.

The column list is read off each :class:`~app.analytics.events.AnalyticsEvent`
subclass rather than repeated here, so a field added to an event without a
matching ClickHouse type fails loudly at import-adjacent test time instead of
producing an ``INSERT`` whose arity no longer matches the table.
"""

from __future__ import annotations

import re

from app.analytics.events import EVENT_TYPES, AnalyticsEvent

# Config values (database name) reach SQL as identifiers, where quoting cannot
# save us — so the identifier itself is validated rather than escaped. Anything
# that is not a plain SQL name is a misconfiguration, not an escaping problem.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(name: str, *, what: str = "identifier") -> str:
    """Return ``name`` if it is a bare SQL identifier, else raise ``ValueError``."""
    if not _IDENTIFIER.match(name or ""):
        raise ValueError(f"invalid ClickHouse {what}: {name!r}")
    return name


def qualified(database: str, table: str) -> str:
    """``database.table`` with both halves validated as identifiers."""
    return f"{validate_identifier(database, what='database')}.{validate_identifier(table, what='table')}"


def _columns_sql(event_type: type[AnalyticsEvent]) -> str:
    declared = event_type.COLUMN_TYPES
    fields = event_type.columns()
    names = tuple(name for name, _ in declared)
    if names != fields:
        raise ValueError(
            f"{event_type.__name__}: COLUMN_TYPES {names} does not match fields {fields}"
        )
    return ",\n    ".join(f"{name} {ch_type}" for name, ch_type in declared)


def create_table_sql(event_type: type[AnalyticsEvent], database: str) -> str:
    """``CREATE TABLE IF NOT EXISTS`` for one event type.

    ``MergeTree`` partitioned by month and sorted by the event type's own
    ``ORDER_BY``: every read endpoint in this package filters by ``project_id``
    first, so leading the sort key with it turns each dashboard query into a
    range scan over one project's parts rather than a full table scan.
    """
    order_by = ", ".join(event_type.ORDER_BY)
    return (
        f"CREATE TABLE IF NOT EXISTS {qualified(database, event_type.TABLE)} (\n"
        f"    {_columns_sql(event_type)}\n"
        f") ENGINE = MergeTree\n"
        f"PARTITION BY toYYYYMM(event_time)\n"
        f"ORDER BY ({order_by})"
    )


def create_database_sql(database: str) -> str:
    return f"CREATE DATABASE IF NOT EXISTS {validate_identifier(database, what='database')}"


def migration_statements(database: str) -> list[str]:
    """The full, ordered, idempotent migration for ``database``."""
    return [create_database_sql(database)] + [
        create_table_sql(event_type, database) for event_type in EVENT_TYPES
    ]


TABLE_NAMES: tuple[str, ...] = tuple(event_type.TABLE for event_type in EVENT_TYPES)
