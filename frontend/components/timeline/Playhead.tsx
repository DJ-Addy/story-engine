"use client";

// The single playhead that crosses the ruler and every lane. It subscribes to
// currentMs (so it moves each frame during playback) and gently keeps itself in
// view while playing. The top grip is draggable to scrub — it reuses the same
// pointer handlers TimelineGrid attaches to the ruler.

import { useEffect } from "react";
import type { CSSProperties, PointerEvent, RefObject } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useTimelineStore } from "@/lib/timelineStore";
import { CONTENT_HEIGHT, msToPx } from "@/components/timeline/layout";

export interface ScrubHandlers {
  onPointerDown: (e: PointerEvent) => void;
  onPointerMove: (e: PointerEvent) => void;
  onPointerUp: (e: PointerEvent) => void;
}

export default function Playhead({
  scrollContainerRef,
  scrubHandlers,
}: {
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  scrubHandlers: ScrubHandlers;
}) {
  const reduce = useReducedMotion();
  const currentMs = useTimelineStore((s) => s.currentMs);
  const pxPerSecond = useTimelineStore((s) => s.pxPerSecond);
  const isPlaying = useTimelineStore((s) => s.isPlaying);
  const x = msToPx(currentMs, pxPerSecond);

  // Auto-follow: while playing, scroll to keep the playhead comfortably in view.
  useEffect(() => {
    if (!isPlaying) return;
    const el = scrollContainerRef.current;
    if (!el) return;
    const left = el.scrollLeft;
    const right = left + el.clientWidth;
    if (x < left + 48 || x > right - 96) {
      el.scrollTo({ left: Math.max(0, x - el.clientWidth * 0.33) });
    }
  }, [x, isPlaying, scrollContainerRef]);

  return (
    <div
      className="pointer-events-none absolute top-0 z-20"
      style={{ left: x, height: CONTENT_HEIGHT }}
    >
      <div
        className="tl-playhead absolute left-0 top-0 w-0.5 -translate-x-1/2"
        style={{ height: CONTENT_HEIGHT }}
      />
      <motion.div
        {...scrubHandlers}
        whileHover={reduce ? undefined : { scale: 1.15 }}
        whileTap={reduce ? undefined : { scale: 0.9 }}
        className="tl-playhead-handle pointer-events-auto absolute -left-1.5 top-0 h-3.5 w-3 cursor-ew-resize touch-none rounded-b-sm"
        style={{ touchAction: "none" } as CSSProperties}
        role="slider"
        aria-label="Playhead position"
        aria-valuenow={Math.round(currentMs)}
        aria-valuemin={0}
        tabIndex={0}
      />
    </div>
  );
}
