"use client";

// The guided pipeline: every stage of the product on one page, in the order
// the work happens, with the state of each stage READ FROM THE API rather than
// remembered by the page. A judge can click through it top to bottom; a step
// that is already done says so, a step that costs money says how much before
// it is pressed, and a step that fails shows the server's own words.
//
// Nothing here is a second implementation of anything. Each action is the
// same endpoint the workspace, the casting studio and the dashboard call —
// this page only sequences them and reports what they produced.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import AppNav from "@/components/AppNav";
import ApiModeBadge from "@/components/ApiModeBadge";
import { FOCUS_RING } from "@/components/casting/theme";
import { api, API_MODE } from "@/lib/api";
import { getToken } from "@/lib/apiClient";
import { DEMO_REF, startDemoSession } from "@/lib/demoApi";
import {
  decideCasting,
  getAnalyticsStatus,
  getCasting,
  getGraphSummary,
  getProject,
  getRenderedBoards,
  getShotBoard,
  getShots,
  getTimelineStatus,
  renderSceneAudio,
  renderSceneBoards,
  resolveProject,
  runAgent,
  type AgentRun,
  type AnalyticsStatus,
  type CastingProposal,
  type GraphSummary,
  type ProjectOut,
  type TimelineStatus,
} from "@/lib/projectApi";
import type { ShotVideo } from "@/lib/types";

const errorText = (err: unknown): string =>
  err instanceof Error ? err.message : String(err);

type StepTone = "done" | "todo" | "busy" | "bad" | "off";

function Pill({ tone, children }: { tone: StepTone; children: React.ReactNode }) {
  const skin = {
    done: "border-emerald-500/35 bg-emerald-500/[0.08] text-emerald-200",
    todo: "border-[var(--hairline-strong)] bg-white/[0.03] text-zinc-400",
    busy: "border-amber-500/40 bg-amber-500/10 text-amber-200",
    bad: "border-rose-500/40 bg-rose-500/10 text-rose-200",
    off: "border-[var(--hairline)] text-zinc-600",
  }[tone];
  return (
    <span className={`rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider ${skin}`}>
      {children}
    </span>
  );
}

const BTN =
  "rounded-md bg-amber-400 px-3 py-1.5 text-xs font-medium text-zinc-950 transition-colors hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40";
const GHOST =
  "rounded-md border border-[var(--hairline-strong)] px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100";

function Step({
  n,
  title,
  tone,
  pill,
  children,
  actions,
  tour,
}: {
  n: number;
  title: string;
  tone: StepTone;
  pill: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
  /** `data-tour` id, so the guided tour can spotlight this card. */
  tour?: string;
}) {
  return (
    <li className="cast-panel relative p-5 pl-16" data-tour={tour}>
      <span
        aria-hidden
        className={`absolute left-5 top-5 flex h-8 w-8 items-center justify-center rounded-full border font-mono text-xs ${
          tone === "done"
            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
            : "border-[var(--hairline-strong)] text-zinc-400"
        }`}
      >
        {tone === "done" ? "✓" : n}
      </span>
      <div className="flex flex-wrap items-center gap-2.5">
        <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
        <Pill tone={tone}>{pill}</Pill>
      </div>
      <div className="mt-2 space-y-2 text-xs leading-relaxed text-zinc-400">{children}</div>
      {actions && <div className="mt-3 flex flex-wrap items-center gap-2">{actions}</div>}
    </li>
  );
}

interface Loaded {
  project: ProjectOut;
  graph: GraphSummary | null;
  casting: CastingProposal | null;
  shots: { ordinal: number; intent: string; size: string }[] | null;
  timeline: TimelineStatus | null;
  boards: { rendered: number[]; total: number };
  video: ShotVideo | null;
  analytics: AnalyticsStatus | null;
}

