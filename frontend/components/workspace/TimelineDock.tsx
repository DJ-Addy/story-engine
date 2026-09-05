"use client";

// The timeline, docked to the bottom of the workspace for good: transport on
// top, ruler + four lanes underneath, always on the same screen as the shot
// list and the program monitor.
//
// It is resizable (drag or arrow-key the grip) and collapsible (the lanes fold
// away, the transport never does), because vertical space is the whole risk of
// this layout: on a short laptop the grid gives up height first, and whatever
// is left scrolls INSIDE the grid rather than scrolling the page.

import { useCallback, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import TransportBar from "@/components/timeline/TransportBar";
import TimelineGrid from "@/components/timeline/TimelineGrid";
import { CONTENT_HEIGHT } from "@/components/timeline/layout";
import { FailurePanel } from "@/components/casting/JudgeStatus";
import { FOCUS_RING } from "@/components/casting/theme";

/** Ruler + all four lanes, exactly. The dock opens showing the whole timeline. */
const DEFAULT_HEIGHT = CONTENT_HEIGHT + 2;
const MIN_HEIGHT = 108;
const STEP = 24;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Never let the dock eat the monitor: at most ~55% of the viewport. */
const maxHeight = () =>
  typeof window === "undefined"
    ? DEFAULT_HEIGHT
    : Math.max(MIN_HEIGHT, Math.round(window.innerHeight * 0.55));

export default function TimelineDock({
  onActivateAudio,
  loading,
  failure,
  onRetry,
  highlightShotOrdinal,
  onShotHover,
}: {
  onActivateAudio: () => void;
  loading: boolean;
  failure: unknown | null;
  onRetry: () => void;
  highlightShotOrdinal: number | null;
  onShotHover: (shotOrdinal: number | null) => void;
}) {
  const [height, setHeight] = useState(DEFAULT_HEIGHT);
  const [collapsed, setCollapsed] = useState(false);
  const drag = useRef<{ y: number; h: number } | null>(null);

  const onPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      if (collapsed) return;
      e.preventDefault();
      e.currentTarget.setPointerCapture?.(e.pointerId);
      drag.current = { y: e.clientY, h: height };
    },
    [collapsed, height],
  );

  const onPointerMove = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (!start) return;
    // Dragging UP grows the dock, so the delta is inverted.
    setHeight(clamp(start.h + (start.y - e.clientY), MIN_HEIGHT, maxHeight()));
  }, []);

  const endDrag = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  }, []);

  const failed = failure !== null;

  return (
    <section
      aria-label="Timeline"
      className="shrink-0 border-t border-[var(--hairline)] bg-black/25"
    >
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the timeline"
        tabIndex={0}
        data-local-arrow-keys="true"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setHeight((h) => clamp(h + STEP, MIN_HEIGHT, maxHeight()));
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            setHeight((h) => clamp(h - STEP, MIN_HEIGHT, maxHeight()));
          }
        }}
        className={`ws-dock-resizer flex h-2.5 touch-none items-center justify-center ${
          collapsed ? "" : "cursor-row-resize"
        } ${FOCUS_RING}`}
      >
        <span className="ws-dock-grip h-0.5 w-14 rounded-full" aria-hidden />
      </div>

      <div className="flex items-stretch gap-2 px-4 sm:px-6">
        <div className="min-w-0 flex-1">
          <TransportBar onActivateAudio={onActivateAudio} />
        </div>
        <button
          onClick={() => setCollapsed((c) => !c)}
          aria-expanded={!collapsed}
          className={`shrink-0 rounded-md border border-[var(--hairline)] bg-[var(--surface-3)] px-2.5 font-mono text-[10px] uppercase tracking-wider text-zinc-400 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-100 ${FOCUS_RING}`}
        >
          {collapsed ? "▴ lanes" : "▾ lanes"}
        </button>
      </div>

      {!collapsed && (
        <div className="px-4 pb-3 pt-2 sm:px-6">
          {failed ? (
            <FailurePanel
              error={failure}
              onRetry={onRetry}
              retryLabel="Reload timeline"
              compact
            />
          ) : (
            <div style={{ height }}>
              {loading ? (
                <div className="cast-panel cast-shimmer h-full" aria-busy />
              ) : (
                <TimelineGrid
                  highlightShotOrdinal={highlightShotOrdinal}
                  onShotHover={onShotHover}
                />
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
