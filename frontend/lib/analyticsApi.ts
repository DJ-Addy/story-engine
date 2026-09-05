// Client for the ClickHouse analytics spine
// (backend/app/api/routers/analytics.py). Deliberately its own module rather
// than an extension of `lib/api.ts`: nothing here fits the `StoryEngineApi`
// repository shape. Those methods return *domain objects* whose field names the
// UI knows; these endpoints return `AnalyticsPanel` — a question, a column list,
// and row dicts exactly as ClickHouse produced them. Modelling that as a
// repository method per panel would re-declare each SELECT list in a second
// place that can silently drift from the SQL, which is the same reason the
// backend refused to declare a Pydantic model per panel.
//
// Three responsibilities:
//
// 1. **Mirror the wire shapes** without guessing. Every field below is copied
//    from the Pydantic models; every name in `COLUMNS` is copied from the
//    docstring of the endpoint that produces it.
// 2. **Tell "unavailable" apart from "empty".** `available: false` arrives at
//    HTTP 200 — an unreachable cluster is not an error, it is an answer.
//    `panelState()` is the single place that distinction is made, so no
//    component re-derives it and gets it subtly wrong.
// 3. **Read untyped cells safely.** Rows are `Record<string, unknown>`: they
//    travel as JSON over MCP, where ClickHouse's 64-bit integers commonly
//    arrive as strings and `groupUniqArray` arrives as an array. The readers
//    below coerce defensively and return a documented fallback rather than
//    rendering NaN into a chart.

import { ApiError, request } from "@/lib/apiClient";

// --------------------------------------------------------------------------- //
// Wire shapes — mirror the models in backend/app/api/routers/analytics.py
// --------------------------------------------------------------------------- //

/** One row of a panel, keyed by that panel's own column names. */
export type AnalyticsRow = Record<string, unknown>;

/** `AnalyticsPanel`: one answered question, or why there is no answer. */
export interface AnalyticsPanel {
  question: string;
  available: boolean;
  detail: string | null;
  columns: string[];
  rows: AnalyticsRow[];
}

/** `AnalyticsStatus`: is the spine configured, reachable and migrated? */
export interface AnalyticsStatus {
  configured: boolean;
  reachable: boolean;
  detail: string | null;
  database: string;
  transport: string;
  host: string;
  tables_expected: string[];
  tables_present: string[];
  /** `EventRecorder.stats()` — enabled / buffered / written / dropped / failed. */
  buffer: Record<string, number | boolean>;
}

/** `AnalyticsDashboard`: every panel in one round trip. */
export interface AnalyticsDashboard {
  project_id: string;
  available: boolean;
  detail: string | null;
  panels: AnalyticsPanel[];
}

/** The slice of `ProjectOut` (backend/app/api/schemas.py) the picker needs. */
export interface ProjectSummary {
  id: string;
  title: string;
  cost_cap_cents: number;
  cost_spent_cents: number;
}

// --------------------------------------------------------------------------- //
// Panel identity
//
// GET /dashboard returns a *list*, so a panel has to be recognised by
// something. The `question` string is its only stable identifier on the wire,
// so that is matched first; the documented order of the list is the fallback.
// Both are copied verbatim from the router — if the backend rewords a question,
// the positional fallback still resolves the panel.
// --------------------------------------------------------------------------- //

export const QUESTION = {
  voiceLeaderboard:
    "Which voice fits each character best, across every variant judged?",
  animaticTrend: "Which scenes improved and which regressed?",
  bakeOffs: "Which variant won each bake-off?",
  spendByProvider: "What did each provider cost and deliver?",
  spendByScene: "Where did the money go, scene by scene?",
  costPressure: "How close to the cost cap did we get?",
} as const;

export type PanelKey = keyof typeof QUESTION;

/** The order `GET /dashboard` builds its `panels` list in. */
export const DASHBOARD_ORDER: PanelKey[] = [
  "voiceLeaderboard",
  "animaticTrend",
  "bakeOffs",
  "spendByProvider",
  "spendByScene",
  "costPressure",
];

/** The single-panel endpoint behind each key, for a per-panel refresh. */
export const PANEL_PATH: Record<PanelKey, string> = {
  voiceLeaderboard: "voice-leaderboard",
  animaticTrend: "animatic-trend",
  bakeOffs: "bake-offs",
  spendByProvider: "spend",
  spendByScene: "spend-by-scene",
  costPressure: "cost-pressure",
};

/**
 * Column names each panel produces, copied from its endpoint docstring. Used
 * only to *order* the table view: a column the backend stops returning simply
 * does not render, and one it starts returning is appended, so this list can
 * never hide data.
 */
