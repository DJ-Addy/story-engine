"use client";

// THE workspace: ONE SHELL, TWO VIEWS.
//
// Before this, /scenes/demo drove `useSceneStore` and /timeline drove
// `useTimelineStore`, each fetching its own thing, with no shared notion of the
// scene being worked on. Then both landed on one route — and that route grew
// six regions and a four-tab panel, which is the version that got called "way
// too cluttered to use for anyone". This is the answer to that.
//
// The frame never moves. The top bar, the scene rail on the left and the
// assistant on the right are the same in both views; only the middle column is
// swapped, so throwing the Script/Edit switch never feels like changing pages.
// Read the scene as a screenplay, or cut it as picture — one scene reference
// loads both halves and one selection is mirrored across them (see
// useSceneTimelineSync), so the script, the filmstrip, the monitor and the
// lanes are four views of one graph rather than four fetches.
//
// The page itself NEVER scrolls: it is exactly one viewport tall and every
// region that can overflow scrolls inside itself.

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api, type SceneData, type SceneSummary } from "@/lib/api";
import type { TimelineData } from "@/lib/types";
import { getToken } from "@/lib/apiClient";
import { splitSceneRef } from "@/lib/sceneRef";
import { useSceneStore } from "@/lib/store";
import { useTimelineStore } from "@/lib/timelineStore";
import { DEMO_REF, isDemoRef, startDemoSession } from "@/lib/demoApi";
import AppNav from "@/components/AppNav";
import ApiModeBadge from "@/components/ApiModeBadge";
import AssistantPanel from "@/components/assistant/AssistantPanel";
import { useTimelineAudio } from "@/components/timeline/useTimelineAudio";
import { useTransportClock } from "@/components/timeline/useTransportClock";
import ColdStartPanel, { coldStartKind } from "@/components/workspace/ColdStart";
import EditView from "@/components/workspace/EditView";
import SceneRail from "@/components/workspace/SceneRail";
import ScriptView from "@/components/workspace/ScriptView";
import ViewSwitch, {
  VIEW_PANEL_ID,
  viewTabId,
  type WorkspaceView,
} from "@/components/workspace/ViewSwitch";
import { useSceneTimelineSync } from "@/components/workspace/useSceneTimelineSync";

/** No projects endpoint exists yet, so the workspace opens on the demo scene
 * unless ?scene= says otherwise. `api.getScene`, `api.getTimeline` and
 * `api.listScenes` all accept the same reference ("demo", "proj_1/4"), which is
 * what lets one control open every part of this page. What "demo" points at is
 * discovered from the server at runtime — see lib/demoApi.ts. */
const DEFAULT_SCENE_REF = DEMO_REF;

/** Edit is the default view: a reviewer arriving cold should meet the picture. */
const DEFAULT_VIEW: WorkspaceView = "edit";

type Fetched<T> = { key: string; data: T } | null;
type Failed = { key: string; error: unknown } | null;

