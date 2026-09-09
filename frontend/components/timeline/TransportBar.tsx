"use client";

import { motion, useReducedMotion } from "motion/react";
import { useTimelineStore } from "@/lib/timelineStore";
import { FOCUS_RING } from "@/components/casting/theme";
import { msToClock } from "@/components/timeline/layout";

/** Isolated so only the time readout re-renders on every clock frame. */
function ClockReadout() {
  const currentMs = useTimelineStore((s) => s.currentMs);
  const durationMs = useTimelineStore((s) => s.durationMs);
  const hasData = useTimelineStore((s) => s.data !== null);
  return (
    <div className="flex items-baseline gap-1.5 font-mono tabular-nums">
      <span className="text-base text-zinc-100">
        {hasData ? msToClock(currentMs) : "--:--"}
      </span>
      <span className="text-zinc-600">/</span>
      <span className="text-xs text-zinc-500">
        {hasData ? msToClock(durationMs) : "--:--"}
      </span>
    </div>
  );
}

/**
 * Says whether the onsets on screen were measured or planned.
 *
 * Only shown when they are estimated. A rendered timeline is the expected case
 * and needs no announcement, but an estimate silently passing as a measurement
 * is the failure worth guarding against — the lanes are real, the durations are
 * a reading-speed heuristic, and everything shifts once audio is rendered.
 */
export function TimingSourceChip() {
  const timingSource = useTimelineStore((s) => s.data?.timingSource ?? null);
  if (timingSource !== "estimated") return null;
  return (
    <span
      title="Planned from the script, not measured. Render this scene's audio for exact onsets."
      className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/25 bg-amber-500/10 px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-amber-300/90"
    >
      <span aria-hidden>◷</span>
      estimated timings
    </span>
  );
}

const CTRL =
  "flex h-8 w-8 items-center justify-center rounded-md border border-[var(--hairline)] bg-[var(--surface-3)] text-zinc-300 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-50 disabled:cursor-not-allowed disabled:opacity-40";

export default function TransportBar({
  onActivateAudio,
}: {
  onActivateAudio: () => void;
}) {
  const reduce = useReducedMotion();
  const isPlaying = useTimelineStore((s) => s.isPlaying);
  const muted = useTimelineStore((s) => s.muted);
  const audioAvailable = useTimelineStore((s) => s.audioAvailable);
  const pxPerSecond = useTimelineStore((s) => s.pxPerSecond);
  const hasData = useTimelineStore((s) => s.data !== null);

  const togglePlay = useTimelineStore((s) => s.togglePlay);
  const stop = useTimelineStore((s) => s.stop);
  const toggleMuted = useTimelineStore((s) => s.toggleMuted);
  const zoomIn = useTimelineStore((s) => s.zoomIn);
  const zoomOut = useTimelineStore((s) => s.zoomOut);
  const setZoom = useTimelineStore((s) => s.setZoom);

  const handlePlay = () => {
    onActivateAudio();
    togglePlay();
  };
  const handleMute = () => {
    if (muted) onActivateAudio(); // unmuting is a valid gesture to resume audio
    toggleMuted();
  };

  const tap = reduce ? undefined : { scale: 0.94 };

  return (
    <div className="cast-panel flex flex-wrap items-center gap-3 px-3 py-2.5">
      {/* Play / pause / stop */}
      <div className="flex items-center gap-1.5">
        <motion.button
          whileTap={tap}
          onClick={handlePlay}
          disabled={!hasData}
          aria-label={isPlaying ? "Pause" : "Play"}
          className={`flex h-9 w-9 items-center justify-center rounded-md bg-amber-400 text-zinc-950 shadow-[0_0_24px_-10px_rgba(251,191,36,0.7)] transition-colors hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none ${FOCUS_RING}`}
        >
          {isPlaying ? (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden>
              <rect x="6" y="5" width="4" height="14" rx="1" />
              <rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden>
              <path d="M8 5.14v13.72a1 1 0 0 0 1.54.84l10.29-6.86a1 1 0 0 0 0-1.68L9.54 4.3A1 1 0 0 0 8 5.14Z" />
            </svg>
          )}
        </motion.button>
        <motion.button
          whileTap={tap}
          onClick={stop}
          disabled={!hasData}
          aria-label="Stop"
          className={`${CTRL} ${FOCUS_RING}`}
        >
          <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
            <rect x="6" y="6" width="12" height="12" rx="1.5" />
          </svg>
        </motion.button>
      </div>

      <div className="h-6 w-px bg-[var(--hairline)]" aria-hidden />

      <ClockReadout />
      <TimingSourceChip />

      <div className="ml-auto flex items-center gap-3">
        {/* Zoom */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={zoomOut}
            disabled={!hasData}
            aria-label="Zoom out"
            className={`${CTRL} ${FOCUS_RING}`}
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden>
              <path d="M5 12h14" />
            </svg>
          </button>
          <button
            onClick={() => setZoom(80)}
            disabled={!hasData}
            title="Reset zoom"
            className={`min-w-[52px] rounded-md border border-[var(--hairline)] bg-[var(--surface-3)] px-2 py-1 font-mono text-[10px] text-zinc-400 transition-colors hover:text-zinc-100 disabled:opacity-40 ${FOCUS_RING}`}
          >
            {pxPerSecond}px/s
          </button>
          <button
            onClick={zoomIn}
            disabled={!hasData}
            aria-label="Zoom in"
            className={`${CTRL} ${FOCUS_RING}`}
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden>
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
        </div>

        <div className="h-6 w-px bg-[var(--hairline)]" aria-hidden />

        {/* Mute. Sound is ON by default: the timeline plays the rendered mix.
            When the scene has no render there is nothing to mute, and the
            button says so instead of implying a sound that does not exist. */}
        <motion.button
          whileTap={tap}
          onClick={handleMute}
          disabled={!hasData || !audioAvailable}
          aria-pressed={!muted}
          title={
            audioAvailable
              ? undefined
              : "No audio rendered for this scene yet — render it from the pipeline"
          }
          aria-label={muted ? "Unmute the rendered mix" : "Mute the rendered mix"}
          className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors disabled:opacity-40 ${
            muted
              ? "border-[var(--hairline)] bg-[var(--surface-3)] text-zinc-400 hover:text-zinc-100"
              : "border-amber-500/40 bg-amber-500/10 text-amber-200"
          } ${FOCUS_RING}`}
        >
          {muted ? (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M11 5 6 9H3v6h3l5 4V5Z" />
              <path d="m17 9 4 6M21 9l-4 6" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M11 5 6 9H3v6h3l5 4V5Z" />
              <path d="M16 9a4 4 0 0 1 0 6M19 6a8 8 0 0 1 0 12" />
            </svg>
          )}
          <span className="font-mono text-[10px] uppercase tracking-wider">
            {!audioAvailable ? "no render" : muted ? "muted" : "sound"}
          </span>
        </motion.button>
      </div>
    </div>
  );
}
