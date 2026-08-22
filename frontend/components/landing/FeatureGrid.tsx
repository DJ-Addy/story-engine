"use client";

import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

type Feature = {
  title: string;
  body: string;
  glyph: ReactNode;
};

const glyphProps = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
} as const;

const FEATURES: Feature[] = [
  {
    title: "Dialogue attribution",
    body: "Cue-accurate and confidence-scored. Every line mapped to a speaker, with the receipts to prove it.",
    glyph: (
      <svg {...glyphProps}>
        <path d="M4 5h16M4 9h10M4 13h13M4 17h8" />
        <circle cx="19" cy="16" r="3" />
      </svg>
    ),
  },
  {
    title: "Continuity validator",
    body: "Warns, never blocks. 180° axis, eyelines, lens jumps — flag it, or mark it deliberate.",
    glyph: (
      <svg {...glyphProps}>
        <path d="M12 3l8 4v5c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V7l8-4z" />
        <path d="M9 12l2 2 4-4" />
      </svg>
    ),
  },
  {
    title: "Sound design",
    body: "Ambience ducks under speech via sidechain compression. Dialogue sits forward at −18 LUFS.",
    glyph: (
      <svg {...glyphProps}>
        <path d="M4 14v-4M8 17V7M12 20V4M16 16V8M20 13v-2" />
      </svg>
    ),
  },
  {
    title: "Character bible",
    body: "Wardrobe and prop variants locked by scene range. The coat stays on until scene 40 says otherwise.",
    glyph: (
      <svg {...glyphProps}>
        <path d="M5 4a2 2 0 012-2h12v18H7a2 2 0 00-2 2V4z" />
        <path d="M5 18a2 2 0 012-2h12" />
        <circle cx="12" cy="8" r="2" />
      </svg>
    ),
  },
  {
    title: "Shot grammar profiles",
    body: "Classical, handheld, symmetrical, anime. Coverage generated in the grammar you pick.",
    glyph: (
      <svg {...glyphProps}>
        <rect x="3" y="6" width="18" height="12" rx="1.5" />
        <path d="M3 10h18M9 6v12M15 6v12" />
      </svg>
    ),
  },
  {
    title: "Cost governor",
    body: "Hard caps before a single render job enqueues. No surprise invoices.",
    glyph: (
      <svg {...glyphProps}>
        <path d="M20 13a8 8 0 10-16 0" />
        <path d="M12 13l4-3" />
        <path d="M4 17h16" />
      </svg>
    ),
  },
];

export default function FeatureGrid() {
  const reduce = useReducedMotion();

  return (
    <section className="border-t border-zinc-800" aria-label="Capabilities">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-[11px] uppercase tracking-widest text-amber-400">
          Capabilities
        </p>
        <h2 className="mt-3 max-w-xl text-3xl font-semibold tracking-tight text-zinc-50 md:text-4xl">
          Built for people who ship stories
        </h2>
        <ul className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f, i) => (
            <motion.li
              key={f.title}
              initial={reduce ? false : { opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-60px" }}
              transition={{ duration: 0.5, delay: reduce ? 0 : (i % 3) * 0.08, ease: "easeOut" }}
              className="group rounded-lg border border-zinc-800 bg-zinc-900/40 p-6 transition-all duration-300 hover:-translate-y-1 hover:border-amber-400/40 hover:shadow-[0_0_28px_-10px_rgba(251,191,36,0.35)] motion-reduce:hover:translate-y-0"
            >
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-zinc-800 bg-zinc-950 text-amber-400">
                {f.glyph}
              </span>
              <h3 className="mt-4 text-sm font-semibold text-zinc-100">
                {f.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                {f.body}
              </p>
            </motion.li>
          ))}
        </ul>
      </div>
    </section>
  );
}