export default function Workspace() {
  const router = useRouter();
  const params = useSearchParams();
  const sceneRef = (params.get("scene") ?? "").trim() || DEFAULT_SCENE_REF;
  const view: WorkspaceView =
    params.get("view") === "script" ? "script" : DEFAULT_VIEW;

  const { projectRef, ordinal } = splitSceneRef(sceneRef);

  // Bumped by Retry AND by the assistant applying edits; part of the load key,
  // so either one re-runs the effect and refetches both halves.
  const [attempt, setAttempt] = useState(0);
  const loadKey = `${sceneRef}#${attempt}`;

  const loadScene = useSceneStore((s) => s.loadScene);
  const loadTimeline = useTimelineStore((s) => s.load);

  const [scene, setScene] = useState<Fetched<SceneData>>(null);
  const [sceneError, setSceneError] = useState<Failed>(null);
  const [timeline, setTimeline] = useState<Fetched<TimelineData>>(null);
  const [timelineError, setTimelineError] = useState<Failed>(null);
  const [scenes, setScenes] = useState<Fetched<SceneSummary[]>>(null);

  // The placeholder audio engine + the virtual transport clock, mounted once
  // for the whole workspace. These stay authoritative over time; the monitor
  // only follows them.
  const { engineRef, activate } = useTimelineAudio();
  useTransportClock(engineRef);

  // One selection across the script, the filmstrip and the lanes.
  useSceneTimelineSync();

  // One reference, both halves. They are fetched in parallel and reported
  // independently: against a live backend the shot list can be missing for a
  // scene whose timeline plans perfectly well, and losing the timeline to that
  // would be a lie about what is available.
  useEffect(() => {
    let cancelled = false;
    const key = loadKey;

    // A visitor with no token asking for the demo gets the demo session
    // started for them, the same way the pipeline page does. The cold-start
    // panel stays for the cases it was built for — nothing seeded, or a
    // project this account does not own — but "you have not pressed the
    // button yet" is not a state a reviewer should be shown.
    const ready =
      isDemoRef(projectRef) && !getToken()
        ? startDemoSession().then(() => undefined, () => undefined)
        : Promise.resolve();

    ready.then(() => {
    if (cancelled) return;

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
    });

    return () => {
      cancelled = true;
    };
  }, [loadKey, sceneRef, projectRef, loadScene, loadTimeline]);

  // The rail is a property of the PROJECT, so it survives moving between that
  // project's scenes and is only refetched when the project changes. A failure
  // here is answered with an empty list rather than an alert: the rail then
  // shows the one scene that is open, and the scene's own failure — the one
  // that actually costs the viewer something — is the only one reported.
  useEffect(() => {
    let cancelled = false;
    const key = `${projectRef}#${attempt}`;

    api
      .listScenes(projectRef)
      .then((list) => {
        if (!cancelled) setScenes({ key, data: list });
      })
      .catch(() => {
        if (!cancelled) setScenes({ key, data: [] });
      });

    return () => {
      cancelled = true;
    };
  }, [projectRef, attempt]);

  // Loading is DERIVED from "what has answered for the current key", so no
  // effect ever has to synchronously set state back to "loading".
  const sceneData = scene?.key === loadKey ? scene.data : null;
  const sceneFailure = sceneError?.key === loadKey ? sceneError.error : null;

  const timelineData = timeline?.key === loadKey ? timeline.data : null;
  const timelineFailure =
    timelineError?.key === loadKey ? timelineError.error : null;
  const timelineLoading = timelineData === null && timelineFailure === null;

  const sceneList =
    scenes?.key === `${projectRef}#${attempt}` ? scenes.data : null;

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  // A cold start is not one of the failures a panel can usefully report in
  // place: there is no session, or no project, so the rail, the monitor and the
  // assistant have nothing to be about. The shell keeps its frame and hands the
  // middle to one recovery instead of showing three empty regions around an
  // error strip. Everything else — a 500, a scene missing from a project that
  // loaded, a timeline that was never rendered — still fails where it happened.
  const coldStart = coldStartKind({
    sceneFailure,
    timelineFailure,
    onDemoRef: isDemoRef(projectRef),
  });

  /** The URL that opens a given scene in a given view. Both live in the query
   * so a view is shareable — a reviewer can be sent straight to the script. */
  const hrefFor = useCallback((nextRef: string, nextView: WorkspaceView) => {
    const q = new URLSearchParams();
    if (nextRef !== DEFAULT_SCENE_REF) q.set("scene", nextRef);
    if (nextView !== DEFAULT_VIEW) q.set("view", nextView);
    const query = q.toString();
    return query ? `/workspace?${query}` : "/workspace";
  }, []);

  // Switching scenes empties both stores first, so nothing from the old scene
  // is on screen while the new one loads.
  const openScene = useCallback(
    (next: string) => {
      // Compared as a split reference, not as a string: "demo" and "demo/1"
      // name the same scene, and reloading it would be a flash for nothing.
      const target = splitSceneRef(next);
      if (target.projectRef === projectRef && target.ordinal === ordinal) return;
      useSceneStore.getState().loadScene([], []);
      useTimelineStore.getState().clear();
      router.replace(hrefFor(next, view), { scroll: false });
    },
    [router, hrefFor, projectRef, ordinal, view],
  );

  // Switching views refetches nothing: the load key does not contain the view,
  // and both views read the stores that are already loaded.
  const setView = useCallback(
    (next: WorkspaceView) => {
      if (next === view) return;
      router.replace(hrefFor(sceneRef, next), { scroll: false });
    },
    [router, hrefFor, sceneRef, view],
  );

  // Global transport keyboard: space = play/pause, arrows nudge. Ignored while
  // typing in a form control (the assistant's composer, the rail's reference
  // box), and inside any widget that owns its own arrows (the view switch).
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
        {/* The switch is a control over a loaded scene, and during a cold start
            it would label a tab panel that is not rendered. It stands down with
            the rest of the workspace rather than offering an empty choice. */}
        {coldStart === null && <ViewSwitch value={view} onChange={setView} />}
        <ApiModeBadge />
      </AppNav>

      {coldStart !== null ? (
        <ColdStartPanel
          kind={coldStart.kind}
          failure={coldStart.failure}
          onRecovered={retry}
        />
      ) : (
        <div className="flex min-h-0 flex-1">
          <SceneRail
            projectRef={projectRef}
            activeOrdinal={timelineData?.sceneOrdinal ?? ordinal}
            scenes={sceneList}
            openTitle={sceneData?.title ?? timelineData?.sceneTitle ?? null}
            onOpen={openScene}
            sceneFailure={sceneFailure}
            onRetry={retry}
          />

          <div
            id={VIEW_PANEL_ID}
            role="tabpanel"
            aria-labelledby={viewTabId(view)}
            className="flex min-h-0 min-w-0 flex-1 flex-col"
          >
            {view === "edit" ? (
              <EditView
                onActivateAudio={activate}
                loading={timelineLoading}
                failure={timelineFailure}
                onRetry={retry}
              />
            ) : (
              <ScriptView
                onActivateAudio={activate}
                loading={timelineLoading}
                failure={timelineFailure}
                onRetry={retry}
              />
            )}
          </div>

          {/* The assistant is the third fixed region, not a drawer: it edits the
              story graph, and when it applies an edit the shell — not the panel —
              refetches the scene and the timeline it just changed. Below `lg`
              there is no honest way to keep three columns on one non-scrolling
              page, so it and the rail step aside rather than shrink to nothing. */}
          <aside
            aria-label="Assistant"
            className="hidden w-[336px] shrink-0 lg:block"
          >
            <AssistantPanel
              projectId={timelineData?.projectId ?? projectRef}
              sceneOrdinal={timelineData?.sceneOrdinal ?? ordinal}
              onEditsApplied={retry}
            />
          </aside>
        </div>
      )}
    </div>
  );
}
