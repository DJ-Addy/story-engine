"use client";

import { useMemo } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useCastingStore } from "@/lib/castingStore";
import { ScoreDial } from "@/components/casting/ScoreMeter";

/**
 * Cinematic scene band for the Casting Studio. Presentation only: it reads the
 * roster / voices / current fit from the store to summarize the scene and mirror
 * the live overall casting score — it never mutates state.
 */
export default function SceneHero() {
  const reduce = useReducedMotion();
  const title = useCastingStore((s) => s.title);
  const characters = useCastingStore((s) => s.characters);
  const voices = useCastingStore((s) => s.voices);
  const casting = useCastingStore((s) => s.casting);
  const fit = useCastingStore((s) => s.fit);
  const fitStale = useCastingStore((s) => s.fitStale);

  const { name, tag } = useMemo(() => {
    if (!title) return { name: "Casting Studio", tag: "" };
    const [head, ...rest] = title.split("—");
    return { name: head.trim(), tag: rest.join("—").trim() };
  }, [title]);

  const totalLines = useMemo(
    () => characters.reduce((sum, c) => sum + c.dialogue_lines, 0),
    [characters],
  );
  const castCount = Object.keys(casting).length;

  const stats = [
    { value: characters.length, label: "speaking roles" },
    { value: totalLines, label: "dialogue lines" },
    { value: voices.length, label: "voices in pool" },
    { value: castCount, label: "roles cast" },
  ];

  const fade = (delay: number) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, y: 16 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.6, delay, ease: [0.16, 1, 0.3, 1] as const },
        };

  return (
    <section
      className="relative overflow-hidden border-b border-[var(--hairline)]"
      aria-label="Scene"
    >
      {/* Atmospheric backdrop */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_75%_60%_at_15%_-10%,rgba(251,191,36,0.12),transparent_60%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_55%_at_100%_120%,rgba(56,189,248,0.1),transparent_60%)]" />
        <div className="absolute inset-0 bg-[linear-gradient(180deg,transparent,rgba(9,9,11,0.4))]" />
      </div>

      <div className="relative mx-auto w-full max-w-7xl px-5 pb-10 pt-12 sm:px-8 md:pb-14 md:pt-16">
        <div className="flex flex-col gap-8 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <motion.p
              {...fade(0)}
              className="font-mono text-[11px] uppercase tracking-[0.22em] text-amber-400"
            >
              Casting Studio
            </motion.p>
            <motion.h1
              {...fade(0.08)}
              className="mt-3 text-4xl font-semibold tracking-tight text-zinc-50 md:text-6xl"
            >
              {name}
            </motion.h1>
            {tag && (
              <motion.p
                {...fade(0.16)}
                className="mt-2 font-mono text-xs uppercase tracking-[0.18em] text-zinc-500"
              >
                {tag}
              </motion.p>
            )}
            <motion.p
              {...fade(0.24)}
              className="mt-5 max-w-xl text-sm leading-relaxed text-zinc-400 md:text-base"
            >
              Cast a voice to every character, judge how each one fits the role,
              then rank your takes to find the best-sounding cast.
            </motion.p>
          </div>

          {/* Live overall casting score */}
          <motion.div
            {...fade(0.3)}
            className="flex shrink-0 items-center gap-4 self-start rounded-2xl border border-[var(--hairline)] bg-[var(--surface-2)] p-4 shadow-[var(--shadow-card)] md:self-auto"
          >
            {fit ? (
              <ScoreDial score={fit.overall_score} size={92} label="overall fit" />
            ) : (
              <div
                className="flex flex-col items-center justify-center rounded-full"
                style={{ width: 92, height: 92 }}
              >
                <div className="flex h-[92px] w-[92px] items-center justify-center rounded-full border-[8px] border-white/[0.06]">
                  <span className="font-mono text-2xl font-semibold text-zinc-600">
                    —
                  </span>
                </div>
              </div>
            )}
            <div className="max-w-[9rem]">
              <p className="text-xs font-semibold text-zinc-200">
                {fit ? "Overall casting" : "Not judged yet"}
              </p>
              <p className="mt-1 text-[11px] leading-snug text-zinc-500">
                {fit
                  ? fitStale
                    ? "Casting changed since the last judgment — re-judge to refresh."
                    : "Live score across every cast character."
                  : "Assign voices and run the judge to score the cast."}
              </p>
            </div>
          </motion.div>
        </div>

        {/* Meta stats */}
        <motion.dl
          {...fade(0.38)}
          className="mt-10 grid max-w-2xl grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4"
        >
          {stats.map((s) => (
            <div key={s.label}>
              <dt className="sr-only">{s.label}</dt>
              <dd className="font-mono text-2xl font-semibold tracking-tight text-zinc-100 md:text-3xl">
                {s.value}
              </dd>
              <p className="mt-1 text-[11px] leading-tight text-zinc-500">
                {s.label}
              </p>
            </div>
          ))}
        </motion.dl>
      </div>
    </section>
  );
}
