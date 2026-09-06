"use client";

// The same scene, read as a screenplay.
//
// This is the view that makes the IR thesis visible: the lanes next door and
// the page here are ONE graph, and nothing is re-fetched to switch between
// them. Every beat on this page is a clip on the dialogue lane, every chip in
// the margin is a clip on the visual lane, and clicking either moves the same
// playhead. Time runs down the left edge at 42px; the script body takes the
// middle; the margin at 186px carries what the script cannot say on its own —
// which shot covers these lines, how the line is delivered, and what the
// continuity validator has found there.
//
// CONTINUITY LIVES HERE. It used to be a tab in a panel nobody opened, which
// meant a note about shot 6 was three clicks from the words shot 6 covers.
// Beside the line is the only place a note like that can be acted on.
//
// The transport does not vanish; it thins to one line at the foot of the page.

import { useEffect, useMemo, useRef } from "react";
import type { Ref } from "react";
import { useSceneStore } from "@/lib/store";
import { useTimelineStore } from "@/lib/timelineStore";
import type { DialogueClip, Finding, VisualClip } from "@/lib/types";
import { FailurePanel } from "@/components/casting/JudgeStatus";
import { emotionStyle, FOCUS_RING } from "@/components/casting/theme";
import { msToClock } from "@/components/timeline/layout";
import MiniTransport from "@/components/workspace/MiniTransport";
import {
  activeLineId,
  buildBeats,
  cueColor,
  NARRATOR,
  type ScriptBeat,
} from "@/components/workspace/scriptBeats";

/** Stable empty lanes, so the selectors never return a fresh array. */
const NO_DIALOGUE: DialogueClip[] = [];
const NO_VISUAL: VisualClip[] = [];

function ShotChip({ shot, active }: { shot: VisualClip; active: boolean }) {
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const hoverShot = useSceneStore((s) => s.hoverShot);
  return (
    <button
      type="button"
      onClick={() => selectAndSeek({ lane: "visual", id: shot.id })}
      onPointerEnter={() => hoverShot(shot.shotOrdinal)}
      onPointerLeave={() => hoverShot(null)}
      title={shot.label}
      className={`inline-flex max-w-full items-center gap-[7px] rounded-lg border px-2.5 py-[5px] text-[11px] transition-colors ${
        active
          ? "border-amber-500/30 bg-amber-400/[0.09] text-amber-200"
          : "border-[var(--hairline)] bg-[var(--surface-2)] text-zinc-400 hover:border-[var(--hairline-strong)] hover:text-zinc-200"
      } ${FOCUS_RING}`}
    >
      <span className="font-mono text-zinc-500">{shot.shotOrdinal}</span>
      <span className="truncate uppercase">{shot.size}</span>
      <span className="truncate text-zinc-500">· {shot.label}</span>
    </button>
  );
}

/** A continuity finding, said where it happened. Marking one deliberate is the
 * one edit the margin allows, because it is the answer to the note rather than
 * a change to the scene — and without it a known-good axis cross would nag for
 * ever. */
function ContinuityNote({
  finding,
  showShot = false,
}: {
  finding: Finding;
  /** Name the shot. Only needed away from the margin, where the note is not
   * already sitting beside the lines that shot covers. */
  showShot?: boolean;
}) {
  const markDeliberate = useSceneStore((s) => s.markDeliberate);
  const tone =
    finding.severity === "error"
      ? "text-rose-300"
      : finding.severity === "warn"
        ? "text-amber-300"
        : "text-sky-300";
  return (
    <div className={`mt-[7px] flex items-start gap-1.5 ${finding.deliberate ? "opacity-45" : ""}`}>
      <span
        aria-hidden
        className={`mt-[5px] h-[5px] w-[5px] shrink-0 rounded-full ${
          finding.severity === "error"
            ? "bg-rose-400"
            : finding.severity === "warn"
              ? "bg-amber-400"
              : "bg-sky-400"
        }`}
      />
      <div className="min-w-0">
        <p className={`text-[10.5px] leading-snug ${tone}`}>
          {showShot && finding.shot_ordinal !== null && (
            <span className="font-mono text-zinc-500">
              shot {finding.shot_ordinal} ·{" "}
            </span>
          )}
          {finding.message}
        </p>
        <button
          type="button"
          onClick={() =>
            markDeliberate(finding.id, !finding.deliberate, finding.deliberate_note)
          }
          className={`mt-0.5 rounded text-[10px] text-zinc-600 underline-offset-2 transition-colors hover:text-zinc-300 hover:underline ${FOCUS_RING}`}
        >
          {finding.deliberate ? "deliberate — undo" : "mark deliberate"}
        </button>
      </div>
    </div>
  );
}

