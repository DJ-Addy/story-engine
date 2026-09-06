"use client";

// The transport, thinned to one line.
//
// Both views need the same three facts — where the playhead is, how long the
// scene runs, and whether those numbers were measured or planned — but neither
// has room for the full `TransportBar`. This is that bar's spine: play/pause,
// the clock, and the estimated-timings chip, which is imported from the bar
// itself so the two can never disagree about when to show it.
//
// The Script view adds a scrub track (it has no lanes to scrub on); the Edit
// dock leaves it off and passes its own control into the right-hand slot.
// currentMs is read only by the two small components that draw it, so a moving
// playhead never re-renders the line around them.

import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useTimelineStore } from "@/lib/timelineStore";
import { FOCUS_RING } from "@/components/casting/theme";
import { msToClock } from "@/components/timeline/layout";
import { TimingSourceChip } from "@/components/timeline/TransportBar";

function Clock() {
  const currentMs = useTimelineStore((s) => s.currentMs);
  const hasData = useTimelineStore((s) => s.data !== null);
  return (
    <span className="shrink-0 font-mono text-[13px] tabular-nums text-zinc-50">
      {hasData ? msToClock(currentMs) : "--:--"}
    </span>
  );
}

function Total() {
  const durationMs = useTimelineStore((s) => s.durationMs);
  const hasData = useTimelineStore((s) => s.data !== null);
  return (
    <span className="shrink-0 font-mono text-[11px] tabular-nums text-zinc-600">
      {hasData ? msToClock(durationMs) : "--:--"}
    </span>
  );
}

/** A seekable progress line. Arrow keys are deliberately NOT handled here: the
 * workspace's global transport keys already nudge by 1s (5s with shift), which
 * is exactly what a slider's arrows should do, and handling them twice would
 * double every nudge. */
function ScrubTrack() {
  const currentMs = useTimelineStore((s) => s.currentMs);
  const durationMs = useTimelineStore((s) => s.durationMs);
  const seek = useTimelineStore((s) => s.seek);
  const hasData = useTimelineStore((s) => s.data !== null);

  const pct = durationMs > 0 ? Math.min(100, (currentMs / durationMs) * 100) : 0;

  const seekTo = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (durationMs <= 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width === 0) return;
    seek(((e.clientX - rect.left) / rect.width) * durationMs);
  };

  return (
    <div
      role="slider"
      tabIndex={hasData ? 0 : -1}
      aria-label="Playhead"
      aria-valuemin={0}
      aria-valuemax={Math.round(durationMs)}
      aria-valuenow={Math.round(currentMs)}
      aria-valuetext={msToClock(currentMs)}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture?.(e.pointerId);
        seekTo(e);
      }}
      onPointerMove={(e) => {
        if (e.buttons === 0) return;
        seekTo(e);
      }}
      className={`relative h-1.5 min-w-0 flex-1 cursor-pointer touch-none rounded-full bg-[var(--surface-2)] ${FOCUS_RING}`}
    >
      <div
        className="absolute inset-y-0 left-0 rounded-full bg-amber-400/50"
        style={{ width: `${pct}%` }}
      />
      <span
        aria-hidden
        className="tl-playhead-handle absolute -top-[5px] h-4 w-0.5 rounded-sm"
        style={{ left: `${pct}%` }}
      />
    </div>
  );
}

export default function MiniTransport({
  onActivateAudio,
  scrub = false,
  children,
}: {
  onActivateAudio: () => void;
  /** Draw the scrub track between the clock and the total. */
  scrub?: boolean;
  /** Right-hand slot: whatever control the view owning this line needs. */
  children?: ReactNode;
}) {
  const reduce = useReducedMotion();
  const isPlaying = useTimelineStore((s) => s.isPlaying);
  const hasData = useTimelineStore((s) => s.data !== null);
  const togglePlay = useTimelineStore((s) => s.togglePlay);

  return (
    <div className="flex items-center gap-3">
      <motion.button
        type="button"
        whileTap={reduce ? undefined : { scale: 0.94 }}
        onClick={() => {
          onActivateAudio();
          togglePlay();
        }}
        disabled={!hasData}
        aria-label={isPlaying ? "Pause" : "Play"}
        className={`flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-[7px] bg-amber-400 text-zinc-950 transition-colors hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`}
      >
        {isPlaying ? (
          <svg viewBox="0 0 24 24" className="h-2.5 w-2.5" fill="currentColor" aria-hidden>
            <rect x="6" y="5" width="4" height="14" rx="1" />
            <rect x="14" y="5" width="4" height="14" rx="1" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" className="h-2.5 w-2.5" fill="currentColor" aria-hidden>
            <path d="M8 5.14v13.72a1 1 0 0 0 1.54.84l10.29-6.86a1 1 0 0 0 0-1.68L9.54 4.3A1 1 0 0 0 8 5.14Z" />
          </svg>
        )}
      </motion.button>

      <Clock />
      {scrub ? (
        <ScrubTrack />
      ) : (
        <span className="shrink-0 font-mono text-[11px] text-zinc-700" aria-hidden>
          /
        </span>
      )}
      <Total />
      <TimingSourceChip />

      {children && <div className="ml-auto flex shrink-0 items-center gap-2">{children}</div>}
    </div>
  );
}
