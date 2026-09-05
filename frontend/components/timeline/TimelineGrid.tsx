"use client";

// Assembles the ruler + four lanes under one horizontal time axis, and owns the
// scrub interaction. Left column = sticky lane labels; right column = the single
// horizontally-scrolling track (never the page body). The playhead lives inside
// the scrolling content so it tracks with it. Only Playhead/ClockReadout read
// currentMs, so scrolling and playback don't re-render the lanes.
//
// In the workspace the grid is docked at the bottom of a fixed-height page, so
// it fills its container and takes any leftover VERTICAL overflow on itself
// (`overflow-y-auto` wrapping both columns, so labels and lanes scroll as one).

import { useRef } from "react";
import type { CSSProperties, PointerEvent } from "react";
import { useTimelineStore } from "@/lib/timelineStore";
import {
  CONTENT_HEIGHT,
  LABEL_WIDTH,
  LANE_HEIGHT,
  LANE_META,
  LANE_ORDER,
  RULER_HEIGHT,
  msToPx,
  pxToMs,
} from "@/components/timeline/layout";
import TimelineRuler from "@/components/timeline/TimelineRuler";
import Playhead from "@/components/timeline/Playhead";
import {
  AmbienceLane,
  DialogueLane,
  SfxLane,
  VisualLane,
} from "@/components/timeline/Lanes";

export default function TimelineGrid({
  highlightShotOrdinal = null,
  onShotHover,
}: {
  /** Shot ordinal to ring on the visual lane — the workspace passes the shot
   * hovered in the shot list, so the two surfaces point at the same shot. */
  highlightShotOrdinal?: number | null;
  /** Called as the pointer enters/leaves a visual clip, with that clip's shot
   * ordinal (null on leave). Drives the reverse highlight in the shot list. */
  onShotHover?: (shotOrdinal: number | null) => void;
} = {}) {
  const data = useTimelineStore((s) => s.data);
  const durationMs = useTimelineStore((s) => s.durationMs);
  const pxPerSecond = useTimelineStore((s) => s.pxPerSecond);
  const seek = useTimelineStore((s) => s.seek);

  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const scrubbing = useRef(false);

  const clientXToMs = (clientX: number) => {
    const el = contentRef.current;
    if (!el) return 0;
    // getBoundingClientRect().left already accounts for horizontal scroll.
    const rect = el.getBoundingClientRect();
    return pxToMs(clientX - rect.left, pxPerSecond);
  };

  // Shared by the ruler surface and the playhead grip. Pointer capture keeps the
  // drag alive even if the pointer leaves the element.
  const scrubHandlers = {
    onPointerDown: (e: PointerEvent) => {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
      scrubbing.current = true;
      seek(clientXToMs(e.clientX));
    },
    onPointerMove: (e: PointerEvent) => {
      if (!scrubbing.current) return;
      seek(clientXToMs(e.clientX));
    },
    onPointerUp: (e: PointerEvent) => {
      scrubbing.current = false;
      (e.currentTarget as Element).releasePointerCapture?.(e.pointerId);
    },
  };

  if (!data) {
    return <div className="cast-panel cast-shimmer h-full min-h-[120px]" aria-busy />;
  }

  const contentWidth = Math.max(msToPx(durationMs, pxPerSecond), 320);

  const laneBody = {
    visual: (
      <VisualLane
        clips={data.lanes.visual}
        pxPerSecond={pxPerSecond}
        highlightShotOrdinal={highlightShotOrdinal}
        onShotHover={onShotHover}
      />
    ),
    dialogue: <DialogueLane clips={data.lanes.dialogue} pxPerSecond={pxPerSecond} />,
    ambience: <AmbienceLane blocks={data.lanes.ambience} pxPerSecond={pxPerSecond} />,
    sfx: <SfxLane markers={data.lanes.sfx} pxPerSecond={pxPerSecond} />,
  } as const;

  return (
    <div className="cast-panel h-full overflow-hidden">
      {/* Vertical overflow (a short dock) scrolls both columns as one. */}
      <div className="cast-scroll h-full overflow-y-auto">
        <div className="flex min-h-full">
          {/* Sticky lane labels */}
          <div
            className="shrink-0 border-r border-[var(--hairline)]"
            style={{ width: LABEL_WIDTH }}
          >
            <div
              className="tl-lane-label flex items-end px-3 pb-1"
              style={{ height: RULER_HEIGHT }}
            >
              <span className="font-mono text-[9px] uppercase tracking-wider text-zinc-600">
                time
              </span>
            </div>
            {LANE_ORDER.map((k) => (
              <div
                key={k}
                className="tl-lane-label flex flex-col justify-center px-3"
                style={{ height: LANE_HEIGHT[k] }}
              >
                <div className="flex items-center gap-1.5">
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ backgroundColor: `rgb(${LANE_META[k].accent})` }}
                  />
                  <span className="font-mono text-[10px] font-semibold tracking-wide text-zinc-200">
                    {LANE_META[k].label}
                  </span>
                </div>
                <span className="mt-0.5 pl-3 font-mono text-[9px] text-zinc-600">
                  {LANE_META[k].hint}
                </span>
              </div>
            ))}
          </div>

          {/* Scrollable track (horizontal scroll stays inside this container) */}
          <div
            ref={scrollRef}
            className="cast-scroll min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
          >
            <div
              ref={contentRef}
              className="relative"
              style={{ width: contentWidth, height: CONTENT_HEIGHT }}
            >
              {/* Ruler doubles as the scrub surface */}
              <div
                className="absolute inset-x-0 top-0 cursor-pointer touch-none"
                style={{ height: RULER_HEIGHT }}
                {...scrubHandlers}
              >
                <TimelineRuler
                  durationMs={durationMs}
                  pxPerSecond={pxPerSecond}
                  scenes={data.scenes}
                  width={contentWidth}
                />
              </div>

              {/* Lanes */}
              <div className="absolute inset-x-0" style={{ top: RULER_HEIGHT }}>
                {LANE_ORDER.map((k) => (
                  <div
                    key={k}
                    className="tl-lane-row relative"
                    style={
                      {
                        height: LANE_HEIGHT[k],
                        ["--tl-grid-step"]: `${pxPerSecond}px`,
                      } as CSSProperties
                    }
                  >
                    {laneBody[k]}
                  </div>
                ))}
              </div>

              {/* One playhead across ruler + every lane */}
              <Playhead scrollContainerRef={scrollRef} scrubHandlers={scrubHandlers} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