function Beat({
  beat,
  active,
  findings,
  scrollRef,
}: {
  beat: ScriptBeat;
  active: boolean;
  findings: Finding[];
  scrollRef: Ref<HTMLDivElement> | undefined;
}) {
  const selectAndSeek = useTimelineStore((s) => s.selectAndSeek);
  const { line, shot, opensShot } = beat;
  const isAction = line.character === NARRATOR;

  return (
    <div ref={scrollRef} className="flex items-start gap-[18px]">
      <div className="w-[42px] shrink-0 pt-[3px] text-right">
        <span
          className={`font-mono text-[10.5px] tabular-nums ${
            active ? "text-amber-400" : "text-zinc-700"
          }`}
        >
          {msToClock(line.startMs)}
        </span>
      </div>

      <button
        type="button"
        onClick={() => selectAndSeek({ lane: "dialogue", id: line.id })}
        className={`min-w-0 flex-1 rounded-r-sm border-l-2 py-0.5 pl-[10px] text-left transition-colors ${
          active ? "border-amber-400" : "border-transparent hover:border-white/10"
        } ${FOCUS_RING}`}
      >
        {!isAction && (
          <span
            className="mb-1 block text-[11px] uppercase tracking-[0.09em]"
            style={{ color: cueColor(line.character) }}
          >
            {line.character}
          </span>
        )}
        <span
          className={
            isAction
              ? "block text-[13.5px] leading-[1.75] text-zinc-400"
              : "block text-[14px] leading-[1.7] text-zinc-200"
          }
        >
          {line.text}
        </span>
      </button>

      <div className="w-[186px] shrink-0 pt-px">
        {shot && opensShot && <ShotChip shot={shot} active={active} />}
        {!isAction && line.emotion !== "neutral" && (
          <div className="mt-[7px] flex flex-wrap gap-1.5">
            <span
              className="emotion-chip rounded-full px-2 py-[3px] text-[10.5px]"
              style={emotionStyle(line.emotion)}
            >
              {line.emotion}
            </span>
          </div>
        )}
        {opensShot && findings.map((f) => <ContinuityNote key={f.id} finding={f} />)}
      </div>
    </div>
  );
}

export default function ScriptView({
  onActivateAudio,
  loading,
  failure,
  onRetry,
}: {
  onActivateAudio: () => void;
  loading: boolean;
  failure: unknown | null;
  onRetry: () => void;
}) {
  const dialogue = useTimelineStore((s) => s.data?.lanes.dialogue ?? NO_DIALOGUE);
  const visual = useTimelineStore((s) => s.data?.lanes.visual ?? NO_VISUAL);
  const sceneTitle = useTimelineStore((s) => s.data?.sceneTitle ?? null);
  const activeId = useTimelineStore((s) =>
    activeLineId(s.data?.lanes.dialogue ?? NO_DIALOGUE, s.currentMs),
  );
  const findings = useSceneStore((s) => s.findings);

  const beats = useMemo(() => buildBeats(dialogue, visual), [dialogue, visual]);

  const findingsByShot = useMemo(() => {
    const map = new Map<number, Finding[]>();
    for (const f of findings) {
      if (f.shot_ordinal === null) continue;
      const list = map.get(f.shot_ordinal) ?? [];
      list.push(f);
      map.set(f.shot_ordinal, list);
    }
    return map;
  }, [findings]);

  // Not every finding has a line to sit beside: some are about the scene as a
  // whole, and a shot that covers no dialogue never opens a beat, so its chip —
  // and its notes — would have nowhere to go. Those are collected and stated
  // once at the top, because a continuity note that silently disappears is
  // worse than one in the wrong place.
  const unplaced = useMemo(() => {
    const placed = new Set<number>();
    for (const b of beats) {
      if (b.opensShot && b.shot) placed.add(b.shot.shotOrdinal);
    }
    return findings.filter(
      (f) => f.shot_ordinal === null || !placed.has(f.shot_ordinal),
    );
  }, [beats, findings]);

  // Follow the playhead down the page. Only fires when the beat CHANGES, so
  // reading ahead with the transport parked never fights the scroll.
  const activeRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="cast-scroll min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-6">
        {failure !== null ? (
          <FailurePanel error={failure} onRetry={onRetry} retryLabel="Reload scene" />
        ) : loading ? (
          <div className="flex flex-col gap-5" aria-busy>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="cast-shimmer h-12 rounded-lg" />
            ))}
          </div>
        ) : beats.length === 0 ? (
          <div className="pt-16 text-center">
            <p className="text-sm text-zinc-300">Nothing has been written here yet</p>
            <p className="mx-auto mt-2 max-w-sm text-xs leading-relaxed text-zinc-600">
              This scene has no attributed lines, so there is no script to lay out.
              Ingest a screenplay for this project and the beats appear here.
            </p>
          </div>
        ) : (
          <article className="flex flex-col gap-[22px]">
            {sceneTitle && (
              <h1 className="pl-[60px] font-mono text-[11px] uppercase tracking-[0.18em] text-zinc-500">
                {sceneTitle}
              </h1>
            )}
            {unplaced.length > 0 && (
              <div className="ml-[60px] max-w-xl rounded-lg border border-amber-500/20 bg-amber-500/[0.05] px-3 pb-2 pt-1">
                {unplaced.map((f) => (
                  <ContinuityNote key={f.id} finding={f} showShot />
                ))}
              </div>
            )}
            {beats.map((b) => (
              <Beat
                key={b.line.id}
                beat={b}
                active={b.line.id === activeId}
                findings={
                  b.shot ? (findingsByShot.get(b.shot.shotOrdinal) ?? []) : []
                }
                scrollRef={b.line.id === activeId ? activeRef : undefined}
              />
            ))}
          </article>
        )}
      </div>

      <div className="shrink-0 px-7 pb-4 pt-3">
        <div className="cast-panel px-4 py-2.5">
          <MiniTransport onActivateAudio={onActivateAudio} scrub />
        </div>
      </div>
    </div>
  );
}
