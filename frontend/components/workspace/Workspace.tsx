"use client";

// THE workspace: one route where a scene is edited.
//
// Before this, /scenes/demo drove `useSceneStore` and /timeline drove
// `useTimelineStore`, each fetching its own thing, with no shared notion of the
// scene being worked on. Here a single scene reference loads BOTH, and the two
// selections are mirrored (see useSceneTimelineSync), so the shot list, the
// program monitor and the lanes are three views of one thing.
//
// Layout, top to bottom: app nav, scene bar, then the edit bay — program
// monitor beside the shot list and its panels — and the timeline docked at the
// bottom. The page itself NEVER scrolls: it is exactly one viewport tall and
// every region that can overflow scrolls inside itself.

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, type SceneData } from "@/lib/api";
import type { TimelineData } from "@/lib/types";
import { useSceneStore } from "@/lib/store";
import { useTimelineStore } from "@/lib/timelineStore";
import AppNav from "@/components/AppNav";
import ApiModeBadge from "@/components/ApiModeBadge";
import ShotListEditor from "@/components/ShotListEditor";
import VideoMonitor from "@/components/timeline/VideoMonitor";
import { useTimelineAudio } from "@/components/timeline/useTimelineAudio";
import { useTransportClock } from "@/components/timeline/useTransportClock";
import { FailurePanel } from "@/components/casting/JudgeStatus";
import SceneBar from "@/components/workspace/SceneBar";
import SidePanel, { type SidePanelTab } from "@/components/workspace/SidePanel";
import TimelineDock from "@/components/workspace/TimelineDock";
import { useSceneTimelineSync } from "@/components/workspace/useSceneTimelineSync";

/** No projects endpoint exists yet, so the workspace opens on the demo scene
 * unless ?scene= says otherwise. Both api.getScene and api.getTimeline accept
 * the same reference ("demo", "proj_1/4"), which is what lets one control open
 * both halves. */
const DEFAULT_SCENE_REF = "demo";

type Fetched<T> = { key: string; data: T } | null;
type Failed = { key: string; error: unknown } | null;

