"use client";

// The four lane renderers. Each lays out its clips absolutely on the shared time
// axis (left/width from msToPx) and animates entrance + reflow with Motion's
// `layout` inside AnimatePresence, so an AI edit that shifts/trims/ducks clips
// reflows smoothly. Clips select-and-seek on click. None of these subscribe to
// currentMs, so they don't re-render while the playhead moves.

import type { CSSProperties } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type {
  AmbienceBlock,
  DialogueClip,
  SfxMarker,
  VisualClip,
} from "@/lib/types";
import { useTimelineStore } from "@/lib/timelineStore";
import { emotionStyle, FOCUS_RING } from "@/components/casting/theme";
import { msToPx } from "@/components/timeline/layout";

const EASE = [0.16, 1, 0.3, 1] as const;
const MIN_CLIP_PX = 40;

export function VisualLane({
  clips,
  pxPerSecond,
  highlightShotOrdinal = null,
  onShotHover,
}: {
  clips: VisualClip[];
  pxPerSecond: number;
  /** Ring this shot's clip without selecting it — the workspace passes the shot
   * the pointer is over in the shot list, so both surfaces point at one shot. */
  highlightShotOrdinal?: number | null;
  /** Mirrors the hover back out, so hovering a clip lights up its shot row. */
  onShotHover?: (shotOrdinal: number | null) => void;
}) {
  const reduce = useReducedMotion();
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const selectedId = useTimelineStore((s) =>
    s.selection?.lane === "visual" ? s.selection.id : null,
  );
  return (
    <AnimatePresence initial={false}>
      {clips.map((c) => {
        const selected = selectedId === c.id;
        const linked = !selected && highlightShotOrdinal === c.shotOrdinal;
        return (
          <motion.button
            layout
            key={c.id}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? undefined : { opacity: 0, scale: 0.96 }}
            transition={{ duration: 0.3, ease: EASE }}
            onClick={() => selectAndSeek({ lane: "visual", id: c.id })}
            onPointerEnter={() => onShotHover?.(c.shotOrdinal)}
            onPointerLeave={() => onShotHover?.(null)}
            style={
              {
                left: msToPx(c.startMs, pxPerSecond),
                width: Math.max(msToPx(c.durationMs, pxPerSecond) - 2, MIN_CLIP_PX),
                top: 8,
                bottom: 8,
                ["--tl-accent"]: "var(--tl-visual)",
              } as CSSProperties
            }
            className={`tl-clip tl-visual-board text-left ${selected ? "tl-clip-selected" : ""} ${linked ? "tl-clip-linked" : ""} ${FOCUS_RING}`}
            title={c.label}
          >
            <div className="flex h-full flex-col justify-between p-1.5">
              <div className="flex items-center gap-1">
                <span className="rounded bg-black/45 px-1 font-mono text-[9px] text-sky-200">
                  #{c.shotOrdinal}
                </span>
                <span className="rounded bg-black/30 px-1 font-mono text-[9px] uppercase text-zinc-300">
                  {c.size}
                </span>
              </div>
              <span className="truncate font-mono text-[9px] text-zinc-300">
                {c.subjects.join(" · ")}
              </span>
            </div>
          </motion.button>
        );
      })}
    </AnimatePresence>
  );
}