export default function Pipeline() {
  const params = useSearchParams();
  const projectRef = (params.get("project") ?? "").trim() || DEMO_REF;
  const scene = Math.max(1, Number(params.get("scene") ?? "1") || 1);

  const [pid, setPid] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [stepError, setStepError] = useState<Record<string, string>>({});
  const [agentRun, setAgentRun] = useState<AgentRun | null>(null);
  const [boardFailures, setBoardFailures] = useState<{ shot_ordinal: number; detail: string }[]>([]);
  const [featuredShot, setFeaturedShot] = useState<number>(1);
  const [thumbs, setThumbs] = useState<Record<number, string>>({});
  const [reloads, setReloads] = useState(0);

  const reload = useCallback(() => setReloads((n) => n + 1), []);

  // Resolve the reference, then read every stage in parallel. Each read is
  // allowed to fail on its own: a missing shot list must not hide the casting.
  useEffect(() => {
    if (API_MODE === "mock") return;
    let cancelled = false;
    (async () => {
      try {
        if (!getToken()) await startDemoSession();
        const id = await resolveProject(projectRef);
        if (cancelled) return;
        setPid(id);
        const project = await getProject(id);
        const settle = <T,>(p: Promise<T>): Promise<T | null> => p.catch(() => null);
        const [graph, casting, shots, timeline, boards, analytics] = await Promise.all([
          settle(getGraphSummary(id)),
          settle(getCasting(id)),
          settle(getShots(id, scene)),
          settle(getTimelineStatus(id, scene)),
          settle(getRenderedBoards(id, scene)),
          settle(getAnalyticsStatus(id)),
        ]);
        const shot = boards?.rendered[0] ?? shots?.[0]?.ordinal ?? 1;
        const video = await settle(api.getShotVideo(id, scene, featuredShot || shot));
        if (cancelled) return;
        setFeaturedShot((f) => f || shot);
        setLoaded({
          project,
          graph,
          casting,
          shots,
          timeline,
          boards: boards ?? { rendered: [], total: 0 },
          video,
          analytics,
        });
        setLoadError(null);
      } catch (err) {
        if (!cancelled) setLoadError(errorText(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectRef, scene, reloads, featuredShot]);

  // Thumbnails for the boards that exist. Object URLs are owned here and
  // released when the set changes or the page unmounts.
  useEffect(() => {
    if (!pid || !loaded) return;
    let cancelled = false;
    const urls: string[] = [];
    (async () => {
      const next: Record<number, string> = {};
      for (const ordinal of loaded.boards.rendered.slice(0, 12)) {
        const src = await getShotBoard(pid, scene, ordinal).catch(() => null);
        if (src) {
          urls.push(src);
          next[ordinal] = src;
        }
      }
      if (!cancelled) setThumbs(next);
    })();
    return () => {
      cancelled = true;
      for (const u of urls) URL.revokeObjectURL(u);
    };
  }, [pid, scene, loaded?.boards.rendered.join(","), loaded]);

  const run = useCallback(
    async (key: string, label: string, fn: () => Promise<unknown>) => {
      setBusy(key);
      setStepError((e) => ({ ...e, [key]: "" }));
      try {
        await fn();
        reload();
      } catch (err) {
        setStepError((e) => ({ ...e, [key]: errorText(err) }));
      } finally {
        setBusy(null);
      }
      void label;
    },
    [reload],
  );

  const links = useMemo(() => {
    const id = pid ?? projectRef;
    return {
      workspace: `/workspace?scene=${encodeURIComponent(id)}/${scene}`,
      casting: `/casting?project=${encodeURIComponent(id)}`,
      dashboard: `/dashboard?project=${encodeURIComponent(id)}`,
    };
  }, [pid, projectRef, scene]);

  const boardsDone = loaded ? loaded.boards.rendered.length : 0;
  const boardsTotal = loaded ? loaded.boards.total || (loaded.shots?.length ?? 0) : 0;
  const err = (k: string) => stepError[k] || null;

  return (
    <div className="min-h-screen bg-[var(--cast-bg)] text-zinc-200">
      <AppNav>
        {loaded && (
          <span className="hidden max-w-[22rem] truncate rounded border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 lg:inline">
            {loaded.project.title}
          </span>
        )}
        <ApiModeBadge />
      </AppNav>

      <main className="mx-auto w-full max-w-4xl px-5 py-10 sm:px-8">
        <p
          data-tour="pipeline-title"
          className="font-mono text-[10px] uppercase tracking-[0.2em] text-amber-300/80"
        >
          Pipeline
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-50">
          {loaded ? loaded.project.title : "One graph, every render"}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-500">
          The manuscript becomes a story graph. The judges cast it, the agent
          network shoots it, the renderers voice it and picture it, and every
          decision lands in ClickHouse. Each step below reads its state from the
          API — nothing is pre-baked.
        </p>
        {loaded && (
          <p className="mt-2 font-mono text-[10px] text-zinc-600">
            spent {(loaded.project.cost_spent_cents / 100).toFixed(2)} of a{" "}
            {(loaded.project.cost_cap_cents / 100).toFixed(0)} cap · scene {scene}
          </p>
        )}

        {API_MODE === "mock" && (
          <div className="mt-6 rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
            This build is on mock data. The pipeline needs a live backend.
          </div>
        )}
        {loadError && (
          <div className="mt-6 rounded-md border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs text-rose-200">
            {loadError}
          </div>
        )}

        <ol className="mt-8 space-y-4">
          {/* 1. Ingest */}
          <Step
            n={1}
            tour="step-ingest"
            title="Manuscript → story graph"
            tone={loaded?.graph ? "done" : loaded ? "todo" : "off"}
            pill={loaded?.graph ? `${loaded.graph.sceneCount} scene(s)` : "nothing ingested"}
            actions={
              <>
                <Link href="/new" className={`${GHOST} ${FOCUS_RING}`}>
                  Upload a script or book
                </Link>
                {loaded?.graph && (
                  <Link href={`${links.workspace}&view=script`} className={`${GHOST} ${FOCUS_RING}`}>
                    Read the graph →
                  </Link>
                )}
              </>
            }
          >
            {loaded?.graph ? (
              <p>
                {loaded.graph.characters.length} speaking part(s):{" "}
                {loaded.graph.characters
                  .map((c) => `${c.name} (${c.lineCount})`)
                  .join(", ")}
                . Every line is attributed to a speaker; this is the IR everything else renders from.
              </p>
            ) : (
              <p>Bring in a screenplay or prose. Ingest is free.</p>
            )}
          </Step>

          {/* 2. Casting */}
          <Step
            n={2}
            tour="step-cast"
            title="Cast a voice and a tone for every part"
            tone={busy === "cast" ? "busy" : loaded?.casting ? "done" : loaded?.graph ? "todo" : "off"}
            pill={busy === "cast" ? "judging" : loaded?.casting ? "decided" : "not cast"}
            actions={
              <>
                <button
                  className={`${BTN} ${FOCUS_RING}`}
                  disabled={!loaded?.graph || busy !== null}
                  onClick={() => run("cast", "cast", () => decideCasting(pid!))}
                >
                  {loaded?.casting ? "Re-judge the casting" : "Let the judge cast it"}
                </button>
                <span className="text-[10px] text-zinc-600">free · no provider call</span>
                <Link href={links.casting} className={`${GHOST} ${FOCUS_RING}`}>
                  Casting studio →
                </Link>
              </>
            }
          >
            {loaded?.casting ? (
              <table className="w-full text-left text-xs">
                <tbody>
                  {loaded.casting.entries.map((e) => (
                    <tr key={e.character ?? "__narrator"} className="border-t border-[var(--hairline)]">
                      <td className="py-1.5 pr-3 font-medium text-zinc-200">
                        {e.is_narrator ? "Narrator" : e.character}
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-zinc-300">{e.voice_name}</td>
                      <td className="py-1.5 pr-3 font-mono text-amber-200">{e.tone ?? "—"}</td>
                      <td className="py-1.5 text-zinc-500">{e.rationale}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p>
                The judge reads the lines — who speaks how often, what the writer
                marked, how the dialogue is punctuated — and decides a voice and a
                delivery tone per part. Where the text gives no signal it says so
                rather than guessing.
              </p>
            )}
            {err("cast") && <p className="text-rose-300">{err("cast")}</p>}
          </Step>

          {/* 3. Shot list via the agent network */}
          <Step
            n={3}
            tour="step-agent"
            title="Shot list from the agent network"
            tone={busy === "agent" ? "busy" : loaded?.shots?.length ? "done" : loaded?.graph ? "todo" : "off"}
            pill={busy === "agent" ? "delegating" : loaded?.shots?.length ? `${loaded.shots.length} shots` : "no shot list"}
            actions={
              <>
                <button
                  className={`${BTN} ${FOCUS_RING}`}
                  disabled={!loaded?.graph || busy !== null}
                  onClick={() =>
                    run("agent", "agent", async () => {
                      const r = await runAgent(
                        pid!,
                        `Scene ${scene} only. Build and verify a shot list for scene ${scene}, then score the previz.`,
                        1,
                      );
                      setAgentRun(r);
                      if (r.status !== "completed") throw new Error(r.error ?? `agent run ${r.status}`);
                    })
                  }
                >
                  Run the agent network
                </button>
                <span className="text-[10px] text-zinc-600">Gemini · ~30–60s</span>
                <Link href={links.workspace} className={`${GHOST} ${FOCUS_RING}`}>
                  Workspace →
                </Link>
              </>
            }
          >
            <p>
              A coordinator on Gemini delegates to specialists — script analyst,
              shot designer, previz critic — whose tools are the real pipeline
              functions. The shot designer writes the list; the critic scores it.
            </p>
            {agentRun && (
              <p className="font-mono text-[10px] text-zinc-500">
                {agentRun.model} · delegations: {agentRun.delegations.join(" → ") || "—"} · tools:{" "}
                {agentRun.tool_calls.join(", ") || "—"}
              </p>
            )}
            {loaded?.shots?.length ? (
              <ul className="grid gap-1 sm:grid-cols-2">
                {loaded.shots.slice(0, 12).map((s) => (
                  <li key={s.ordinal} className="truncate font-mono text-[10px] text-zinc-500">
                    #{s.ordinal} {s.size.toUpperCase()} — {s.intent}
                  </li>
                ))}
              </ul>
            ) : null}
            {err("agent") && <p className="text-rose-300">{err("agent")}</p>}
          </Step>

          {/* 4. Audio */}
          <Step
            n={4}
            tour="step-audio"
            title="Render the audio — voices, tone, ambience"
            tone={
              busy === "audio"
                ? "busy"
                : loaded?.timeline?.timingSource === "rendered"
                  ? "done"
                  : loaded?.graph
                    ? "todo"
                    : "off"
            }
            pill={
              busy === "audio"
                ? "rendering"
                : loaded?.timeline?.timingSource === "rendered"
                  ? `${(loaded.timeline.durationMs / 1000).toFixed(0)}s of audio`
                  : "not rendered"
            }
            actions={
              <>
                <button
                  className={`${BTN} ${FOCUS_RING}`}
                  disabled={!loaded?.graph || busy !== null}
                  onClick={() => run("audio", "audio", () => renderSceneAudio(pid!, scene))}
                >
                  {loaded?.timeline?.timingSource === "rendered" ? "Re-render the audio" : "Render the scene audio"}
                </button>
                <span className="text-[10px] text-zinc-600">Gemini-TTS · spends credits · 2–3 min</span>
                <Link href={links.workspace} className={`${GHOST} ${FOCUS_RING}`}>
                  Listen in the workspace →
                </Link>
              </>
            }
          >
            <p>
              Every line is synthesized in the cast voice with the cast tone,
              placed on a speech bus, and mixed over a procedural ambience bed
              with sound effects cut to the words that describe them.
            </p>
            {loaded?.timeline?.timingSource === "rendered" && (
              <p className="font-mono text-[10px] text-zinc-500">
                {loaded.timeline.dialogueClips} clips · {loaded.timeline.sfxEvents} sound event(s) ·
                onsets measured to the millisecond
              </p>
            )}
            {err("audio") && <p className="text-rose-300">{err("audio")}</p>}
          </Step>

          {/* 5. Animatic boards */}
          <Step
            n={5}
            tour="step-boards"
            title="Animatic — a storyboard frame per shot"
            tone={
              busy === "boards"
                ? "busy"
                : boardsTotal > 0 && boardsDone >= boardsTotal
                  ? "done"
                  : loaded?.shots?.length
                    ? "todo"
                    : "off"
            }
            pill={busy === "boards" ? "drawing" : `${boardsDone} / ${boardsTotal} boards`}
            actions={
              <>
                <button
                  className={`${BTN} ${FOCUS_RING}`}
                  disabled={!loaded?.shots?.length || busy !== null}
                  onClick={() =>
                    run("boards", "boards", async () => {
                      const out = await renderSceneBoards(pid!, scene);
                      setBoardFailures(out.failed ?? []);
                    })
                  }
                >
                  {boardsDone > 0 && boardsDone < boardsTotal ? "Draw the remaining boards" : "Draw the boards"}
                </button>
                <span className="text-[10px] text-zinc-600">
                  Gemini image · ≈4¢ per board × {Math.max(0, boardsTotal - boardsDone)}
                </span>
                <Link href={links.workspace} className={`${GHOST} ${FOCUS_RING}`}>
                  Play the animatic →
                </Link>
              </>
            }
          >
            <p>
              Gemini draws one frame per shot from the shot spec. The workspace
              cuts them to the rendered audio — that is the animatic, and it costs
              cents. Switch the monitor to <em>Video</em> to upgrade any shot to
              Veo, which then animates from its board.
            </p>
            {Object.keys(thumbs).length > 0 && (
              <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-6">
                {loaded?.boards.rendered.slice(0, 12).map((o) =>
                  thumbs[o] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      key={o}
                      src={thumbs[o]}
                      alt={`Shot ${o} board`}
                      className="aspect-video w-full rounded border border-[var(--hairline)] object-cover"
                    />
                  ) : null,
                )}
              </div>
            )}
            {boardFailures.length > 0 && (
              <ul className="text-rose-300">
                {boardFailures.map((f) => (
                  <li key={f.shot_ordinal}>
                    shot {f.shot_ordinal}: {f.detail}
                  </li>
                ))}
              </ul>
            )}
            {err("boards") && <p className="text-rose-300">{err("boards")}</p>}
          </Step>

          {/* 6. Veo video */}
          <Step
            n={6}
            tour="step-video"
            title="Video — one shot with Veo"
            tone={busy === "video" ? "busy" : loaded?.video?.src ? "done" : loaded?.shots?.length ? "todo" : "off"}
            pill={busy === "video" ? "rendering" : loaded?.video?.src ? `shot ${featuredShot} rendered` : "no video"}
            actions={
              <>
                <label className="flex items-center gap-2 text-[10px] text-zinc-500">
                  shot
                  <select
                    value={featuredShot}
                    onChange={(e) => setFeaturedShot(Number(e.target.value))}
                    className="rounded border border-[var(--hairline)] bg-[var(--surface-3)] px-1.5 py-1 font-mono text-[10px] text-zinc-200"
                  >
                    {(loaded?.shots ?? []).map((s) => (
                      <option key={s.ordinal} value={s.ordinal}>
                        #{s.ordinal} {s.size.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className={`${BTN} ${FOCUS_RING}`}
                  disabled={!loaded?.shots?.length || busy !== null}
                  onClick={() =>
                    run("video", "video", () =>
                      api.renderShotVideo(pid!, {
                        scene_ordinal: scene,
                        shot_ordinal: featuredShot,
                        duration_s: 4,
                      }),
                    )
                  }
                >
                  Render with Veo
                </button>
                <span className="text-[10px] text-zinc-600">
                  4s · ≈$0.40 fast · from its board when one exists
                </span>
                <Link href={links.workspace} className={`${GHOST} ${FOCUS_RING}`}>
                  Watch in the monitor →
                </Link>
              </>
            }
          >
            <p>
              Veo on Vertex AI renders the shot. With a board in place it animates
              that frame (image-to-video); without one it works from the prompt.
              Governed: the cost cap is checked before the call and the refusal
              is written to ClickHouse.
            </p>
            {loaded?.video && (
              <p className="font-mono text-[10px] text-zinc-500">
                {loaded.video.provider ?? "veo"} · {loaded.video.model ?? ""} ·{" "}
                {loaded.video.durationMs ? `${(loaded.video.durationMs / 1000).toFixed(0)}s` : "clip stored"}
              </p>
            )}
            {err("video") && <p className="text-rose-300">{err("video")}</p>}
          </Step>

          {/* 7. Analytics */}
          <Step
            n={7}
            tour="step-dashboard"
            title="Every decision in ClickHouse"
            tone={loaded?.analytics?.reachable ? "done" : loaded ? "bad" : "off"}
            pill={
              loaded?.analytics?.reachable
                ? `${loaded.analytics.tables_present.length} tables live`
                : loaded?.analytics
                  ? "unreachable"
                  : "—"
            }
            actions={
              <Link href={links.dashboard} className={`${BTN} ${FOCUS_RING}`}>
                Open the dashboard →
              </Link>
            }
          >
            <p>
              Judge scores, render events and cost decisions are written through
              the official ClickHouse MCP server as they happen, and read back
              into the dashboard's leaderboards and spend panels.
            </p>
            {loaded?.analytics && !loaded.analytics.reachable && (
              <p className="text-rose-300">{loaded.analytics.detail ?? "ClickHouse is not reachable."}</p>
            )}
          </Step>
        </ol>
      </main>
    </div>
  );
}
