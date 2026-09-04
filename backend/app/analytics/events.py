"""Append-only analytics events and their SQL rendering.

Why these three tables and not a generic blob: each one is grounded in a value
the running code already computes and then throws away. ``InMemoryRepository``
keeps only the *latest* judgement, the *latest* render and a single running
spend integer per project — so "which voice actually casts best for MARA across
every variant we tried", "did scene 3's coverage improve or regress", and
"where did the money go" are questions the operational store structurally
cannot answer. They are append-only, high-cardinality and read analytically:
ClickHouse's shape exactly.

Each event class owns its own column types (``COLUMN_TYPES``) in field order,
so :mod:`app.analytics.schema` derives the DDL from the models and the two can
never drift. Values are rendered into SQL literals here because the transport
is the ``mcp-clickhouse`` MCP server, whose ``run_query`` tool takes one SQL
string and offers no parameter binding — every escaping decision therefore has
to be made on this side, once, in :func:`sql_literal`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.judge.model import AnimaticJudgment, RankingResult, VoiceFitResult

# --------------------------------------------------------------------------- #
# SQL literal rendering
# --------------------------------------------------------------------------- #
# ClickHouse string escapes. Backslash first: escaping it after the quote would
# double-escape the backslash we just inserted. Control characters are escaped
# rather than passed through so a character name pasted out of a PDF screenplay
# can never terminate the literal or split the statement.
_STRING_ESCAPES = (
    ("\\", "\\\\"),
    ("'", "\\'"),
    ("\n", "\\n"),
    ("\r", "\\r"),
    ("\t", "\\t"),
    ("\0", "\\0"),
)


def escape_string(value: str) -> str:
    """Escape a Python string for use inside a single-quoted ClickHouse literal."""
    for needle, replacement in _STRING_ESCAPES:
        value = value.replace(needle, replacement)
    return value


def format_datetime(value: datetime) -> str:
    """Render a datetime as ClickHouse ``DateTime64(3)`` text, always in UTC.

    Naive datetimes are *assumed* UTC rather than localised: every producer in
    this package stamps ``datetime.now(UTC)``, and silently applying the host's
    timezone would put a demo machine's clock into the data.
    """
    moment = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return f"{moment:%Y-%m-%d %H:%M:%S}.{moment.microsecond // 1000:03d}"


def sql_literal(value: Any) -> str:
    """Render one Python value as a ClickHouse SQL literal.

    Deliberately strict: an unsupported type raises rather than falling back to
    ``str()``, because a silent ``repr`` of some object is exactly how an
    injection or a corrupt row gets written.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot store non-finite float in ClickHouse: {value!r}")
        return repr(value)
    if isinstance(value, datetime):
        return f"'{format_datetime(value)}'"
    if isinstance(value, str):
        return f"'{escape_string(value)}'"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(sql_literal(item) for item in value) + "]"
    raise TypeError(f"unsupported ClickHouse literal type: {type(value).__name__}")


def new_run_id() -> str:
    """Correlation id shared by every event one API call produces."""
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Base event
# --------------------------------------------------------------------------- #
class AnalyticsEvent(BaseModel):
    """One immutable fact, one row.

    Subclasses declare ``TABLE`` and ``COLUMN_TYPES``; the column order *is* the
    Pydantic field order, which is what makes the derived DDL and the rendered
    ``INSERT`` line up without a mapping layer.
    """

    model_config = ConfigDict(frozen=True)

    TABLE: ClassVar[str] = ""
    COLUMN_TYPES: ClassVar[tuple[tuple[str, str], ...]] = ()
    ORDER_BY: ClassVar[tuple[str, ...]] = ()

    event_time: datetime = Field(default_factory=_now)
    run_id: str = Field(default_factory=new_run_id)
    project_id: str

    @classmethod
    def columns(cls) -> tuple[str, ...]:
        return tuple(cls.model_fields)

    def values(self) -> tuple[Any, ...]:
        return tuple(getattr(self, name) for name in self.columns())

    def values_sql(self) -> str:
        """This event as a ``(...)`` tuple for an ``INSERT ... VALUES`` list."""
        return "(" + ", ".join(sql_literal(value) for value in self.values()) + ")"


