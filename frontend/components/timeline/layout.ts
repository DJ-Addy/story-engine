// Presentation-only geometry + formatting helpers shared across the timeline
// components. Keeps the time<->pixel math and the lane row metrics in one place
// so the ruler, lanes, and playhead all agree on the same coordinate system.
// No store, no data access — pure functions and constants.

import type { TimelineLaneKind } from "@/lib/types";

/** Width (px) of the sticky lane-label column on the left. */
export const LABEL_WIDTH = 118;

/** Height (px) of the ruler row that sits above the lanes.
 *
 * The ruler and the lane heights below are sized so that ruler + all four
 * lanes fit inside the workspace's docked timeline on a 900px-tall laptop
 * without the page scrolling as a whole (see components/workspace). */
export const RULER_HEIGHT = 40;

/** Per-lane row heights (px). */
export const LANE_HEIGHT: Record<TimelineLaneKind, number> = {
  visual: 72,
  dialogue: 60,
  ambience: 44,
  sfx: 38,
};

/** Top-to-bottom lane order shared by the labels column and the track. */
export const LANE_ORDER: TimelineLaneKind[] = [
  "visual",
  "dialogue",
  "ambience",
  "sfx",
];

/** Total height (px) of the ruler + every lane; the playhead spans all of it. */
export const CONTENT_HEIGHT =
  RULER_HEIGHT + LANE_ORDER.reduce((sum, k) => sum + LANE_HEIGHT[k], 0);

/** Label / hint / accent (an --tl-* triple token) for each lane. */
export const LANE_META: Record<
  TimelineLaneKind,
  { label: string; hint: string; accent: string }
> = {
  visual: { label: "VISUAL", hint: "shots", accent: "var(--tl-visual)" },
  dialogue: { label: "DIALOGUE", hint: "lines", accent: "var(--tl-dialogue)" },
  ambience: { label: "AMBIENCE", hint: "beds", accent: "var(--tl-ambience)" },
  sfx: { label: "SFX", hint: "one-shots", accent: "var(--tl-sfx)" },
};

export const ZOOM_MIN = 24;
export const ZOOM_MAX = 320;
export const ZOOM_DEFAULT = 80;

/** Time -> horizontal pixels at the current zoom. */
export const msToPx = (ms: number, pxPerSecond: number) =>
  (ms / 1000) * pxPerSecond;

/** Horizontal pixels -> time at the current zoom. */
export const pxToMs = (px: number, pxPerSecond: number) =>
  (px / pxPerSecond) * 1000;

/** Format milliseconds as mm:ss; nullish/NaN renders the empty placeholder. */
export function msToClock(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "--:--";
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Candidate ruler tick intervals in seconds, coarsening as you zoom out. */
const NICE_INTERVALS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];

/** Pick a "nice" tick interval (seconds) so major labels stay ~64px apart. */
export function tickIntervalSeconds(pxPerSecond: number): number {
  const minPx = 64;
  return (
    NICE_INTERVALS.find((s) => s * pxPerSecond >= minPx) ??
    NICE_INTERVALS[NICE_INTERVALS.length - 1]
  );
}
