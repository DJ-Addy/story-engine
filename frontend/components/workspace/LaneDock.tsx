"use client";

// Two lanes, not four.
//
// The old dock showed the full grid at all times: ruler, visual, dialogue,
// ambience and SFX, ~254px of the screen the picture wanted. Almost none of
// that is needed while you are watching — what you need is where the cuts fall
// and where the sound is, which is what PICTURE and SOUND are: the visual lane,
// and the three audio lanes flattened onto one row.
//
// The summary is fit-to-width on purpose. It is an overview, so it is measured
// in percentages of the scene and has no zoom; the moment you want to work at
// sample level, "Expand lanes" hands over to the real `TimelineGrid` — same
// clips, same selection, same playhead — with the inspector and the axis map
// beside it, because those are the two things a shot list can no longer say.

import { useMemo } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useSceneStore } from "@/lib/store";
import { useTimelineStore } from "@/lib/timelineStore";
import type { AmbienceBlock, DialogueClip, SfxMarker, VisualClip } from "@/lib/types";
import AxisDiagram from "@/components/AxisDiagram";
import { FailurePanel } from "@/components/casting/JudgeStatus";
import { emotionStyle, FOCUS_RING } from "@/components/casting/theme";
import Inspector from "@/components/timeline/Inspector";
import TimelineGrid from "@/components/timeline/TimelineGrid";
import { CONTENT_HEIGHT, msToClock } from "@/components/timeline/layout";
import MiniTransport from "@/components/workspace/MiniTransport";

/** Stable empty lanes, so the selectors never return a fresh array. */
const NO_VISUAL: VisualClip[] = [];
const NO_DIALOGUE: DialogueClip[] = [];
const NO_AMBIENCE: AmbienceBlock[] = [];
const NO_SFX: SfxMarker[] = [];

/** Ruler + all four lanes, exactly, when the dock is opened out. */
const EXPANDED_HEIGHT = CONTENT_HEIGHT + 2;

const pct = (ms: number, total: number) => (total > 0 ? (ms / total) * 100 : 0);

function LaneLabel({ children }: { children: string }) {
  return (
    <span className="w-[50px] shrink-0 font-mono text-[10.5px] uppercase tracking-[0.05em] text-zinc-600">
      {children}
    </span>
  );
}

/** One playhead over both summary lanes. Isolated because it is the only thing
 * in the dock that reads the clock. */
function DockPlayhead() {
  const currentMs = useTimelineStore((s) => s.currentMs);
  const durationMs = useTimelineStore((s) => s.durationMs);
  return (
    <span
      aria-hidden
      className="tl-playhead pointer-events-none absolute -top-1 bottom-[-4px] w-[1.5px]"
      style={{ left: `${pct(currentMs, durationMs)}%` }}
    />
  );
}

/** Seek by proportion of the scene. The summary has no zoom, so a click maps
 * straight from x to time with no scroll offset to account for. */
function useLaneSeek() {
  const seek = useTimelineStore((s) => s.seek);
  const durationMs = useTimelineStore((s) => s.durationMs);
  return (e: ReactPointerEvent<HTMLDivElement>) => {
    if (durationMs <= 0) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width === 0) return;
    seek(((e.clientX - rect.left) / rect.width) * durationMs);
  };
}

function PictureLane() {
  const clips = useTimelineStore((s) => s.data?.lanes.visual ?? NO_VISUAL);
  const durationMs = useTimelineStore((s) => s.durationMs);
  const selectedId = useTimelineStore((s) =>
    s.selection?.lane === "visual" ? s.selection.id : null,
  );
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const hoveredOrdinal = useSceneStore((s) => s.hoveredOrdinal);
  const hoverShot = useSceneStore((s) => s.hoverShot);
  const onSeek = useLaneSeek();

  return (
    <div className="ws-lane" onPointerDown={onSeek}>
      {clips.map((c) => {
        const on = c.id === selectedId || hoveredOrdinal === c.shotOrdinal;
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => selectAndSeek({ lane: "visual", id: c.id })}
            onPointerEnter={() => hoverShot(c.shotOrdinal)}
            onPointerLeave={() => hoverShot(null)}
            aria-label={`Shot ${c.shotOrdinal} at ${msToClock(c.startMs)}`}
            title={`Shot ${c.shotOrdinal} · ${c.label}`}
            className={`ws-lane-shot ${on ? "ws-lane-shot-on" : ""} ${FOCUS_RING}`}
            style={{
              left: `${pct(c.startMs, durationMs)}%`,
              width: `${pct(c.durationMs, durationMs)}%`,
            }}
          />
        );
      })}
    </div>
  );
}