export const COLUMNS: Record<PanelKey, string[]> = {
  voiceLeaderboard: [
    "character_name",
    "voice_name",
    "voice_id",
    "judgements",
    "avg_score",
    "best_score",
    "latest_score",
    "speaks_share",
    "lines_judged",
    "warnings",
    "errors",
    "last_judged_at",
  ],
  animaticTrend: [
    "scene_ordinal",
    "judgements",
    "avg_score",
    "first_score",
    "latest_score",
    "delta",
    "avg_coverage",
    "avg_continuity",
    "avg_variety",
    "avg_pacing",
    "max_shots",
    "warnings",
    "errors",
    "last_judged_at",
  ],
  bakeOffs: [
    "run_id",
    "judge",
    "ran_at",
    "candidates",
    "winner",
    "winner_score",
    "runner_up_score",
    "margin",
  ],
  spendByProvider: [
    "provider",
    "kind",
    "model",
    "renders",
    "cost_cents",
    "estimated_cents",
    "output_ms",
    "avg_latency_ms",
    "p95_latency_ms",
  ],
  spendByScene: [
    "scene_ordinal",
    "audio_renders",
    "video_renders",
    "cost_cents",
    "output_ms",
    "providers",
    "last_render_at",
  ],
  costPressure: [
    "operation",
    "provider",
    "decisions",
    "blocked",
    "approved_cents",
    "refused_cents",
    "min_headroom_cents",
    "cap_cents",
    "peak_spent_cents",
    "last_decision_at",
  ],
};

/** Columns of `/voice-trend`, which `/dashboard` does not include. */
export const VOICE_TREND_COLUMNS = [
  "event_time",
  "run_id",
  "mode",
  "candidate_label",
  "candidate_rank",
  "character_name",
  "voice_name",
  "score",
  "rolling_avg",
];

/** Find one panel in a dashboard response: by question, then by position. */
export function selectPanel(
  dashboard: AnalyticsDashboard | null,
  key: PanelKey,
): AnalyticsPanel | null {
  if (!dashboard) return null;
  const byQuestion = dashboard.panels.find((p) => p.question === QUESTION[key]);
  if (byQuestion) return byQuestion;
  return dashboard.panels[DASHBOARD_ORDER.indexOf(key)] ?? null;
}

// --------------------------------------------------------------------------- //
// The states a panel can be in
// --------------------------------------------------------------------------- //

/**
 * - `unavailable` — unconfigured or unreachable cluster. `detail` says which.
 *   There is no number to show and there never was one.
 * - `empty` — the cluster answered, and the answer is zero rows. Nothing has
 *   been judged or rendered for this project yet.
 * - `ready` — rows to draw.
 *
 * These read differently on screen on purpose: conflating them is how a
 * dashboard ends up implying a healthy pipeline produced nothing.
 */
export type PanelState = "unavailable" | "empty" | "ready";

export const panelState = (panel: AnalyticsPanel | null): PanelState =>
  !panel || !panel.available
    ? "unavailable"
    : panel.rows.length
      ? "ready"
      : "empty";

/** True when the cluster answered every panel and no panel has a single row. */
export function dashboardIsEmpty(dashboard: AnalyticsDashboard | null): boolean {
  if (!dashboard || !dashboard.available || !dashboard.panels.length) return false;
  return dashboard.panels.every((p) => p.available && p.rows.length === 0);
}

// --------------------------------------------------------------------------- //
// Cell readers
//
// A column's runtime type is not guaranteed to be its ClickHouse type: the rows
// are JSON produced by the MCP server, where 64-bit integers are commonly
// serialised as strings and `groupUniqArray` yields an array. Every read goes
// through one of these, each with a documented fallback — a chart never renders
// NaN, and an absent column reads as absent rather than as zero.
// --------------------------------------------------------------------------- //

/** A numeric cell, or `null` when absent or non-numeric. Never NaN. */
export function num(row: AnalyticsRow, key: string): number | null {
  const v = row[key];
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  if (typeof v === "boolean") return v ? 1 : 0;
  return null;
}

/** A numeric cell with a caller-chosen stand-in. Use only where the chart
 * geometry needs a number and the fallback is honest (a zero-length bar). */
export const numOr = (row: AnalyticsRow, key: string, fallback: number): number =>
  num(row, key) ?? fallback;

/** A text cell. Arrays join; objects stringify through JSON, never "[object …]". */
export function text(row: AnalyticsRow, key: string): string {
  const v = row[key];
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return v.map((x) => String(x)).join(", ");
  try {
    return JSON.stringify(v);
  } catch {
    return "";
  }
}

/** An array cell (`groupUniqArray`), tolerating a stringified array. */
export function list(row: AnalyticsRow, key: string): string[] {
  const v = row[key];
  if (Array.isArray(v)) return v.map((x) => String(x)).filter(Boolean);
  if (typeof v === "string" && v.trim()) {
    return v
      .replace(/^\[/, "")
      .replace(/\]$/, "")
      .split(",")
      .map((s) => s.trim().replace(/^'/, "").replace(/'$/, ""))
      .filter(Boolean);
  }
  return [];
}

