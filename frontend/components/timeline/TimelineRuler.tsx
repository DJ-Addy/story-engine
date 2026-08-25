"use client";

// Presentational ruler: adaptive time ticks + scene markers. Pure — all inputs
// arrive as props from TimelineGrid, which also owns the scrub pointer handlers
// wrapping this element.

import type { SceneMarker } from "@/lib/types";
import {
  RULER_HEIGHT,
  msToClock,
  msToPx,
  tickIntervalSeconds,
} from "@/components/timeline/layout";

export default function TimelineRuler({
  durationMs,
  pxPerSecond,
  scenes,
  width,
}: {
  durationMs: number;
  pxPerSecond: number;
  scenes: SceneMarker[];
  width: number;
}) {
  const intervalSec = tickIntervalSeconds(pxPerSecond);
  const totalSec = durationMs / 1000;

  const majors: number[] = [];
  for (let s = 0; s <= totalSec + 0.001; s += intervalSec) majors.push(s);

  return (
    <div
      className="tl-ruler relative h-full w-full select-none"
      style={{ width }}
    >
      {/* Major ticks + labels */}
      {majors.map((sec) => {
        const left = msToPx(sec * 1000, pxPerSecond);
        return (
          <div key={sec} className="absolute bottom-0 top-0" style={{ left }}>
            <div className="absolute bottom-0 h-2.5 w-px bg-white/20" />
            <span className="absolute bottom-3 left-1 whitespace-nowrap font-mono text-[9px] text-zinc-500">
              {msToClock(sec * 1000)}
            </span>
          </div>
        );
      })}

      {/* Minor ticks at the half-interval */}
      {majors.map((sec) => {
        const left = msToPx((sec + intervalSec / 2) * 1000, pxPerSecond);
        if (left > width) return null;
        return (
          <div
            key={`m-${sec}`}
            className="absolute bottom-0 h-1.5 w-px bg-white/10"
            style={{ left }}
          />
        );
      })}

      {/* Scene markers */}
      {scenes.map((scene, i) => {
        const left = msToPx(scene.startMs, pxPerSecond);
        return (
          <div
            key={`${scene.label}-${i}`}
            className="absolute top-0"
            style={{ left, height: RULER_HEIGHT }}
          >
            <div className="absolute top-0 h-full w-px bg-amber-400/40" />
            <span className="absolute left-1 top-0.5 whitespace-nowrap rounded-sm bg-amber-400/15 px-1 font-mono text-[9px] uppercase tracking-wide text-amber-300">
              {scene.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
