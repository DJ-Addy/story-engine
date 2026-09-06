"use client";

// The scene's shots, as a strip under the picture rather than a table beside it.
//
// The old workspace spent a whole column on a shot grid, which is what pushed
// the monitor down to a thumbnail. Horizontally, the same shots cost ~76px of
// height and read the way coverage actually reads: in order, along time.
//
// What a card carries is deliberately thin — ordinal, size, onset, and a dot
// when continuity has something to say about that shot. The DETAIL lives where
// you are already looking: the monitor's slate spells out size, subjects,
// intent and timing for whichever shot is under the playhead, and the inspector
// in the expanded dock covers every other lane. A card is a way in, not a form.
//
// Selection is the same one selection as everywhere else: clicking a card seeks
// the transport to that shot, which is what moves the monitor, and hovering one
// rings its clip on the lanes (and vice versa) through `useSceneStore`.

import { useEffect, useRef } from "react";
import { useSceneStore } from "@/lib/store";
import { useTimelineStore } from "@/lib/timelineStore";
import type { VisualClip } from "@/lib/types";
import { FOCUS_RING } from "@/components/casting/theme";
import { msToClock } from "@/components/timeline/layout";

/** A stable empty lane, so the selector never returns a fresh array. */
const NO_CLIPS: VisualClip[] = [];

/** The shot under the playhead — the same rule the program monitor uses: the
 * last clip that has started. Returning an id keeps this selector stable
 * between shot changes even though it re-runs on every clock tick. */
function activeClipId(clips: VisualClip[], currentMs: number): string | null {
  if (clips.length === 0) return null;
  let best = clips[0];
  for (const c of clips) {
    if (c.startMs <= currentMs && c.startMs >= best.startMs) best = c;
  }
  return best.id;
}

export default function Filmstrip() {
  const clips = useTimelineStore((s) => s.data?.lanes.visual ?? NO_CLIPS);
  const activeId = useTimelineStore((s) =>
    activeClipId(s.data?.lanes.visual ?? NO_CLIPS, s.currentMs),
  );
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);

  const hoveredOrdinal = useSceneStore((s) => s.hoveredOrdinal);
  const hoverShot = useSceneStore((s) => s.hoverShot);
  const findings = useSceneStore((s) => s.findings);

  const activeRef = useRef<HTMLButtonElement | null>(null);

  // Keep the shot being played in view. The strip is the only thing that
  // scrolls here — the page cannot — so this can never move anything else.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeId]);

  if (clips.length === 0) {
    return (
      <div className="flex h-[76px] items-center justify-center rounded-xl border border-dashed border-[var(--hairline)] px-4">
        <p className="text-[11px] text-zinc-600">
          No shots in this scene yet — the filmstrip fills in once a shot list exists.
        </p>
      </div>
    );
  }

  return (
    <div
      className="cast-scroll flex gap-2 overflow-x-auto overflow-y-hidden pb-1"
      role="group"
      aria-label="Shots"
    >
      {clips.map((c) => {
        const active = c.id === activeId;
        const linked = !active && hoveredOrdinal === c.shotOrdinal;
        const open = findings.filter(
          (f) => f.shot_ordinal === c.shotOrdinal && !f.deliberate,
        ).length;
        return (
          <button
            key={c.id}
            ref={active ? activeRef : undefined}
            type="button"
            onClick={() => selectAndSeek({ lane: "visual", id: c.id })}
            onPointerEnter={() => hoverShot(c.shotOrdinal)}
            onPointerLeave={() => hoverShot(null)}
            aria-current={active ? "true" : undefined}
            title={`Shot ${c.shotOrdinal} · ${c.size.toUpperCase()} · ${c.label}`}
            aria-label={`Shot ${c.shotOrdinal}, ${c.size}, at ${msToClock(c.startMs)}${
              open > 0 ? `, ${open} open continuity findings` : ""
            }`}
            className={`ws-thumb ${active ? "ws-thumb-active" : ""} ${
              linked ? "ws-thumb-linked" : ""
            } ${FOCUS_RING}`}
          >
            <span className="ws-thumb-board">
              <span className="font-mono text-[10px] uppercase tracking-wider">
                {c.size}
              </span>
              {open > 0 && (
                <span
                  aria-hidden
                  title={`${open} continuity finding${open === 1 ? "" : "s"}`}
                  className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-amber-400"
                />
              )}
            </span>
            <span className="flex items-center gap-1 px-[7px] py-[5px] font-mono text-[9.5px] tabular-nums">
              <span className={active ? "text-amber-300" : "text-zinc-500"}>
                {c.shotOrdinal}
              </span>
              <span className="text-zinc-600">·</span>
              <span className={active ? "text-amber-300/80" : "text-zinc-600"}>
                {msToClock(c.startMs)}
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