/**
 * A ClickHouse DateTime cell as epoch ms, or `null`.
 *
 * ClickHouse renders DateTime as `YYYY-MM-DD hh:mm:ss` with no zone and the
 * spine stores UTC, but `Date.parse("2026-09-04 12:00:00")` is implementation
 * defined — so the string is normalised to an explicit UTC ISO form first.
 */
export function time(row: AnalyticsRow, key: string): number | null {
  const raw = row[key];
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw !== "string" || !raw.trim()) return null;
  const trimmed = raw.trim();
  const iso = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/.test(trimmed)
    ? `${trimmed.replace(" ", "T").replace(/Z$/, "")}Z`
    : trimmed;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/** Rows sorted by a numeric column, descending, absent values last. */
export const byDesc = (rows: AnalyticsRow[], key: string): AnalyticsRow[] =>
  [...rows].sort((a, b) => (num(b, key) ?? -Infinity) - (num(a, key) ?? -Infinity));

/** Rows sorted by a numeric column, ascending, absent values last. */
export const byAsc = (rows: AnalyticsRow[], key: string): AnalyticsRow[] =>
  [...rows].sort((a, b) => (num(a, key) ?? Infinity) - (num(b, key) ?? Infinity));

/** Sum one numeric column across rows. Absent cells contribute nothing. */
export const sumOf = (rows: AnalyticsRow[], key: string): number =>
  rows.reduce((acc, row) => acc + (num(row, key) ?? 0), 0);

/** The largest value in a numeric column, or 0 when there is none. */
export const maxOf = (rows: AnalyticsRow[], key: string): number =>
  rows.reduce((acc, row) => Math.max(acc, num(row, key) ?? 0), 0);

// --------------------------------------------------------------------------- //
// Requests
// --------------------------------------------------------------------------- //

const base = (projectId: string) =>
  `/projects/${encodeURIComponent(projectId)}/analytics`;

const withLimit = (path: string, limit?: number): string =>
  limit === undefined ? path : `${path}?limit=${encodeURIComponent(String(limit))}`;

/** GET .../analytics/status — configuration, liveness, migration, buffer. */
export const fetchStatus = (projectId: string): Promise<AnalyticsStatus> =>
  request<AnalyticsStatus>(`${base(projectId)}/status`);

/** GET .../analytics/dashboard — every panel in one round trip. */
export const fetchDashboard = (
  projectId: string,
  limit?: number,
): Promise<AnalyticsDashboard> =>
  request<AnalyticsDashboard>(withLimit(`${base(projectId)}/dashboard`, limit));

/** GET one panel's own endpoint — used to refresh a single card. */
export const fetchPanel = (
  projectId: string,
  key: PanelKey,
  limit?: number,
): Promise<AnalyticsPanel> =>
  request<AnalyticsPanel>(withLimit(`${base(projectId)}/${PANEL_PATH[key]}`, limit));

/**
 * GET .../analytics/voice-trend — scores in time order with a rolling mean.
 * Not part of `/dashboard`, so the page fetches it separately; `character`
 * narrows it to one character's stream.
 */
export function fetchVoiceTrend(
  projectId: string,
  character?: string | null,
  limit?: number,
): Promise<AnalyticsPanel> {
  const params = new URLSearchParams();
  if (character) params.set("character", character);
  if (limit !== undefined) params.set("limit", String(limit));
  const qs = params.toString();
  return request<AnalyticsPanel>(
    `${base(projectId)}/voice-trend${qs ? `?${qs}` : ""}`,
  );
}

/** GET /projects — the caller's own projects, for the project picker. */
export const listProjects = (): Promise<ProjectSummary[]> =>
  request<ProjectSummary[]>("/projects");

// --------------------------------------------------------------------------- //
// Failures
//
// A transport failure is a *fourth* state, distinct from unavailable and empty:
// the backend never got to answer. Classified here so the page can say what
// actually broke instead of showing one generic "something went wrong".
// --------------------------------------------------------------------------- //

export type FailureKind =
  | "auth"
  | "not-found"
  | "forbidden"
  | "server"
  | "network";

export interface Failure {
  kind: FailureKind;
  /** Headline for the state card. */
  title: string;
  /** The backend's own message where there is one. */
  detail: string;
  /** HTTP status, when the request reached the server at all. */
  status: number | null;
}

export function classify(err: unknown): Failure {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      return {
        kind: "auth",
        title: "Not signed in",
        detail:
          "The analytics endpoints take a bearer token, like the rest of the API.",
        status: 401,
      };
    }
    if (err.status === 403) {
      return {
        kind: "forbidden",
        title: "Project belongs to another account",
        detail: err.message,
        status: err.status,
      };
    }
    if (err.status === 404) {
      return {
        kind: "not-found",
        title: "No such project",
        detail: err.message,
        status: 404,
      };
    }
    return {
      kind: "server",
      title: `API error ${err.status}`,
      detail: err.message,
      status: err.status,
    };
  }
  return {
    kind: "network",
    title: "Could not reach the API",
    detail:
      err instanceof Error
        ? err.message
        : "The request failed before the backend answered.",
    status: null,
  };
}