export function DialogueLane({
  clips,
  pxPerSecond,
}: {
  clips: DialogueClip[];
  pxPerSecond: number;
}) {
  const reduce = useReducedMotion();
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const selectedId = useTimelineStore((s) =>
    s.selection?.lane === "dialogue" ? s.selection.id : null,
  );
  return (
    <AnimatePresence initial={false}>
      {clips.map((c) => {
        const selected = selectedId === c.id;
        return (
          <motion.button
            layout
            key={c.id}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? undefined : { opacity: 0, scale: 0.96 }}
            transition={{ duration: 0.3, ease: EASE }}
            onClick={() => selectAndSeek({ lane: "dialogue", id: c.id })}
            style={
              {
                ...emotionStyle(c.emotion),
                left: msToPx(c.startMs, pxPerSecond),
                width: Math.max(msToPx(c.durationMs, pxPerSecond) - 2, MIN_CLIP_PX),
                top: 10,
                bottom: 10,
                ["--tl-accent"]: "var(--ec)",
              } as CSSProperties
            }
            className={`tl-clip text-left ${selected ? "tl-clip-selected" : ""} ${FOCUS_RING}`}
            title={`${c.character}: ${c.text}`}
          >
            <div className="tl-wave pointer-events-none absolute inset-0" aria-hidden />
            <div className="relative flex h-full flex-col justify-between p-1.5">
              <span className="truncate font-mono text-[9px] font-semibold text-zinc-100">
                {c.character}
              </span>
              <span className="truncate text-[9px] leading-tight text-zinc-300">
                {c.text}
              </span>
            </div>
          </motion.button>
        );
      })}
    </AnimatePresence>
  );
}

export function AmbienceLane({
  blocks,
  pxPerSecond,
}: {
  blocks: AmbienceBlock[];
  pxPerSecond: number;
}) {
  const reduce = useReducedMotion();
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const selectedId = useTimelineStore((s) =>
    s.selection?.lane === "ambience" ? s.selection.id : null,
  );
  return (
    <AnimatePresence initial={false}>
      {blocks.map((b) => {
        const selected = selectedId === b.id;
        return (
          <motion.button
            layout
            key={b.id}
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: b.ducked ? 0.5 : 1 }}
            exit={reduce ? undefined : { opacity: 0 }}
            transition={{ duration: 0.3, ease: EASE }}
            onClick={() => selectAndSeek({ lane: "ambience", id: b.id })}
            style={
              {
                left: msToPx(b.startMs, pxPerSecond),
                width: Math.max(msToPx(b.durationMs, pxPerSecond) - 2, MIN_CLIP_PX),
                top: 8,
                bottom: 8,
                ["--tl-accent"]: "var(--tl-ambience)",
              } as CSSProperties
            }
            className={`tl-clip flex items-center gap-2 px-2 text-left ${selected ? "tl-clip-selected" : ""} ${FOCUS_RING}`}
            title={`${b.tag}${b.ducked ? " (ducked)" : ""}`}
          >
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[rgb(var(--tl-ambience))]" />
            <span className="truncate font-mono text-[10px] text-teal-100">
              {b.tag}
            </span>
            {b.ducked && (
              <span className="ml-auto shrink-0 rounded-sm bg-black/40 px-1 font-mono text-[8px] uppercase tracking-wide text-amber-300">
                ducked
              </span>
            )}
          </motion.button>
        );
      })}
    </AnimatePresence>
  );
}

export function SfxLane({
  markers,
  pxPerSecond,
}: {
  markers: SfxMarker[];
  pxPerSecond: number;
}) {
  const reduce = useReducedMotion();
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const selectedId = useTimelineStore((s) =>
    s.selection?.lane === "sfx" ? s.selection.id : null,
  );
  return (
    <AnimatePresence initial={false}>
      {markers.map((m) => {
        const selected = selectedId === m.id;
        return (
          <motion.button
            layout
            key={m.id}
            initial={reduce ? false : { opacity: 0, scale: 0.6 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={reduce ? undefined : { opacity: 0, scale: 0.6 }}
            transition={{ duration: 0.28, ease: EASE }}
            onClick={() => selectAndSeek({ lane: "sfx", id: m.id })}
            style={{ left: msToPx(m.atMs, pxPerSecond), top: "50%", transform: "translateY(-50%)" }}
            className={`absolute flex items-center gap-1.5 rounded-sm px-0.5 ${FOCUS_RING}`}
            title={m.name}
          >
            <span
              className={`tl-sfx-marker h-2.5 w-2.5 rotate-45 rounded-[2px] ${selected ? "ring-2 ring-white/80" : ""}`}
              aria-hidden
            />
            <span className="whitespace-nowrap font-mono text-[9px] text-violet-200">
              {m.name}
            </span>
          </motion.button>
        );
      })}
    </AnimatePresence>
  );
}
