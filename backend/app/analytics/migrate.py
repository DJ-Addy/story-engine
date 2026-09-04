"""Create the ClickHouse event tables: ``python -m app.analytics.migrate``.

Separate from Alembic on purpose — see :mod:`app.analytics.schema`. Runs the
same idempotent statements the write path would run lazily, but does it once,
loudly, with the SQL printed, so provisioning a fresh ClickHouse Cloud service
is a visible step rather than a side effect of the first judge call.

``--dry-run`` prints the statements without connecting, which is also the only
way to review the DDL when no cluster is available.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app import config  # noqa: F401  # loads backend/.env before env reads
from app.analytics.client import AnalyticsClient
from app.analytics.mcp_client import ClickHouseMCPRunner, ClickHouseUnavailable
from app.analytics.schema import migration_statements
from app.analytics.settings import AnalyticsSettings


async def _apply(settings: AnalyticsSettings) -> int:
    runner = ClickHouseMCPRunner(settings)
    client = AnalyticsClient(runner, settings.database)
    try:
        statements = await client.ensure_schema(force=True)
    except ClickHouseUnavailable as exc:
        print(f"clickhouse unreachable: {exc}", file=sys.stderr)
        return 1
    finally:
        await runner.aclose()
    print(f"applied {len(statements)} statement(s) to {settings.database}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply the Story Engine ClickHouse schema")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the DDL without connecting"
    )
    args = parser.parse_args(argv)

    settings = AnalyticsSettings.from_env()
    if args.dry_run:
        for statement in migration_statements(settings.database):
            print(statement + ";\n")
        return 0
    if not settings.enabled:
        print(
            "analytics is not configured: set CLICKHOUSE_HOST (and user/password) "
            "or use --dry-run",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(_apply(settings))


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
