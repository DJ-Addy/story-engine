"use client";

import { useEffect } from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { api } from "@/lib/api";
import { useTimelineStore } from "@/lib/timelineStore";
import { FOCUS_RING } from "@/components/casting/theme";
import TransportBar from "@/components/timeline/TransportBar";
import TimelineGrid from "@/components/timeline/TimelineGrid";
import Inspector from "@/components/timeline/Inspector";
import AiAssistStrip from "@/components/timeline/AiAssistStrip";
import { useTimelineAudio } from "@/components/timeline/useTimelineAudio";
import { useTransportClock } from "@/components/timeline/useTransportClock";
import { msToClock } from "@/components/timeline/layout";

export default function TimelinePage() {
  const reduce = useReducedMotion();
  const load = useTimelineStore((s) => s.load);
  const data = useTimelineStore((s) => s.data);

  // Placeholder audio engine + the virtual transport clock. See
  // useTimelineAudio for the SEAM to the real rendered WAV.
  const { engineRef, activate } = useTimelineAudio();
  useTransportClock(engineRef);

  // Load the (mock) timeline through the api seam, mirroring /casting.
  useEffect(() => {
    let cancelled = false;
    // Real: GET /api/v1/projects/{id}/render/audio (rendered WAV) + the shot
    // list for the visual track; here it's served from fixtures.
    api.getTimeline("demo").then((d) => {
      if (cancelled) return;
      load(d);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  // Global transport keyboard: space = play/pause, arrows nudge. Ignored while
  // typing in a form control.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.tagName === "SELECT" ||
          t.isContentEditable)
      )
        return;
      const store = useTimelineStore.getState();
      if (!store.data) return;
      if (e.code === "Space" || e.key === " ") {
        e.preventDefault();
        activate();
        store.togglePlay();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        store.nudge(e.shiftKey ? -5000 : -1000);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        store.nudge(e.shiftKey ? 5000 : 1000);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [activate]);

  const laneCounts = data
    ? [
        { value: data.lanes.visual.length, label: "shots" },
        { value: data.lanes.dialogue.length, label: "lines" },
        { value: data.lanes.ambience.length, label: "beds" },
        { value: data.lanes.sfx.length, label: "sfx" },
      ]
    : [];

  const fade = (delay: number) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, y: 14 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.5, delay, ease: [0.16, 1, 0.3, 1] as const },
        };

  return (
    <div className="tl-shell flex min-h-screen flex-col overflow-x-hidden text-zinc-200">
      <header className="sticky top-0 z-40 border-b border-[var(--hairline)] bg-[var(--cast-bg)]/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-[1600px] items-center gap-3 px-5 py-3 sm:px-8">
          <Link
            href="/"
            className={`rounded-sm text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
          >
            Story Engine
          </Link>
          <span className="text-zinc-700" aria-hidden>
            /
          </span>
          <h1 className="font-mono text-xs text-zinc-100">Timeline</h1>
          {data && (
            <span className="hidden rounded border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 md:inline">
              {data.sceneTitle}
            </span>
          )}
          <div className="ml-auto flex items-center gap-3">
            <Link
              href="/casting"
              className={`rounded-sm text-xs text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
            >
              Casting →
            </Link>
            <Link
              href="/scenes/demo"
              className={`rounded-sm text-xs text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
            >
              Scene workspace →
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-4 px-5 py-6 sm:px-8">
        {/* Scene band */}
        <motion.div {...fade(0)} className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-amber-400">
              Scrubbable timeline
            </p>
            <h2 className="mt-2 truncate text-2xl font-semibold tracking-tight text-zinc-50 md:text-3xl">
              {data ? data.sceneTitle : "Loading timeline…"}
            </h2>
            <p className="mt-1 max-w-xl text-xs leading-relaxed text-zinc-500">
              The audiobook and animatic on one axis — play, scrub, and inspect.
              Runs on mock data with a procedural placeholder mix (muted by
              default); the rendered WAV wires in later.
            </p>
          </div>
          <dl className="flex shrink-0 items-center gap-5">
            <div>
              <dt className="sr-only">total duration</dt>
              <dd className="font-mono text-2xl font-semibold tracking-tight text-zinc-100">
                {data ? msToClock(data.durationMs) : "--:--"}
              </dd>
              <p className="mt-0.5 text-[11px] text-zinc-600">total</p>
            </div>
            {laneCounts.map((c) => (
              <div key={c.label}>
                <dt className="sr-only">{c.label}</dt>
                <dd className="font-mono text-2xl font-semibold tracking-tight text-zinc-100">
                  {c.value}
                </dd>
                <p className="mt-0.5 text-[11px] text-zinc-600">{c.label}</p>
              </div>
            ))}
          </dl>
        </motion.div>

        <motion.div {...fade(0.06)}>
          <TransportBar onActivateAudio={activate} />
        </motion.div>

        <motion.div {...fade(0.12)}>
          <AiAssistStrip />
        </motion.div>

        <motion.div {...fade(0.18)}>
          <TimelineGrid />
        </motion.div>

        <motion.div {...fade(0.24)}>
          <Inspector />
        </motion.div>

        <p className="text-center font-mono text-[10px] text-zinc-700">
          space play/pause · ←/→ nudge (shift = ×5) · drag the ruler or playhead
          to scrub
        </p>
      </main>
    </div>
  );
}