export default function Workspace() {
  const router = useRouter();
  const params = useSearchParams();
  const sceneRef = (params.get("scene") ?? "").trim() || DEFAULT_SCENE_REF;

  // Bumped by Retry; part of the load key so a retry re-runs the effect.
  const [attempt, setAttempt] = useState(0);
  const loadKey = `${sceneRef}#${attempt}`;

  const loadScene = useSceneStore((s) => s.loadScene);
  const loadTimeline = useTimelineStore((s) => s.load);

  const [scene, setScene] = useState<Fetched<SceneData>>(null);
  const [sceneError, setSceneError] = useState<Failed>(null);
  const [timeline, setTimeline] = useState<Fetched<TimelineData>>(null);
  const [timelineError, setTimelineError] = useState<Failed>(null);

  // The placeholder audio engine + the virtual transport clock, mounted once
  // for the whole workspace. These stay authoritative over time; the monitor
  // only follows them.
  const { engineRef, activate } = useTimelineAudio();
  useTransportClock(engineRef);

  // One selection across the shot list and the visual lane.
  useSceneTimelineSync();

  // One reference, both halves. They are fetched in parallel and reported
  // independently: against a live backend the timeline 404s ("No audio rendered
  // yet") for a scene whose shot list loads perfectly well, and losing the shot
  // list to that would be a lie about what is available.
  useEffect(() => {
    let cancelled = false;
    const key = loadKey;

    api
      .getScene(sceneRef)
      .then((d) => {
        if (cancelled) return;
        setScene({ key, data: d });
        loadScene(d.shots, d.findings);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setSceneError({ key, error });
      });

    api
      .getTimeline(sceneRef)
      .then((d) => {
        if (cancelled) return;
        setTimeline({ key, data: d });
        loadTimeline(d);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setTimelineError({ key, error });
      });

    return () => {
      cancelled = true;
    };
  }, [loadKey, sceneRef, loadScene, loadTimeline]);

  // Loading is DERIVED from "what has answered for the current key", so no
  // effect ever has to synchronously set state back to "loading".
  const sceneData = scene?.key === loadKey ? scene.data : null;
  const sceneFailure = sceneError?.key === loadKey ? sceneError.error : null;
  const sceneLoading = sceneData === null && sceneFailure === null;

  const timelineData = timeline?.key === loadKey ? timeline.data : null;
  const timelineFailure =
    timelineError?.key === loadKey ? timelineError.error : null;
  const timelineLoading = timelineData === null && timelineFailure === null;

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  // Switching scenes empties both stores first, so nothing from the old scene
  // is on screen while the new one loads.
  const openScene = useCallback(
    (next: string) => {
      useSceneStore.getState().loadScene([], []);
      useTimelineStore.getState().clear();
      const query =
        next === DEFAULT_SCENE_REF ? "" : `?scene=${encodeURIComponent(next)}`;
      router.replace(`/workspace${query}`);
    },
    [router],
  );

  // Hover is shared: pointing at a shot row rings its clip on the visual lane,
  // and pointing at a clip lights up its row.
  const hoveredOrdinal = useSceneStore((s) => s.hoveredOrdinal);
  const hoverShot = useSceneStore((s) => s.hoverShot);

  const shotCount = useSceneStore((s) => s.shots.length);
  const findingCount = useSceneStore(
    (s) => s.findings.filter((f) => !f.deliberate).length,
  );

  const [tab, setTab] = useState<SidePanelTab>("continuity");

  // A dialogue line, an ambience bed or an SFX cue has nowhere else to be read,
  // so selecting one brings the inspector forward. A visual clip does not: it
  // is already spelled out in the shot list and the monitor. Adjusted during
  // render (the documented pattern for reacting to a changed value) rather than
  // in an effect, which would render the old tab first and then swap it.
  const selection = useTimelineStore((s) => s.selection);
  const [lastSelection, setLastSelection] = useState(selection);
  if (selection !== lastSelection) {
    setLastSelection(selection);
    if (selection && selection.lane !== "visual") setTab("inspector");
  }

  // Global transport keyboard: space = play/pause, arrows nudge. Ignored while
  // typing in a form control, and inside any widget that owns its own arrows
  // (the shot-list grid, the dock's resize grip).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.tagName === "SELECT" ||
          t.isContentEditable)
      )
        return;
      const store = useTimelineStore.getState();
      if (!store.data) return;
      if (e.code === "Space" || e.key === " ") {
        e.preventDefault();
        activate();
        store.togglePlay();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        if (t?.closest("[data-local-arrow-keys]")) return;
        e.preventDefault();
        const step = e.shiftKey ? 5000 : 1000;
        store.nudge(e.key === "ArrowLeft" ? -step : step);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activate]);

  return (
    <div className="tl-shell flex h-dvh flex-col overflow-hidden text-zinc-200">
      <AppNav width="max-w-none">
        <ApiModeBadge />
      </AppNav>

      <SceneBar
        sceneRef={sceneRef}
        onOpen={openScene}
        scene={sceneData}
        timeline={timelineData}
        loading={sceneLoading && timelineLoading}
        shotCount={shotCount}
        findingCount={findingCount}
      />

      <main className="grid min-h-0 flex-1 gap-3 overflow-y-auto px-4 pb-1 pt-3 sm:px-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(360px,1fr)] lg:overflow-hidden">
        <section
          aria-label="Program monitor"
          className="min-h-0 min-w-0 max-lg:min-h-[240px]"
        >
          {/* Width derived from the height this row can spare, so the 16:9
              stage is never squashed. See .ws-monitor-fit in globals.css. */}
          <div className="ws-monitor-fit">
            <VideoMonitor />
          </div>
        </section>

        <aside
          aria-label="Shot list and scene panels"
          className="flex min-h-0 min-w-0 flex-col gap-3 lg:grid lg:grid-rows-[minmax(0,1.15fr)_minmax(0,1fr)]"
        >
          <div className="min-h-0 min-w-0 max-lg:min-h-[320px]">
            {sceneFailure !== null ? (
              <FailurePanel
                error={sceneFailure}
                onRetry={retry}
                retryLabel="Reload scene"
                compact
              />
            ) : sceneLoading ? (
              <div
                className="cast-panel cast-shimmer h-full min-h-[160px]"
                aria-busy
              />
            ) : (
              <ShotListEditor />
            )}
          </div>
          <div className="min-h-0 min-w-0 max-lg:min-h-[300px]">
            <SidePanel tab={tab} onTab={setTab} />
          </div>
        </aside>
      </main>

      <TimelineDock
        onActivateAudio={activate}
        loading={timelineLoading}
        failure={timelineFailure}
        onRetry={retry}
        highlightShotOrdinal={hoveredOrdinal}
        onShotHover={hoverShot}
      />
    </div>
  );
}
