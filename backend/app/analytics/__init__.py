"""ClickHouse analytics spine, reached through the official ``mcp-clickhouse`` server.

Layering, outermost first:

``recorder``     non-fatal buffered writes — what the API routers call
``client``       schema bootstrap, batched inserts, row-dict reads
``mcp_client``   the MCP transport (stdio child process or streamable HTTP)
``queries``      the analytical SQL the dashboard endpoints run
``events``       the event models and their SQL rendering
``schema``       DDL derived from those models
``settings``     environment configuration

Nothing above ``mcp_client`` knows the transport exists, and ``mcp_client`` is
the only module that imports the ``mcp`` SDK — lazily, inside the session
factories — so the package imports and the test suite collects with no MCP
package installed and no network.
"""

from __future__ import annotations

from app.analytics.client import AnalyticsClient, try_insert
from app.analytics.events import (
    AnalyticsEvent,
    CostEvent,
    JudgeScoreEvent,
    RenderEvent,
    animatic_events,
    new_run_id,
    ranking_events,
    voice_fit_events,
)
from app.analytics.mcp_client import (
    ClickHouseMCPRunner,
    ClickHouseUnavailable,
    QueryResult,
    QueryRunner,
)
from app.analytics.recorder import (
    EventRecorder,
    build_recorder,
    get_recorder,
    set_recorder,
    shutdown_recorder,
)
from app.analytics.settings import AnalyticsSettings

__all__ = [
    "AnalyticsClient",
    "AnalyticsEvent",
    "AnalyticsSettings",
    "ClickHouseMCPRunner",
    "ClickHouseUnavailable",
    "CostEvent",
    "EventRecorder",
    "JudgeScoreEvent",
    "QueryResult",
    "QueryRunner",
    "RenderEvent",
    "animatic_events",
    "build_recorder",
    "get_recorder",
    "new_run_id",
    "ranking_events",
    "set_recorder",
    "shutdown_recorder",
    "try_insert",
    "voice_fit_events",
]
