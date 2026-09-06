"use client";

// The scenes of the project, always in the same place.
//
// This replaces the scene BAR — a single reference you had to know to type —
// with the list it should always have been: the project's scenes, one click
// each, driving both views at once because `api.getScene` and `api.getTimeline`
// take the same reference. The rail never moves when the view is switched, so
// "which scene am I in" is answered identically in Script and in Edit.
//
// Opening a scene the rail does not list is still possible (a real project id
// against a live backend), which is what the reference control in the footer is
// for — it is the old scene bar, folded into the one place scenes live.

import { useState } from "react";
import type { SceneSummary } from "@/lib/api";
import { joinSceneRef } from "@/lib/sceneRef";
import { FailurePanel } from "@/components/casting/JudgeStatus";
import { FOCUS_RING } from "@/components/casting/theme";

export default function SceneRail({
  projectRef,
  activeOrdinal,
  scenes,
  openTitle,
  onOpen,
  sceneFailure,
  onRetry,
}: {
  /** The project half of the open reference — what a rail click re-joins to. */
  projectRef: string;
  activeOrdinal: number;
  /** The project's scenes, or null while they are still being fetched. An empty
   * list means they could not be listed at all, and the rail falls back to the
   * one scene that IS open rather than showing nothing. */
  scenes: SceneSummary[] | null;
  /** Title of the scene actually open, used for that fallback row. */
  openTitle: string | null;
  onOpen: (sceneRef: string) => void;
  sceneFailure: unknown | null;
  onRetry: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(projectRef);

  // A project opened from elsewhere (the URL, the back button) re-seeds the
  // draft. Adjusted during render rather than in an effect, so there is no
  // frame where the input shows the previous project's id.
  const [lastRef, setLastRef] = useState(projectRef);
  if (lastRef !== projectRef) {
    setLastRef(projectRef);
    setDraft(projectRef);
  }

  const rows: SceneSummary[] =
    scenes && scenes.length > 0
      ? scenes
      : scenes === null
        ? []
        : [{ ordinal: activeOrdinal, title: openTitle ?? "This scene", lineCount: 0 }];

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    if (!next) {
      setDraft(projectRef);
      return;
    }
    onOpen(next);
  };

  return (
    <nav
      aria-label="Scenes"
      className="hidden w-[194px] shrink-0 flex-col border-r border-[var(--hairline)] bg-black/25 md:flex"
    >
      <div className="cast-scroll min-h-0 flex-1 overflow-y-auto px-2.5 py-3.5">
        <p className="px-2.5 pb-2.5 font-mono text-[11px] uppercase tracking-[0.07em] text-zinc-500">
          Scenes
        </p>

        {scenes === null ? (
          <div className="flex flex-col gap-1.5" aria-busy>
            {[0, 1, 2].map((i) => (
              <div key={i} className="cast-shimmer h-[46px] rounded-[9px]" />
            ))}
          </div>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {rows.map((s) => {
              const active = s.ordinal === activeOrdinal;
              return (
                <li key={s.ordinal}>
                  <button
                    type="button"
                    onClick={() => onOpen(joinSceneRef(projectRef, s.ordinal))}
                    aria-current={active ? "true" : undefined}
                    className={`flex w-full flex-col gap-[3px] rounded-[9px] border px-2.5 py-2 text-left transition-colors ${
                      active
                        ? "border-amber-500/25 bg-amber-400/[0.09]"
                        : "border-transparent hover:bg-white/[0.04]"
                    } ${FOCUS_RING}`}
                  >
                    <span
                      className={`line-clamp-2 text-[12px] leading-snug ${
                        active ? "text-zinc-50" : "text-zinc-300"
                      }`}
                    >
                      {s.title}
                    </span>
                    <span
                      className={`font-mono text-[10px] ${
                        active ? "text-amber-300" : "text-zinc-600"
                      }`}
                    >
                      SC {s.ordinal}
                      {s.lineCount > 0 ? ` · ${s.lineCount} lines` : ""}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="shrink-0 border-t border-[var(--hairline)] p-2.5">
        {sceneFailure !== null && (
          <div className="mb-2.5">
            <FailurePanel
              error={sceneFailure}
              onRetry={onRetry}
              retryLabel="Reload"
              compact
            />
          </div>
        )}

        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              commit();
            }}
            className="flex flex-col gap-1.5"
          >
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setDraft(projectRef);
                  setEditing(false);
                }
              }}
              aria-label="Project reference, or project/scene ordinal"
              className={`w-full rounded-md border border-[var(--hairline-strong)] bg-[var(--surface-3)] px-2 py-1 font-mono text-[11px] text-zinc-100 outline-none focus:border-amber-400/60 ${FOCUS_RING}`}
            />
            <button
              type="submit"
              className={`rounded-md bg-amber-400 px-2 py-1 text-[11px] font-semibold text-zinc-950 transition-colors hover:bg-amber-300 ${FOCUS_RING}`}
            >
              Open
            </button>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            title="Open another project — an id, or project/scene ordinal"
            className={`flex w-full items-center gap-1.5 rounded-md border border-[var(--hairline)] bg-[var(--surface-3)] px-2 py-1.5 font-mono text-[11px] text-zinc-300 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-50 ${FOCUS_RING}`}
          >
            <span className="min-w-0 flex-1 truncate text-left">{projectRef}</span>
            <span className="shrink-0 text-zinc-600" aria-hidden>
              ▾
            </span>
          </button>
        )}
      </div>
    </nav>
  );
}
