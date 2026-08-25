"use client";

import { useEffect, useId } from "react";
import {
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from "motion/react";

// Backend scores are floats in [0, 1]; we surface them as 0-100 with a band
// coloring that matches the judge's own "strong / adequate / poor" thresholds
// (see backend/app/judge/voices.py :: _rationale_for). The bands render as a
// tasteful emerald -> amber -> rose gradient scale (tokens in globals.css).
export type Tone = "strong" | "adequate" | "poor";

export function scoreTone(score: number): Tone {
  return score >= 0.75 ? "strong" : score >= 0.55 ? "adequate" : "poor";
}

export const pctOf = (score: number) => Math.round(score * 100);

const EASE = [0.16, 1, 0.3, 1] as const;

const BAND: Record<Tone, { a: string; b: string; text: string; glow: string }> = {
  strong: {
    a: "var(--band-strong-a)",
    b: "var(--band-strong-b)",
    text: "var(--band-strong)",
    glow: "rgb(16 185 129 / 0.55)",
  },
  adequate: {
    a: "var(--band-adequate-a)",
    b: "var(--band-adequate-b)",
    text: "var(--band-adequate)",
    glow: "rgb(245 158 11 / 0.5)",
  },
  poor: {
    a: "var(--band-poor-a)",
    b: "var(--band-poor-b)",
    text: "var(--band-poor)",
    glow: "rgb(244 63 94 / 0.5)",
  },
};

/** A thin horizontal score bar, 0-100, filled with the band gradient. */
export function ScoreBar({ score, delay = 0 }: { score: number; delay?: number }) {
  const reduce = useReducedMotion();
  const band = BAND[scoreTone(score)];
  return (
    <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
      <motion.div
        className="h-full rounded-full"
        style={{ background: `linear-gradient(90deg, ${band.a}, ${band.b})` }}
        initial={reduce ? false : { width: 0 }}
        animate={{ width: `${pctOf(score)}%` }}
        transition={{ duration: 0.7, ease: EASE, delay: reduce ? 0 : delay }}
      />
    </div>
  );
}

/**
 * A circular score dial for prominent, single scores (overall / winner /
 * per-character fit). The arc sweeps with an eased spring on mount and
 * re-animates whenever the score changes (e.g. after Apply re-judges); the
 * value counts up, and strong scores earn a subtle glow.
 */
export function ScoreDial({
  score,
  size = 72,
  label,
  thickness,
}: {
  score: number;
  size?: number;
  label?: string;
  thickness?: number;
}) {
  const reduce = useReducedMotion();
  const gid = useId().replace(/:/g, "");
  const tone = scoreTone(score);
  const band = BAND[tone];
  const stroke = thickness ?? Math.max(5, Math.round(size * 0.09));
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, score));
  const offset = c * (1 - clamped);

  // Count-up: animate a motion value and render it as text (no React re-render).
  const count = useMotionValue(reduce ? pctOf(score) : 0);
  const shown = useTransform(count, (v) => String(Math.round(v)));
  useEffect(() => {
    if (reduce) {
      count.set(pctOf(score));
      return;
    }
    const controls = animate(count, pctOf(score), { duration: 0.9, ease: EASE });
    return () => controls.stop();
  }, [score, reduce, count]);

  const glow = tone === "strong" ? Math.min(1, Math.max(0, (clamped - 0.75) / 0.25)) : 0;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90" aria-hidden="true">
        <defs>
          <linearGradient id={`g-${gid}`} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={band.a} />
            <stop offset="100%" stopColor={band.b} />
          </linearGradient>
        </defs>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="rgba(255,255,255,0.07)"
          strokeWidth={stroke}
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`url(#g-${gid})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          initial={reduce ? false : { strokeDashoffset: c }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 0.95, ease: EASE }}
          style={
            glow > 0
              ? { filter: `drop-shadow(0 0 ${3 + glow * 7}px ${band.glow})` }
              : undefined
          }
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span
          className="font-mono font-semibold tabular-nums leading-none"
          style={{ color: band.text, fontSize: Math.max(14, size * 0.26) }}
        >
          <motion.span>{shown}</motion.span>
        </span>
        {label && (
          <span
            className="mt-1 font-mono uppercase tracking-wider text-zinc-500"
            style={{ fontSize: Math.max(8, size * 0.11) }}
          >
            {label}
          </span>
        )}
      </div>
    </div>
  );
}
