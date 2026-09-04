"""The analytical questions — the half of this that ClickHouse actually earns.

Each builder here is a question the operational store cannot answer at all.
``InMemoryRepository`` (and the Postgres schema it stands in for) keeps the
*current* casting, the *current* shot list, the *latest* render and one running
spend integer. It has no history, so "which voice wins for MARA across every
variant we have ever judged", "did scene 3 get better or worse", and "what did
each provider actually cost per second of finished audio" are not slow there —
they are unanswerable. Those are aggregate scans over an append-only event
stream, which is the workload ClickHouse exists for.

SQL is assembled as text because the transport is the ``mcp-clickhouse``
``run_query`` tool, which accepts a single statement string and offers no bind
parameters. Every interpolated value therefore goes through
:func:`app.analytics.events.sql_literal` (values) or
:func:`app.analytics.schema.validate_identifier` (database/table names) — there
is no third path.
"""

from __future__ import annotations

from app.analytics.events import sql_literal
from app.analytics.schema import qualified

# Result caps. These endpoints back a dashboard, not an export, and an
# unbounded LIMIT over a demo cluster is how a 3-minute video turns into a
# spinner.
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def _judge_scores(database: str) -> str:
    return qualified(database, "judge_scores")


def _render_events(database: str) -> str:
    return qualified(database, "render_events")


def _cost_events(database: str) -> str:
    return qualified(database, "cost_events")


# --------------------------------------------------------------------------- #
# Casting
# --------------------------------------------------------------------------- #
def voice_leaderboard_sql(database: str, project_id: str, limit: int | None = None) -> str:
    """Per character, how each candidate voice has scored across every run.

    This is the casting question the product is actually about: not "what is
    this character's voice" (the repo knows that) but "of everything we tried,
    what fits best, and how consistently".
    """
    return f"""
SELECT
    subject AS character_name,
    voice_name,
    voice_id,
    count() AS judgements,
    round(avg(score), 3) AS avg_score,
    round(max(score), 3) AS best_score,
    round(argMax(score, event_time), 3) AS latest_score,
    round(avg(speaks_share), 3) AS speaks_share,
    sum(line_count) AS lines_judged,
    sum(warn_count) AS warnings,
    sum(error_count) AS errors,
    max(event_time) AS last_judged_at
FROM {_judge_scores(database)}
WHERE project_id = {sql_literal(project_id)}
  AND judge = 'voice_fit'
  AND subject_kind = 'character'
GROUP BY character_name, voice_name, voice_id
ORDER BY avg_score DESC, character_name ASC
LIMIT {clamp_limit(limit)}
""".strip()


def voice_trend_sql(
    database: str,
    project_id: str,
    character: str | None = None,
    limit: int | None = None,
) -> str:
    """Every voice-fit score in time order, with a per-character rolling mean.

    The rolling window is the point: a single judgement is noise, a trend is
    evidence that a casting change helped. A window function over an ordered
    event stream is a one-liner here and a nightmare in the operational store.
    """
    subject_filter = (
        f"\n  AND subject = {sql_literal(character)}" if character else ""
    )
    return f"""
SELECT
    event_time,
    run_id,
    mode,
    candidate_label,
    candidate_rank,
    subject AS character_name,
    voice_name,
    score,
    round(
        avg(score) OVER (
            PARTITION BY subject
            ORDER BY event_time ASC
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ),
        3
    ) AS rolling_avg
FROM {_judge_scores(database)}
WHERE project_id = {sql_literal(project_id)}
  AND judge = 'voice_fit'
  AND subject_kind = 'character'{subject_filter}
ORDER BY event_time ASC, character_name ASC
LIMIT {clamp_limit(limit)}
""".strip()


# --------------------------------------------------------------------------- #
# Previz
# --------------------------------------------------------------------------- #
def animatic_trend_sql(database: str, project_id: str, limit: int | None = None) -> str:
    """Per scene: first score, latest score, and whether the work helped.

    ``argMin``/``argMax`` over ``event_time`` give first-vs-latest in one pass,
    so ``delta`` is a real regression signal — the scenes that got worse are the
    ones to look at, and nothing else in the stack remembers the earlier score.
    """
    return f"""
SELECT
    toUInt32OrZero(subject) AS scene_ordinal,
    count() AS judgements,
    round(avg(score), 3) AS avg_score,
    round(argMin(score, event_time), 3) AS first_score,
    round(argMax(score, event_time), 3) AS latest_score,
    round(argMax(score, event_time) - argMin(score, event_time), 3) AS delta,
    round(avg(coverage_score), 3) AS avg_coverage,
    round(avg(continuity_score), 3) AS avg_continuity,
    round(avg(variety_score), 3) AS avg_variety,
    round(avg(pacing_score), 3) AS avg_pacing,
    max(shot_count) AS max_shots,
    sum(warn_count) AS warnings,
    sum(error_count) AS errors,
    max(event_time) AS last_judged_at
FROM {_judge_scores(database)}
WHERE project_id = {sql_literal(project_id)}
  AND judge = 'animatic'
  AND subject_kind = 'scene'
GROUP BY scene_ordinal
ORDER BY scene_ordinal ASC
LIMIT {clamp_limit(limit)}
""".strip()