_COMMON_COLUMNS: tuple[tuple[str, str], ...] = (
    ("event_time", "DateTime64(3, 'UTC')"),
    ("run_id", "String"),
    ("project_id", "String"),
)


# --------------------------------------------------------------------------- #
# judge_scores
# --------------------------------------------------------------------------- #
class JudgeScoreEvent(AnalyticsEvent):
    """One judged subject: the whole run, one character's voice, or one scene.

    Flattening overall/character/scene into a single table (discriminated by
    ``subject_kind``) rather than three keeps every leaderboard query a single
    scan with no joins, which is how ClickHouse wants to be asked. Unused axes
    are zero rather than nullable: a Float32 zero costs nothing after
    compression and keeps ``avg()`` free of NULL semantics, and every query
    filters on ``subject_kind`` anyway.
    """

    TABLE: ClassVar[str] = "judge_scores"
    ORDER_BY: ClassVar[tuple[str, ...]] = ("project_id", "judge", "subject", "event_time")
    COLUMN_TYPES: ClassVar[tuple[tuple[str, str], ...]] = _COMMON_COLUMNS + (
        ("judge", "LowCardinality(String)"),
        ("mode", "LowCardinality(String)"),
        ("candidate_label", "String"),
        ("candidate_rank", "UInt8"),
        ("subject_kind", "LowCardinality(String)"),
        ("subject", "String"),
        ("score", "Float32"),
        ("coverage_score", "Float32"),
        ("continuity_score", "Float32"),
        ("variety_score", "Float32"),
        ("pacing_score", "Float32"),
        ("voice_id", "String"),
        ("voice_name", "String"),
        ("speaks_share", "Float32"),
        ("line_count", "UInt32"),
        ("shot_count", "UInt32"),
        ("finding_count", "UInt16"),
        ("warn_count", "UInt16"),
        ("error_count", "UInt16"),
        ("grammar_profile", "LowCardinality(String)"),
    )

    judge: str  # "voice_fit" | "animatic"
    mode: str  # "single" | "ranking"
    candidate_label: str = ""
    candidate_rank: int = 0
    subject_kind: str  # "overall" | "character" | "scene"
    subject: str = ""
    score: float
    coverage_score: float = 0.0
    continuity_score: float = 0.0
    variety_score: float = 0.0
    pacing_score: float = 0.0
    voice_id: str = ""
    voice_name: str = ""
    speaks_share: float = 0.0
    line_count: int = 0
    shot_count: int = 0
    finding_count: int = 0
    warn_count: int = 0
    error_count: int = 0
    grammar_profile: str = ""


# --------------------------------------------------------------------------- #
# render_events
# --------------------------------------------------------------------------- #
class RenderEvent(AnalyticsEvent):
    """One completed media render — the audio pipeline or the video provider.

    ``cost_cents`` is what the provider (or the estimator standing in for it)
    actually reports; ``estimated_cost_cents`` is what the cost governor was
    charged before the call. Keeping both is the point: their divergence over a
    project's history is the only way to tell whether the estimator is honest.
    """

    TABLE: ClassVar[str] = "render_events"
    ORDER_BY: ClassVar[tuple[str, ...]] = ("project_id", "kind", "scene_ordinal", "event_time")
    COLUMN_TYPES: ClassVar[tuple[tuple[str, str], ...]] = _COMMON_COLUMNS + (
        ("kind", "LowCardinality(String)"),
        ("scene_ordinal", "UInt32"),
        ("shot_ordinal", "UInt32"),
        ("provider", "LowCardinality(String)"),
        ("model", "LowCardinality(String)"),
        ("source", "LowCardinality(String)"),
        ("status", "LowCardinality(String)"),
        ("duration_ms", "UInt32"),
        ("clip_count", "UInt16"),
        ("cost_cents", "UInt32"),
        ("estimated_cost_cents", "UInt32"),
        ("latency_ms", "UInt32"),
        ("ambience_tags", "Array(String)"),
    )

    kind: str  # "audio" | "video"
    scene_ordinal: int = 0
    shot_ordinal: int = 0
    provider: str = ""
    model: str = ""
    source: str = ""  # video only: "image" | "text"
    status: str = "ok"
    duration_ms: int = 0
    clip_count: int = 0
    cost_cents: int = 0
    estimated_cost_cents: int = 0
    latency_ms: int = 0
    ambience_tags: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# cost_events
