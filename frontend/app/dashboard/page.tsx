"use client";

// /dashboard — the ClickHouse analytics spine, on screen.
//
// The page loads GET .../analytics/dashboard once (six panels in one round trip
// rather than six requests), plus /status for the honesty strip and
// /voice-trend, which the dashboard endpoint does not carry.
//
// THE STATES THIS PAGE IS REALLY ABOUT. Four things can be true, and they are
// deliberately rendered as four different screens, because collapsing any two
// of them would mislead a reader about what the cluster actually said:
//
//   request failed   no token, wrong project, backend down. The API never
//                    answered, so nothing here reflects the cluster. A 401
//                    additionally offers the sign-in the app otherwise lacks.
//   unavailable      the API answered `available: false` at HTTP 200 — the
//                    cluster is unconfigured or unreachable. Shown once at page
//                    level when no panel came back at all, and per panel when
//                    individual queries failed. No numbers, no placeholders.
//   no data yet      the cluster answered and every panel has zero rows. The
//                    spine is healthy; nothing has been judged or rendered.
//   ready            rows to draw.
//
// A refetch never returns to the skeleton: the previous render is held at
// reduced opacity, so changing the row limit does not make the page jump.

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import {
  classify,
  dashboardIsEmpty,
  fetchDashboard,
  fetchStatus,
  fetchVoiceTrend,
  listProjects,
  selectPanel,
  type AnalyticsDashboard,
  type AnalyticsPanel,
  type AnalyticsStatus,
  type Failure,
  type ProjectSummary,
} from "@/lib/analyticsApi";
import { FOCUS_RING } from "@/components/dashboard/theme";
import {
  ActionButton,
  EmptyNotice,
  ErrorNotice,
  UnavailableNotice,
} from "@/components/dashboard/states";
import { ProjectPicker } from "@/components/dashboard/ProjectPicker";
import { StatusStrip } from "@/components/dashboard/StatusStrip";
import { SummaryBar } from "@/components/dashboard/SummaryBar";
import { SignInPanel } from "@/components/dashboard/SignInPanel";
import { VoiceLeaderboardPanel } from "@/components/dashboard/VoiceLeaderboardPanel";
import { VoiceTrendPanel } from "@/components/dashboard/VoiceTrendPanel";
import { AnimaticTrendPanel } from "@/components/dashboard/AnimaticTrendPanel";
import { BakeOffsPanel } from "@/components/dashboard/BakeOffsPanel";
import { SpendByProviderPanel } from "@/components/dashboard/SpendByProviderPanel";
import { SpendByScenePanel } from "@/components/dashboard/SpendByScenePanel";
import { CostPressurePanel } from "@/components/dashboard/CostPressurePanel";

/** The backend's own DEFAULT_LIMIT (app/analytics/queries.py). */
const DEFAULT_LIMIT = 100;

const STORAGE_KEY = "story-engine.dashboard.project";

/** Same fallback the demo routes use, so a configured deployment lands on a
 * real project with no clicking. Read as a full literal so Next inlines it. */
const DEMO_PROJECT_ID = process.env.NEXT_PUBLIC_DEMO_PROJECT_ID ?? "";

/**
 * Analytics has no mock: unlike the rest of the UI this page always talks to
 * the live API, so a tree left on the mock switch will fail here and the reason
 * is worth naming rather than leaving as a bare network error.
 */
const USE_MOCK_API =
  (process.env.NEXT_PUBLIC_USE_MOCK_API ?? "true").toLowerCase() !== "false";

/**
 * The project id lives in two external stores — the URL and localStorage — so
 * it is read with `useSyncExternalStore` rather than resolved in a mount
 * effect. That keeps the server render (which can see neither) and the
 * hydrated client render in agreement without a synchronous setState in an
 * effect body, which React 19 correctly flags as a cascading render.
 */
function environmentProjectId(): string {
  const fromUrl = new URLSearchParams(window.location.search).get("project");
  if (fromUrl) return fromUrl;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;
  } catch {
    // Private mode: fall through to the configured default.
  }
  return DEMO_PROJECT_ID;
}

const serverProjectId = () => DEMO_PROJECT_ID;

/** Neither store emits change events for our purposes — every write goes
 * through `choose`, which also updates React state in the same tick. */
const NEVER_CHANGES = () => () => {};