def bake_offs_sql(database: str, project_id: str, limit: int | None = None) -> str:
    """One row per ranking run: who won, by how much, over how many candidates.

    Every ``/judge/rank/*`` call writes its candidates under a shared
    ``run_id``, so the leaderboard reassembles itself with a ``GROUP BY``.
    ``argMin(..., candidate_rank)`` picks rank 1 without a self-join.
    """
    return f"""
SELECT
    run_id,
    judge,
    min(event_time) AS ran_at,
    count() AS candidates,
    argMin(candidate_label, candidate_rank) AS winner,
    round(argMin(score, candidate_rank), 3) AS winner_score,
    round(argMax(score, candidate_rank), 3) AS runner_up_score,
    round(argMin(score, candidate_rank) - argMax(score, candidate_rank), 3) AS margin
FROM {_judge_scores(database)}
WHERE project_id = {sql_literal(project_id)}
  AND mode = 'ranking'
  AND subject_kind = 'overall'
GROUP BY run_id, judge
ORDER BY ran_at DESC
LIMIT {clamp_limit(limit)}
""".strip()


# --------------------------------------------------------------------------- #
# Spend
# --------------------------------------------------------------------------- #
def spend_by_provider_sql(database: str, project_id: str, limit: int | None = None) -> str:
    """What each provider cost, produced and how slowly it did it.

    ``cost_cents`` vs ``estimated_cost_cents`` is the interesting column pair:
    the cost governor spends the *estimate*, so a persistent gap means the caps
    are protecting the wrong number.
    """
    return f"""
SELECT
    provider,
    kind,
    model,
    count() AS renders,
    sum(cost_cents) AS cost_cents,
    sum(estimated_cost_cents) AS estimated_cents,
    sum(duration_ms) AS output_ms,
    round(avg(latency_ms)) AS avg_latency_ms,
    round(quantile(0.95)(latency_ms)) AS p95_latency_ms
FROM {_render_events(database)}
WHERE project_id = {sql_literal(project_id)}
GROUP BY provider, kind, model
ORDER BY cost_cents DESC, provider ASC
LIMIT {clamp_limit(limit)}
""".strip()


def spend_by_scene_sql(database: str, project_id: str, limit: int | None = None) -> str:
    """Where the money went, scene by scene — the shot list's real budget."""
    return f"""
SELECT
    scene_ordinal,
    countIf(kind = 'audio') AS audio_renders,
    countIf(kind = 'video') AS video_renders,
    sum(cost_cents) AS cost_cents,
    sum(duration_ms) AS output_ms,
    groupUniqArray(provider) AS providers,
    max(event_time) AS last_render_at
FROM {_render_events(database)}
WHERE project_id = {sql_literal(project_id)}
GROUP BY scene_ordinal
ORDER BY cost_cents DESC, scene_ordinal ASC
LIMIT {clamp_limit(limit)}
""".strip()


def cost_pressure_sql(database: str, project_id: str, limit: int | None = None) -> str:
    """Cost-governor decisions, including the ones that were refused.

    A refusal produces no render, so without this table the caps look like they
    never fire. ``min_headroom_cents`` going negative is exactly the moment a
    demo would have blown its budget.
    """
    return f"""
SELECT
    operation,
    provider,
    count() AS decisions,
    countIf(allowed = 0) AS blocked,
    sumIf(estimated_cents, allowed = 1) AS approved_cents,
    sumIf(estimated_cents, allowed = 0) AS refused_cents,
    min(headroom_cents) AS min_headroom_cents,
    max(cap_cents) AS cap_cents,
    max(spent_before_cents) AS peak_spent_cents,
    max(event_time) AS last_decision_at
FROM {_cost_events(database)}
WHERE project_id = {sql_literal(project_id)}
GROUP BY operation, provider
ORDER BY approved_cents DESC, operation ASC
LIMIT {clamp_limit(limit)}
""".strip()