function SoundLane() {
  const dialogue = useTimelineStore((s) => s.data?.lanes.dialogue ?? NO_DIALOGUE);
  const ambience = useTimelineStore((s) => s.data?.lanes.ambience ?? NO_AMBIENCE);
  const sfx = useTimelineStore((s) => s.data?.lanes.sfx ?? NO_SFX);
  const durationMs = useTimelineStore((s) => s.durationMs);
  const selectedId = useTimelineStore((s) =>
    s.selection?.lane === "dialogue" ? s.selection.id : null,
  );
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const onSeek = useLaneSeek();

  return (
    <div className="ws-lane" onPointerDown={onSeek}>
      {/* Ambience is the wash the lines sit on; it is read, not clicked — the
          expanded lanes are where a bed is selected and inspected. */}
      {ambience.map((b) => (
        <span
          key={b.id}
          aria-hidden
          className="ws-lane-bed"
          style={{
            left: `${pct(b.startMs, durationMs)}%`,
            width: `${pct(b.durationMs, durationMs)}%`,
          }}
        />
      ))}
      {dialogue.map((d) => (
        <button
          key={d.id}
          type="button"
          onClick={() => selectAndSeek({ lane: "dialogue", id: d.id })}
          aria-label={`${d.character} at ${msToClock(d.startMs)}`}
          title={`${d.character} — ${d.text}`}
          style={{
            left: `${pct(d.startMs, durationMs)}%`,
            width: `${pct(d.durationMs, durationMs)}%`,
            ...emotionStyle(d.emotion),
          }}
          className={`ws-lane-line ${
            d.id === selectedId ? "ws-lane-line-selected" : ""
          } ${FOCUS_RING}`}
        />
      ))}
      {sfx.map((m) => (
        <span
          key={m.id}
          aria-hidden
          title={m.name}
          className="tl-sfx-marker absolute top-[3px] h-1.5 w-0.5 rounded-sm"
          style={{ left: `${pct(m.atMs, durationMs)}%` }}
        />
      ))}
    </div>
  );
}

export default function LaneDock({
  onActivateAudio,
  loading,
  failure,
  onRetry,
  expanded,
  onExpandedChange,
}: {
  onActivateAudio: () => void;
  loading: boolean;
  failure: unknown | null;
  onRetry: () => void;
  expanded: boolean;
  onExpandedChange: (next: boolean) => void;
}) {
  const hoveredOrdinal = useSceneStore((s) => s.hoveredOrdinal);
  const hoverShot = useSceneStore((s) => s.hoverShot);

  const control = useMemo(
    () => (
      <button
        type="button"
        onClick={() => onExpandedChange(!expanded)}
        aria-expanded={expanded}
        aria-controls="ws-dock-lanes"
        className={`rounded-md px-2 py-1 text-[11px] text-zinc-500 transition-colors hover:text-zinc-200 ${FOCUS_RING}`}
      >
        {expanded ? "Collapse lanes" : "Expand lanes"}
      </button>
    ),
    [expanded, onExpandedChange],
  );

  return (
    <section aria-label="Timeline" className="cast-panel shrink-0 px-3.5 pb-3 pt-2.5">
      <MiniTransport onActivateAudio={onActivateAudio}>{control}</MiniTransport>

      <div id="ws-dock-lanes" className="mt-2.5">
        {failure !== null ? (
          <FailurePanel
            error={failure}
            onRetry={onRetry}
            retryLabel="Reload timeline"
            compact
          />
        ) : loading ? (
          <div className="cast-shimmer h-[58px] rounded-lg" aria-busy />
        ) : expanded ? (
          <div className="flex gap-2.5" style={{ height: EXPANDED_HEIGHT }}>
            <div className="min-w-0 flex-1">
              <TimelineGrid
                highlightShotOrdinal={hoveredOrdinal}
                onShotHover={hoverShot}
              />
            </div>
            {/* The two things the shot table used to carry, kept reachable
                without giving them a permanent column: what exactly is
                selected, and where the cameras stand relative to the axis. */}
            <div className="cast-scroll hidden w-[280px] shrink-0 flex-col gap-2.5 overflow-y-auto xl:flex">
              <div className="shrink-0">
                <Inspector />
              </div>
              <div className="shrink-0">
                <AxisDiagram />
              </div>
            </div>
          </div>
        ) : (
          <div className="relative flex flex-col gap-1.5">
            <div className="flex items-center gap-[11px]">
              <LaneLabel>Picture</LaneLabel>
              <PictureLane />
            </div>
            <div className="flex items-center gap-[11px]">
              <LaneLabel>Sound</LaneLabel>
              <SoundLane />
            </div>
            {/* Sits over both rows, offset by the label column. */}
            <div className="pointer-events-none absolute inset-y-0 left-[61px] right-0">
              <DockPlayhead />
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
