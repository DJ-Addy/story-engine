"""The analytics client: schema bootstrap, batched writes, analytical reads.

Thin on purpose. All it adds over :class:`~app.analytics.mcp_client.QueryRunner`
is (a) qualifying table names with the configured database, (b) grouping a mixed
batch of events into one ``INSERT`` per table, and (c) turning a columnar
``QueryResult`` into row dicts for the API layer.

Everything here raises :class:`ClickHouseUnavailable` on failure and nothing
here swallows it — deciding that a failed write is survivable is the recorder's
job (:mod:`app.analytics.recorder`), not the client's. Keeping the swallow in
exactly one place is what makes "non-fatal" auditable rather than a habit
sprinkled across call sites.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from app.analytics.events import AnalyticsEvent, insert_statement
from app.analytics.mcp_client import ClickHouseUnavailable, QueryResult, QueryRunner
from app.analytics.schema import TABLE_NAMES, migration_statements, qualified

logger = logging.getLogger(__name__)


class AnalyticsClient:
    """Story Engine's view of the ClickHouse cluster behind the MCP server."""

    def __init__(self, runner: QueryRunner, database: str) -> None:
        self._runner = runner
        self._database = database
        self._schema_ready = False

    @property
    def database(self) -> str:
        return self._database

    @property
    def runner(self) -> QueryRunner:
        return self._runner

    def table(self, name: str) -> str:
        return qualified(self._database, name)

    # -- schema ------------------------------------------------------------
    async def ensure_schema(self, *, force: bool = False) -> list[str]:
        """Run the idempotent migration; returns the statements executed.

        Cached after the first success so the per-request write path does not
        re-issue four DDL statements before every insert. ``force=True`` is for
        the explicit migration entrypoint, which should always talk to the
        cluster even if this process already believes the schema exists.
        """
        if self._schema_ready and not force:
            return []
        statements = migration_statements(self._database)
        for statement in statements:
            await self._runner.run_query(statement)
        self._schema_ready = True
        return statements

    # -- writes ------------------------------------------------------------
    async def insert_events(self, events: Sequence[AnalyticsEvent]) -> int:
        """Write a mixed batch of events; returns the number of rows inserted.

        Groups by target table so a batch spanning judge, render and cost events
        costs one round trip per table rather than one per event.
        """
        if not events:
            return 0
        await self.ensure_schema()
        by_table: dict[str, list[AnalyticsEvent]] = {}
        for event in events:
            by_table.setdefault(event.TABLE, []).append(event)
        written = 0
        for table, batch in by_table.items():
            statement = insert_statement(self.table(table), batch)
            if statement is None:
                continue
            await self._runner.run_query(statement)
            written += len(batch)
        return written

    # -- reads -------------------------------------------------------------
    async def query(self, sql: str) -> QueryResult:
        return await self._runner.run_query(sql)

    async def rows(self, sql: str) -> list[dict[str, Any]]:
        """Run a ``SELECT`` and return it as row dicts for the API layer."""
        return (await self.query(sql)).dicts()

    async def existing_tables(self) -> list[str]:
        """Which of Story Engine's tables the cluster actually has.

        A cheap liveness + migration check for the status endpoint: it proves
        the MCP server answered *and* that the migration has been applied,
        which are the two things that go wrong in a demo.
        """
        names = ", ".join(f"'{name}'" for name in TABLE_NAMES)
        result = await self.query(
            "SELECT name FROM system.tables "
            f"WHERE database = '{self._database}' AND name IN ({names}) ORDER BY name"
        )
        return [str(row[0]) for row in result.rows if row]

    async def aclose(self) -> None:
        await self._runner.aclose()


async def try_insert(client: AnalyticsClient | None, events: Iterable[AnalyticsEvent]) -> int:
    """Insert, or log and carry on. The one sanctioned swallow of a write error.

    A demo that hard-fails a render because the analytics cluster blinked is
    worse than one with a hole in its dashboard, so unreachable ClickHouse is a
    logged warning and nothing more.
    """
    batch = list(events)
    if client is None or not batch:
        return 0
    try:
        return await client.insert_events(batch)
    except ClickHouseUnavailable as exc:
        logger.warning("analytics write dropped (%d events): %s", len(batch), exc)
    except Exception as exc:  # defensive: never let telemetry break a render
        logger.warning("analytics write failed unexpectedly (%d events): %s", len(batch), exc)
    return 0