# --------------------------------------------------------------------------- #
class CostEvent(AnalyticsEvent):
    """One cost-governor decision, allowed or refused.

    A different grain from ``render_events`` on purpose: the interesting cost
    question is not only what was spent but what was *stopped*. A refusal never
    produces a render row, so cap pressure is invisible unless the decision
    itself is an event. ``headroom_cents`` is signed so a refusal records how
    far over the cap the request would have gone.
    """

    TABLE: ClassVar[str] = "cost_events"
    ORDER_BY: ClassVar[tuple[str, ...]] = ("project_id", "operation", "event_time")
    COLUMN_TYPES: ClassVar[tuple[tuple[str, str], ...]] = _COMMON_COLUMNS + (
        ("operation", "LowCardinality(String)"),
        ("provider", "LowCardinality(String)"),
        ("scene_ordinal", "UInt32"),
        ("shot_ordinal", "UInt32"),
        ("estimated_cents", "UInt32"),
        ("spent_before_cents", "UInt32"),
        ("cap_cents", "UInt32"),
        ("allowed", "UInt8"),
        ("headroom_cents", "Int64"),
    )

    operation: str  # "render_audio" | "render_video"
    provider: str = ""
    scene_ordinal: int = 0
    shot_ordinal: int = 0
    estimated_cents: int = 0
    spent_before_cents: int = 0
    cap_cents: int = 0
    allowed: bool = True
    headroom_cents: int = 0

    @classmethod
    def decide(
        cls,
        *,
        project_id: str,
        run_id: str,
        operation: str,
        provider: str,
        estimated_cents: int,
        spent_before_cents: int,
        cap_cents: int,
        allowed: bool,
        scene_ordinal: int = 0,
        shot_ordinal: int = 0,
    ) -> CostEvent:
        """Build the event from exactly the numbers ``governor.guard`` saw."""
        return cls(
            project_id=project_id,
            run_id=run_id,
            operation=operation,
            provider=provider,
            scene_ordinal=scene_ordinal,
            shot_ordinal=shot_ordinal,
            estimated_cents=estimated_cents,
            spent_before_cents=spent_before_cents,
            cap_cents=cap_cents,
            allowed=allowed,
            headroom_cents=cap_cents - (spent_before_cents + estimated_cents),
        )


EVENT_TYPES: tuple[type[AnalyticsEvent], ...] = (JudgeScoreEvent, RenderEvent, CostEvent)


# --------------------------------------------------------------------------- #
# Projections from judge results onto events
# --------------------------------------------------------------------------- #
def _severity_counts(findings: Sequence[Any]) -> tuple[int, int, int]:
    """(total, warn, error) over any finding list sharing the judge severity vocab."""
    warn = sum(1 for f in findings if f.severity == "warn")
    error = sum(1 for f in findings if f.severity == "error")
    return len(findings), warn, error


def voice_fit_events(
    project_id: str,
    result: VoiceFitResult,
    *,
    run_id: str | None = None,
    mode: str = "single",
    candidate_label: str = "",
    candidate_rank: int = 0,
    at: datetime | None = None,
) -> list[JudgeScoreEvent]:
    """Fan one ``VoiceFitResult`` out into an overall row plus one row per character."""
    common = {
        "project_id": project_id,
        "run_id": run_id or new_run_id(),
        "event_time": at or _now(),
        "judge": "voice_fit",
        "mode": mode,
        "candidate_label": candidate_label,
        "candidate_rank": candidate_rank,
    }
    rows = [
        JudgeScoreEvent(
            **common,
            subject_kind="overall",
            subject="",
            score=result.overall_score,
            line_count=sum(c.line_count for c in result.characters),
        )
    ]
    for fit in result.characters:
        total, warn, error = _severity_counts(fit.findings)
        rows.append(
            JudgeScoreEvent(
                **common,
                subject_kind="character",
                subject=fit.character,
                score=fit.score,
                voice_id=fit.voice_id,
                voice_name=fit.voice_name,
                speaks_share=fit.speaks_share,
                line_count=fit.line_count,
                finding_count=total,
                warn_count=warn,
                error_count=error,
            )
        )
    return rows


