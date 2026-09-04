"""ClickHouse-backed analytics endpoints: casting history, previz trend, spend.

These are the read half of the analytics spine. Every one of them answers a
question the operational repository structurally cannot — it stores current
state, not history — so the SQL runs against the append-only event tables in
ClickHouse, reached through the official ``mcp-clickhouse`` MCP server (see
:mod:`app.analytics.mcp_client`).

Two deliberate shapes:

* **Panels, not bespoke models.** Every endpoint returns
  :class:`AnalyticsPanel` — the question, the column list, and row dicts as
  ClickHouse returned them. Re-declaring each result set as a Pydantic model
  would duplicate the ``SELECT`` list in a second place that can silently drift
  from it, and would buy nothing: these feed charts and tables, not business
  logic. The columns each panel produces are documented on its endpoint.
* **Degrade, never 500.** An unreachable cluster returns ``available=false``
  with the reason and no rows, at HTTP 200. The same judgement as the write
  path: a dashboard with a gap beats a demo that errors.

Reads flush the pending write buffer first, so a panel opened straight after a
judge run includes that run instead of showing it a few seconds late.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.analytics import queries
from app.analytics.client import AnalyticsClient
from app.analytics.events import AnalyticsEvent
from app.analytics.mcp_client import ClickHouseUnavailable
from app.analytics.recorder import EventRecorder, get_recorder
from app.analytics.schema import TABLE_NAMES
from app.analytics.settings import AnalyticsSettings
from app.api.deps import get_owned_project
from app.api.repo import ProjectRecord

logger = logging.getLogger(__name__)

# The recorder's lifecycle (build at startup, final flush + close the MCP session
# at shutdown) belongs to the app factory's lifespan in app.api.main: a custom
# lifespan replaces Starlette's default one, which is what would otherwise run a
# router-level ``on_shutdown``, so registering the teardown here would be dead
# code that only looks like it runs.
router = APIRouter(prefix="/projects/{project_id}/analytics", tags=["analytics"])


class AnalyticsPanel(BaseModel):
    """One answered question: its columns and rows, or why there are none."""

    question: str
    available: bool = True
    detail: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class AnalyticsStatus(BaseModel):
    """Is the ClickHouse spine configured, reachable and migrated?"""

    configured: bool
    reachable: bool
    detail: str | None = None
    database: str
    transport: str
    host: str
    tables_expected: list[str]
    tables_present: list[str]
    buffer: dict[str, int | bool]


class AnalyticsDashboard(BaseModel):
    """Every panel in one request — the shape the demo UI loads."""

    project_id: str
    available: bool
    detail: str | None = None
    panels: list[AnalyticsPanel]


def get_analytics_recorder() -> EventRecorder:
    """Dependency seam so tests can install a recorder over a fake runner.

    The one seam for both halves of the spine: the read endpoints below depend
    on it, and so do the write call sites in the judge and render routers, so a
    single ``dependency_overrides`` entry redirects the whole subsystem at a
    double.
    """
    return get_recorder()


def emit(recorder: EventRecorder, events: Iterable[AnalyticsEvent]) -> int:
    """Buffer events from a request path; returns how many were accepted.

    ``EventRecorder.record`` already promises never to raise and never to block,
    so this adds nothing in production. It exists because that promise is a
    contract of *one* implementation: a test double, a future recorder or a
    half-built event must not be able to turn a successful render into a 500.
    Telemetry is never worth the request that produced it.
    """
    try:
        return recorder.record(events)
    except Exception as exc:  # pragma: no cover - record() is already non-fatal
        logger.warning("analytics emission failed: %s", exc)
        return 0


async def _panel(
    recorder: EventRecorder, question: str, sql: str
) -> AnalyticsPanel:
    """Run one query, flushing buffered writes first; never raise."""
    client: AnalyticsClient | None = recorder.client
    if client is None:
        return AnalyticsPanel(
            question=question,
            available=False,
            detail="ClickHouse analytics is not configured (set CLICKHOUSE_HOST)",
        )
    try:
        await recorder.flush()
        result = await client.query(sql)
    except ClickHouseUnavailable as exc:
        logger.warning("analytics panel unavailable (%s): %s", question, exc)
        return AnalyticsPanel(question=question, available=False, detail=str(exc))
    except Exception as exc:  # defensive: a dashboard must not take the API down
        logger.warning("analytics panel failed (%s): %s", question, exc)
        return AnalyticsPanel(question=question, available=False, detail=str(exc))
    return AnalyticsPanel(question=question, columns=result.columns, rows=result.dicts())


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
@router.get("/status", response_model=AnalyticsStatus)
async def analytics_status(
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsStatus:
    """Configuration, liveness and migration state, plus write-buffer counters.

    ``tables_present`` comes from ``system.tables``, so a green status proves
    the MCP server answered a real query against the real cluster — not merely
    that credentials are set.
    """
    settings = AnalyticsSettings.from_env()
    client = recorder.client
    reachable = False
    present: list[str] = []
    detail: str | None = None
    if client is None:
        detail = "ClickHouse analytics is not configured (set CLICKHOUSE_HOST)"
    else:
        try:
            present = await client.existing_tables()
            reachable = True
        except Exception as exc:
            detail = str(exc)
    return AnalyticsStatus(
        configured=client is not None,
        reachable=reachable,
        detail=detail,
        database=client.database if client is not None else settings.database,
        transport=settings.transport,
        host=settings.host,
        tables_expected=list(TABLE_NAMES),
        tables_present=present,
        buffer=recorder.stats(),
    )


# --------------------------------------------------------------------------- #
# Casting
# --------------------------------------------------------------------------- #
@router.get("/voice-leaderboard", response_model=AnalyticsPanel)
async def voice_leaderboard(
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsPanel:
    """Best-fitting voice per character across every judged casting variant.

    Columns: ``character_name, voice_name, voice_id, judgements, avg_score,
    best_score, latest_score, speaks_share, lines_judged, warnings, errors,
    last_judged_at``.
    """
    question = "Which voice fits each character best, across every variant judged?"
    if recorder.client is None:
        return await _panel(recorder, question, "")
    return await _panel(
        recorder,
        question,
        queries.voice_leaderboard_sql(recorder.client.database, project.id, limit),
    )


@router.get("/voice-trend", response_model=AnalyticsPanel)
async def voice_trend(
    character: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsPanel:
    """Voice-fit scores in time order with a 5-point rolling mean per character.

    Columns: ``event_time, run_id, mode, candidate_label, candidate_rank,
    character_name, voice_name, score, rolling_avg``.
    """
    if recorder.client is None:
        return await _panel(recorder, "How has casting scored over time?", "")
    return await _panel(
        recorder,
        "How has casting scored over time?",
        queries.voice_trend_sql(recorder.client.database, project.id, character, limit),
    )


# --------------------------------------------------------------------------- #
# Previz
# --------------------------------------------------------------------------- #
@router.get("/animatic-trend", response_model=AnalyticsPanel)
async def animatic_trend(
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsPanel:
    """Per scene: first vs latest animatic score, and the four quality axes.

    Columns: ``scene_ordinal, judgements, avg_score, first_score, latest_score,
    delta, avg_coverage, avg_continuity, avg_variety, avg_pacing, max_shots,
    warnings, errors, last_judged_at``.
    """
    if recorder.client is None:
        return await _panel(recorder, "Which scenes improved and which regressed?", "")
    return await _panel(
        recorder,
        "Which scenes improved and which regressed?",
        queries.animatic_trend_sql(recorder.client.database, project.id, limit),
    )


@router.get("/bake-offs", response_model=AnalyticsPanel)
async def bake_offs(
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsPanel:
    """Every ranking run: winner, margin over the runner-up, candidate count.

    Columns: ``run_id, judge, ran_at, candidates, winner, winner_score,
    runner_up_score, margin``.
    """
    if recorder.client is None:
        return await _panel(recorder, "Which variant won each bake-off?", "")
    return await _panel(
        recorder,
        "Which variant won each bake-off?",
        queries.bake_offs_sql(recorder.client.database, project.id, limit),
    )


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #
@router.get("/spend", response_model=AnalyticsPanel)
async def spend_by_provider(
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsPanel:
    """Cost, output and latency per provider/model.

    Columns: ``provider, kind, model, renders, cost_cents, estimated_cents,
    output_ms, avg_latency_ms, p95_latency_ms``.
    """
    if recorder.client is None:
        return await _panel(recorder, "What did each provider cost and deliver?", "")
    return await _panel(
        recorder,
        "What did each provider cost and deliver?",
        queries.spend_by_provider_sql(recorder.client.database, project.id, limit),
    )


@router.get("/spend-by-scene", response_model=AnalyticsPanel)
async def spend_by_scene(
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsPanel:
    """Spend and output per scene.

    Columns: ``scene_ordinal, audio_renders, video_renders, cost_cents,
    output_ms, providers, last_render_at``.
    """
    if recorder.client is None:
        return await _panel(recorder, "Where did the money go, scene by scene?", "")
    return await _panel(
        recorder,
        "Where did the money go, scene by scene?",
        queries.spend_by_scene_sql(recorder.client.database, project.id, limit),
    )


@router.get("/cost-pressure", response_model=AnalyticsPanel)
async def cost_pressure(
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsPanel:
    """Cost-governor decisions including refusals, and remaining cap headroom.

    Columns: ``operation, provider, decisions, blocked, approved_cents,
    refused_cents, min_headroom_cents, cap_cents, peak_spent_cents,
    last_decision_at``.
    """
    if recorder.client is None:
        return await _panel(recorder, "How close to the cost cap did we get?", "")
    return await _panel(
        recorder,
        "How close to the cost cap did we get?",
        queries.cost_pressure_sql(recorder.client.database, project.id, limit),
    )


# --------------------------------------------------------------------------- #
# Everything at once
# --------------------------------------------------------------------------- #
@router.get("/dashboard", response_model=AnalyticsDashboard)
async def dashboard(
    limit: int = Query(default=queries.DEFAULT_LIMIT, ge=1, le=queries.MAX_LIMIT),
    project: ProjectRecord = Depends(get_owned_project),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnalyticsDashboard:
    """All panels in one round trip — one request backs the whole demo view."""
    client = recorder.client
    if client is None:
        empty = await _panel(recorder, "ClickHouse analytics", "")
        return AnalyticsDashboard(
            project_id=project.id, available=False, detail=empty.detail, panels=[]
        )
    database = client.database
    specs = [
        (
            "Which voice fits each character best, across every variant judged?",
            queries.voice_leaderboard_sql(database, project.id, limit),
        ),
        (
            "Which scenes improved and which regressed?",
            queries.animatic_trend_sql(database, project.id, limit),
        ),
        (
            "Which variant won each bake-off?",
            queries.bake_offs_sql(database, project.id, limit),
        ),
        (
            "What did each provider cost and deliver?",
            queries.spend_by_provider_sql(database, project.id, limit),
        ),
        (
            "Where did the money go, scene by scene?",
            queries.spend_by_scene_sql(database, project.id, limit),
        ),
        (
            "How close to the cost cap did we get?",
            queries.cost_pressure_sql(database, project.id, limit),
        ),
    ]
    panels = [await _panel(recorder, question, sql) for question, sql in specs]
    unavailable = next((p for p in panels if not p.available), None)
    return AnalyticsDashboard(
        project_id=project.id,
        available=unavailable is None,
        detail=unavailable.detail if unavailable is not None else None,
        panels=panels,
    )
