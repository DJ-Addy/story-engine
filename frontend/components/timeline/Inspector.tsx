"use client";

// Selection inspector. Resolves the current {lane, id} selection against the
// loaded timeline and shows that clip's metadata. Read-only; selection + seek
// happen on the clips themselves.

import type { ReactNode } from "react";
import { useTimelineStore, videoKey } from "@/lib/timelineStore";
import { emotionStyle } from "@/components/casting/theme";
import { LANE_META, msToClock } from "@/components/timeline/layout";

/** The shot's render state, in the same words the program monitor uses, so the
 * two surfaces never disagree about whether a picture exists. */
const VIDEO_LABEL: Record<string, { text: string; tone: string }> = {
  unknown: { text: "checking…", tone: "text-zinc-500" },
  probing: { text: "checking…", tone: "text-zinc-500" },
  none: { text: "not rendered", tone: "text-zinc-400" },
  rendering: { text: "rendering…", tone: "text-amber-300" },
  ready: { text: "render available", tone: "text-emerald-300" },
  error: { text: "render failed", tone: "text-rose-300" },
};

function VideoRow({ shotOrdinal }: { shotOrdinal: number }) {
  const sceneOrdinal = useTimelineStore((s) => s.data?.sceneOrdinal ?? null);
  const status = useTimelineStore((s) =>
    sceneOrdinal === null
      ? "unknown"
      : (s.videos[videoKey(sceneOrdinal, shotOrdinal)]?.status ?? "unknown"),
  );
  const label = VIDEO_LABEL[status] ?? VIDEO_LABEL.unknown;
  return (
    <Row label="Video">
      <span className={label.tone}>{label.text}</span>
    </Row>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-[var(--hairline)] py-2 last:border-b-0">
      <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </span>
      <span className="min-w-0 text-right text-xs text-zinc-200">{children}</span>
    </div>
  );
}

function Header({ lane, title }: { lane: string; title: string }) {
  const meta = LANE_META[lane as keyof typeof LANE_META];
  return (
    <div className="mb-2 flex items-center gap-2">
      <span
        className="h-2 w-2 rounded-full"
        style={{ backgroundColor: `rgb(${meta?.accent ?? "var(--emotion-neutral)"})` }}
      />
      <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
        {meta?.label ?? lane}
      </span>
      <span className="ml-auto truncate font-mono text-xs text-zinc-100">
        {title}
      </span>
    </div>
  );
}

export default function Inspector() {
  const data = useTimelineStore((s) => s.data);
  const selection = useTimelineStore((s) => s.selection);

  let body: ReactNode = null;

  if (data && selection) {
    if (selection.lane === "visual") {
      const c = data.lanes.visual.find((x) => x.id === selection.id);
      if (c)
        body = (
          <>
            <Header lane="visual" title={`Shot #${c.shotOrdinal}`} />
            <Row label="Size">
              <span className="font-mono uppercase">{c.size}</span>
            </Row>
            <Row label="Subjects">{c.subjects.join(", ") || "—"}</Row>
            <Row label="Intent">
              <span className="text-zinc-400">{c.label}</span>
            </Row>
            <Row label="Timing">
              <span className="font-mono">
                {msToClock(c.startMs)} · {(c.durationMs / 1000).toFixed(1)}s
              </span>
            </Row>
            <VideoRow shotOrdinal={c.shotOrdinal} />
          </>
        );
    } else if (selection.lane === "dialogue") {
      const c = data.lanes.dialogue.find((x) => x.id === selection.id);
      if (c)
        body = (
          <>
            <Header lane="dialogue" title={c.character} />
            <Row label="Emotion">
              <span
                style={emotionStyle(c.emotion)}
                className="emotion-chip rounded-full px-2 py-0.5 font-mono text-[10px] leading-none"
              >
                {c.emotion}
              </span>
            </Row>
            <Row label="Line">
              <span className="text-zinc-300">{c.text}</span>
            </Row>
            <Row label="Timing">
              <span className="font-mono">
                {msToClock(c.startMs)} · {(c.durationMs / 1000).toFixed(1)}s
              </span>
            </Row>
          </>
        );
    } else if (selection.lane === "ambience") {
      const b = data.lanes.ambience.find((x) => x.id === selection.id);
      if (b)
        body = (
          <>
            <Header lane="ambience" title={b.tag} />
            <Row label="Bed">{b.tag}</Row>
            <Row label="Ducked">
              <span className={b.ducked ? "text-amber-300" : "text-zinc-400"}>
                {b.ducked ? "yes — under dialogue" : "no"}
              </span>
            </Row>
            <Row label="Timing">
              <span className="font-mono">
                {msToClock(b.startMs)} · {(b.durationMs / 1000).toFixed(1)}s
              </span>
            </Row>
          </>
        );
    } else if (selection.lane === "sfx") {
      const m = data.lanes.sfx.find((x) => x.id === selection.id);
      if (m)
        body = (
          <>
            <Header lane="sfx" title={m.name} />
            <Row label="Cue">{m.name}</Row>
            <Row label="At">
              <span className="font-mono">{msToClock(m.atMs)}</span>
            </Row>
          </>
        );
    }
  }

  return (
    <div className="cast-panel flex h-full min-h-[168px] flex-col p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="h-1.5 w-1.5 rounded-full bg-sky-400" aria-hidden />
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
          Inspector
        </h2>
      </div>
      {body ?? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
          <p className="text-sm text-zinc-400">Nothing selected</p>
          <p className="max-w-xs text-xs leading-relaxed text-zinc-600">
            Click any clip, line, ambience bed, or SFX cue to inspect it and jump
            the playhead to its start.
          </p>
        </div>
      )}
    </div>
  );
}