def animatic_events(
    project_id: str,
    result: AnimaticJudgment,
    *,
    run_id: str | None = None,
    mode: str = "single",
    candidate_label: str = "",
    candidate_rank: int = 0,
    grammar_profile: str = "",
    at: datetime | None = None,
) -> list[JudgeScoreEvent]:
    """Fan one ``AnimaticJudgment`` out into an overall row plus one row per scene."""
    common = {
        "project_id": project_id,
        "run_id": run_id or new_run_id(),
        "event_time": at or _now(),
        "judge": "animatic",
        "mode": mode,
        "candidate_label": candidate_label,
        "candidate_rank": candidate_rank,
        "grammar_profile": grammar_profile,
    }
    total, warn, error = _severity_counts(result.findings)
    rows = [
        JudgeScoreEvent(
            **common,
            subject_kind="overall",
            subject="",
            score=result.overall_score,
            coverage_score=result.coverage_score,
            continuity_score=result.continuity_score,
            variety_score=result.variety_score,
            pacing_score=result.pacing_score,
            shot_count=sum(s.shot_count for s in result.scenes),
            finding_count=total,
            warn_count=warn,
            error_count=error,
        )
    ]
    for scene in result.scenes:
        s_total, s_warn, s_error = _severity_counts(scene.findings)
        rows.append(
            JudgeScoreEvent(
                **common,
                subject_kind="scene",
                subject=str(scene.scene_ordinal),
                score=scene.score,
                coverage_score=scene.coverage_score,
                continuity_score=scene.continuity_score,
                variety_score=scene.variety_score,
                pacing_score=scene.pacing_score,
                shot_count=scene.shot_count,
                finding_count=s_total,
                warn_count=s_warn,
                error_count=s_error,
            )
        )
    return rows


def ranking_events(
    project_id: str,
    ranking: RankingResult[Any],
    *,
    judge: str,
    run_id: str | None = None,
    grammar_profile: str = "",
    at: datetime | None = None,
) -> list[JudgeScoreEvent]:
    """Fan a whole leaderboard out, tagging every row with its candidate + rank.

    One ``run_id`` across all candidates is what makes "which variant won this
    bake-off, and by how much" answerable later: the rows reassemble into the
    leaderboard with a ``GROUP BY run_id``.
    """
    run_id = run_id or new_run_id()
    at = at or _now()
    rows: list[JudgeScoreEvent] = []
    for entry in ranking.entries:
        shared: dict[str, Any] = {
            "run_id": run_id,
            "mode": "ranking",
            "candidate_label": entry.label,
            "candidate_rank": entry.rank,
            "at": at,
        }
        if judge == "voice_fit":
            rows.extend(voice_fit_events(project_id, entry.result, **shared))
        else:
            rows.extend(
                animatic_events(
                    project_id, entry.result, grammar_profile=grammar_profile, **shared
                )
            )
    return rows


def insert_statement(table: str, events: Iterable[AnalyticsEvent]) -> str | None:
    """Render one multi-row ``INSERT`` for events that share a table.

    Multi-row on purpose: ClickHouse merges on write, so one insert per event is
    the canonical way to bury a cluster in tiny parts. Returns ``None`` for an
    empty batch so callers never send a degenerate statement.
    """
    batch = list(events)
    if not batch:
        return None
    columns = batch[0].columns()
    values = ", ".join(event.values_sql() for event in batch)
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES {values}"