export default function AnalyticsDashboardPage() {
  const environmentId = useSyncExternalStore(
    NEVER_CHANGES,
    environmentProjectId,
    serverProjectId,
  );
  const [chosen, setChosen] = useState<string | null>(null);
  const projectId = chosen ?? environmentId;

  const [limit, setLimit] = useState(DEFAULT_LIMIT);
  /** Bumped by Retry, so an identical (project, limit) pair still refetches. */
  const [reloads, setReloads] = useState(0);
  const [projectsReloads, setProjectsReloads] = useState(0);

  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectsError, setProjectsError] = useState<string | null>(null);

  const [status, setStatus] = useState<AnalyticsStatus | null>(null);
  const [statusFailure, setStatusFailure] = useState<Failure | null>(null);
  const [dashboard, setDashboard] = useState<AnalyticsDashboard | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [trend, setTrend] = useState<AnalyticsPanel | null>(null);
  const [trendFailure, setTrendFailure] = useState<Failure | null>(null);

  // What the page currently wants, and what it last actually got. Loading state
  // is *derived* from the two rather than being a flag set at request time —
  // which is what keeps every setState below inside an async callback.
  const target = `${projectId}|${limit}|${reloads}`;
  const [done, setDone] = useState<{ key: string; project: string } | null>(null);
  /** In flight: the answer on screen is not the answer for the current scope. */
  const busy = projectId !== "" && done?.key !== target;
  /** Nothing has ever been shown for *this* project — the only honest skeleton. */
  const firstLoad = projectId !== "" && done?.project !== projectId;

  const choose = useCallback((id: string) => {
    setChosen(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Non-persistent session; the in-memory state still carries the choice.
    }
    const url = new URL(window.location.href);
    url.searchParams.set("project", id);
    window.history.replaceState(null, "", url.toString());
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    // One round trip for the six panels, plus /status and the trend the
    // dashboard endpoint does not carry. `allSettled`, not `all`: a failing
    // /voice-trend must not blank the panels that did answer.
    void Promise.allSettled([
      fetchStatus(projectId),
      fetchDashboard(projectId, limit),
      fetchVoiceTrend(projectId, null, limit),
    ]).then(([statusResult, dashboardResult, trendResult]) => {
      if (!alive) return;

      if (statusResult.status === "fulfilled") {
        setStatus(statusResult.value);
        setStatusFailure(null);
      } else {
        setStatus(null);
        setStatusFailure(classify(statusResult.reason));
      }

      if (dashboardResult.status === "fulfilled") {
        setDashboard(dashboardResult.value);
        setFailure(null);
      } else {
        setDashboard(null);
        setFailure(classify(dashboardResult.reason));
      }

      if (trendResult.status === "fulfilled") {
        setTrend(trendResult.value);
        setTrendFailure(null);
      } else {
        setTrend(null);
        setTrendFailure(classify(trendResult.reason));
      }

      setDone({ key: target, project: projectId });
    });
    return () => {
      alive = false;
    };
  }, [projectId, limit, target]);

  // The project list is a convenience, not a dependency: it needs a token, and
  // pasting an id has to keep working when it 401s.
  useEffect(() => {
    let alive = true;
    void listProjects().then(
      (list) => {
        if (!alive) return;
        setProjects(list);
        setProjectsError(null);
      },
      (err: unknown) => {
        if (!alive) return;
        setProjects([]);
        setProjectsError(classify(err).title);
      },
    );
    return () => {
      alive = false;
    };
  }, [projectsReloads]);

  const unauthorized =
    failure?.kind === "auth" ||
    statusFailure?.kind === "auth" ||
    trendFailure?.kind === "auth";

  const retry = useCallback(() => {
    setReloads((n) => n + 1);
    setProjectsReloads((n) => n + 1);
  }, []);

  const panelProps = { loading: firstLoad, refreshing: busy && !firstLoad };
  // The router returns an empty panel list only when there is no client at all
  // — an unconfigured cluster. That is one page-level fact, not six identical
  // per-panel notices.
  const wholeSpineDown =
    dashboard !== null && !dashboard.available && dashboard.panels.length === 0;
  const everythingEmpty = dashboardIsEmpty(dashboard);

  return (
    <div className="tl-shell min-h-screen text-zinc-200">
      <header className="sticky top-0 z-40 border-b border-[var(--hairline)] bg-[var(--cast-bg)]/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-[110rem] items-center gap-3 px-5 py-3 sm:px-8">
          <Link
            href="/"
            className={`rounded-sm text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
          >
            Story Engine
          </Link>
          <span className="text-zinc-700" aria-hidden>
            /
          </span>
          <h1 className="font-mono text-xs text-zinc-100">Analytics</h1>
          <span className="hidden rounded border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 md:inline">
            ClickHouse via MCP
          </span>
          <div className="ml-auto flex items-center gap-4">
            <Link
              href="/workspace"
              className={`rounded-sm text-xs text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
            >
              Workspace →
            </Link>
            <Link
              href="/casting"
              className={`rounded-sm text-xs text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
            >
              Casting Studio →
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[110rem] space-y-5 px-5 py-6 sm:px-8 md:py-8">
        {/* One filter row, above everything it scopes. */}
        <section className="cast-panel px-5 py-4" aria-label="Scope">
          <ProjectPicker
            projectId={projectId}
            projects={projects}
            projectsError={projectsError}
            onSelect={choose}
            onRefresh={retry}
            refreshing={busy}
            limit={limit}
            onLimit={(n) => setLimit(n)}
          />
        </section>

        {!projectId ? (
          <EmptyNotice
            what="no project is selected, so there is nothing to query."
            produces="choosing a project above, or setting NEXT_PUBLIC_DEMO_PROJECT_ID"
          />
        ) : unauthorized ? (
          <section className="cast-panel px-5 py-6">
            <ErrorNotice
              failure={{
                kind: "auth",
                title: "Not signed in",
                detail:
                  "The analytics endpoints take a bearer token like the rest of the API, and this build has no login screen.",
                status: 401,
              }}
            />
            <SignInPanel onSignedIn={retry} />
          </section>
        ) : failure ? (
          <ErrorNotice
            failure={failure}
            actions={
              <>
                <ActionButton onClick={retry} tone="accent">
                  Retry
                </ActionButton>
                {USE_MOCK_API && (
                  <span className="self-center font-mono text-[10px] text-zinc-500">
                    NEXT_PUBLIC_USE_MOCK_API is on — this page has no mock and
                    always calls the live API.
                  </span>
                )}
              </>
            }
          />
        ) : (
          <>
            <StatusStrip
              status={status}
              failure={statusFailure}
              loading={firstLoad && !status}
            />

            {wholeSpineDown ? (
              <UnavailableNotice
                scope="Every panel"
                detail={dashboard?.detail ?? null}
                actions={<ActionButton onClick={retry}>Retry</ActionButton>}
                missing={
                  status ? (
                    <span>
                      The API reports {status.configured ? "a configured" : "no configured"}{" "}
                      cluster at{" "}
                      <span className="font-mono text-zinc-400">
                        {status.host || "(no host)"}
                      </span>{" "}
                      over{" "}
                      <span className="font-mono text-zinc-400">{status.transport}</span>,
                      database{" "}
                      <span className="font-mono text-zinc-400">{status.database}</span>,
                      with {status.tables_present.length} of{" "}
                      {status.tables_expected.length} expected tables present. Judge and
                      render runs still work; their events are buffered or dropped, and
                      this page will fill in once the cluster answers.
                    </span>
                  ) : null
                }
              />
            ) : (
              <>
                {everythingEmpty && (
                  <EmptyNotice
                    what="ClickHouse is reachable and every panel came back with zero rows for this project."
                    produces="the first judge run or render on this project — each one appends events the panels below read back"
                  />
                )}

                <SummaryBar dashboard={dashboard} limit={limit} />

                <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
                  <VoiceLeaderboardPanel
                    {...panelProps}
                    className="lg:col-span-6"
                    panel={selectPanel(dashboard, "voiceLeaderboard")}
                  />
                  <VoiceTrendPanel
                    {...panelProps}
                    className="lg:col-span-6"
                    panel={trend}
                    failure={trendFailure}
                  />
                  <AnimaticTrendPanel
                    {...panelProps}
                    className="lg:col-span-7"
                    panel={selectPanel(dashboard, "animaticTrend")}
                  />
                  <BakeOffsPanel
                    {...panelProps}
                    className="lg:col-span-5"
                    panel={selectPanel(dashboard, "bakeOffs")}
                  />
                  <SpendByProviderPanel
                    {...panelProps}
                    className="lg:col-span-6"
                    panel={selectPanel(dashboard, "spendByProvider")}
                  />
                  <SpendByScenePanel
                    {...panelProps}
                    className="lg:col-span-6"
                    panel={selectPanel(dashboard, "spendByScene")}
                  />
                  <CostPressurePanel
                    {...panelProps}
                    className="lg:col-span-12"
                    panel={selectPanel(dashboard, "costPressure")}
                  />
                </div>
              </>
            )}
          </>
        )}

        <p className="pt-2 text-center font-mono text-[10px] text-zinc-600">
          Every figure on this page is a row returned by ClickHouse through the
          mcp-clickhouse server. Nothing is generated, cached or estimated in the
          browser.
        </p>
      </main>
    </div>
  );
}
