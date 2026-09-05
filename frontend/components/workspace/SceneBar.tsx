"use client";

// The workspace's single scene selection. One id opens BOTH halves — the shot
// list/continuity/axis AND the timeline lanes — because `api.getScene` and
// `api.getTimeline` take the same reference ("demo", "proj_1/4", "proj_1:4").
// Changing it here reloads both, so the two can never drift onto different
// scenes the way two separate routes could.

import { useState } from "react";
import type { SceneData } from "@/lib/api";
import type { TimelineData } from "@/lib/types";
import { FOCUS_RING } from "@/components/casting/theme";
import { msToClock } from "@/components/timeline/layout";

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="shrink-0 text-right">
      <p className="font-mono text-sm leading-none tabular-nums text-zinc-100">
        {value}
      </p>
      <p className="mt-1 font-mono text-[9px] uppercase tracking-wider text-zinc-600">
        {label}
      </p>
    </div>
  );
}

export default function SceneBar({
  sceneRef,
  onOpen,
  scene,
  timeline,
  loading,
  shotCount,
  findingCount,
}: {
  sceneRef: string;
  onOpen: (nextRef: string) => void;
  scene: SceneData | null;
  timeline: TimelineData | null;
  loading: boolean;
  shotCount: number;
  findingCount: number;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(sceneRef);

  // A scene opened from elsewhere (the URL, the back button) re-seeds the
  // draft. Adjusted during render rather than in an effect, so there is no
  // frame where the input shows the previous scene's id.
  const [lastRef, setLastRef] = useState(sceneRef);
  if (lastRef !== sceneRef) {
    setLastRef(sceneRef);
    setDraft(sceneRef);
  }

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    if (!next || next === sceneRef) {
      setDraft(sceneRef);
      return;
    }
    onOpen(next);
  };

  const title = scene?.title ?? timeline?.sceneTitle ?? null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-[var(--hairline)] px-4 py-2 sm:px-6">
      <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-zinc-600">
        scene
      </span>

      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            commit();
          }}
          className="flex items-center gap-1.5"
        >
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setDraft(sceneRef);
                setEditing(false);
              }
            }}
            aria-label="Scene reference (project id, or project/scene ordinal)"
            className={`w-52 rounded-md border border-[var(--hairline-strong)] bg-[var(--surface-3)] px-2 py-1 font-mono text-[11px] text-zinc-100 outline-none focus:border-amber-400/60 ${FOCUS_RING}`}
          />
          <button
            type="submit"
            className={`rounded-md bg-amber-400 px-2.5 py-1 text-[11px] font-semibold text-zinc-950 transition-colors hover:bg-amber-300 ${FOCUS_RING}`}
          >
            Open
          </button>
        </form>
      ) : (
        <button
          onClick={() => setEditing(true)}
          title="Open another scene — a project id, or project/scene ordinal"
          className={`rounded-md border border-[var(--hairline)] bg-[var(--surface-3)] px-2 py-1 font-mono text-[11px] text-zinc-200 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-50 ${FOCUS_RING}`}
        >
          {sceneRef}
          <span className="ml-1.5 text-zinc-600" aria-hidden>
            ▾
          </span>
        </button>
      )}

      <h1 className="min-w-0 flex-1 truncate text-sm font-medium tracking-tight text-zinc-100">
        {title ?? (loading ? "Loading scene…" : "No scene loaded")}
      </h1>

      {scene && (
        <span className="hidden shrink-0 rounded border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 md:inline">
          {scene.grammar_profile}
        </span>
      )}

      <div className="flex shrink-0 items-center gap-4">
        <Stat value={String(shotCount)} label="shots" />
        <Stat value={String(findingCount)} label="findings" />
        <Stat
          value={timeline ? msToClock(timeline.durationMs) : "--:--"}
          label="runtime"
        />
      </div>
    </div>
  );
}
