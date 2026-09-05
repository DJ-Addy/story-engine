"use client";

import { useMemo } from "react";
import { motion, useReducedMotion } from "motion/react";
import { useCastingStore } from "@/lib/castingStore";
import { dominantEmotions, narrationShare } from "@/lib/judge";
import type { CharacterSignal } from "@/lib/judge";
import { ScoreBar } from "@/components/casting/ScoreMeter";
import { emotionStyle, FOCUS_RING } from "@/components/casting/theme";

function VoicePicker({ character }: { character: string }) {
  const voices = useCastingStore((s) => s.voices);
  const assigned = useCastingStore((s) => s.casting[character]);
  const assignVoice = useCastingStore((s) => s.assignVoice);

  return (
    <div className="relative">
      <select
        className={`w-full appearance-none rounded-lg border border-[var(--hairline-strong)] bg-[var(--surface-3)] px-3 py-2 pr-8 font-mono text-[11px] text-zinc-100 outline-none transition-colors hover:border-zinc-500 focus:border-sky-500 disabled:cursor-not-allowed disabled:opacity-50 ${FOCUS_RING}`}
        value={assigned?.id ?? ""}
        disabled={voices.length === 0}
        onChange={(e) => {
          const voice = voices.find((v) => v.id === e.target.value);
          if (voice) assignVoice(character, voice);
        }}
        aria-label={`Voice for ${character}`}
      >
        {voices.length === 0 && <option value="">no voices available</option>}
        {voices.length > 0 && !assigned && <option value="">— pick a voice —</option>}
        {voices.map((v) => (
          <option key={v.id} value={v.id}>
            {v.name} — {v.tags.filter((t) => t !== "american").join(", ")}
          </option>
        ))}
      </select>
      <svg
        aria-hidden
        viewBox="0 0 24 24"
        className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
    </div>
  );
}

function CharacterCard({
  character,
  speaksShare,
  index,
}: {
  character: CharacterSignal;
  speaksShare: number;
  index: number;
}) {
  const reduce = useReducedMotion();
  const assigned = useCastingStore((s) => s.casting[character.name]);
  const emotions = dominantEmotions(character);
  const narr = narrationShare(character);

  return (
    <motion.div
      layout
      initial={reduce ? false : { opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.4,
        delay: reduce ? 0 : index * 0.05,
        ease: [0.16, 1, 0.3, 1],
      }}
      className="cast-card cast-hover p-3.5"
    >
      <div className="flex items-start gap-3">
        {/* Monogram medallion */}
        <span
          aria-hidden
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--hairline-strong)] bg-[var(--surface-3)] font-mono text-sm font-semibold text-zinc-300"
        >
          {character.name.charAt(0)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="truncate font-mono text-xs font-semibold tracking-wide text-zinc-100">
              {character.name}
            </h3>
            <span className="shrink-0 font-mono text-[10px] text-zinc-500">
              {character.dialogue_lines} line
              {character.dialogue_lines === 1 ? "" : "s"}
              {narr > 0 && ` · ${Math.round(narr * 100)}% narr`}
            </span>
          </div>

          <div className="mt-2 flex flex-wrap gap-1">
            {emotions.length > 0 ? (
              emotions.map((e) => (
                <span
                  key={e}
                  style={emotionStyle(e)}
                  className="emotion-chip rounded-full px-2 py-0.5 font-mono text-[10px] leading-none"
                >
                  {e}
                </span>
              ))
            ) : (
              <span className="font-mono text-[10px] text-zinc-600">
                emotionally neutral
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Share of the scene's dialogue this character carries — the "need". */}
      <div className="mt-3.5 flex items-center gap-2">
        <span className="font-mono text-[9px] uppercase tracking-wider text-zinc-600">
          share
        </span>
        <div className="flex-1">
          <ScoreBar score={speaksShare} delay={index * 0.05} />
        </div>
        <span className="w-8 text-right font-mono text-[10px] text-zinc-400">
          {Math.round(speaksShare * 100)}%
        </span>
      </div>

      <div className="mt-3">
        <VoicePicker character={character.name} />
        {assigned && (
          <div className="mt-2 flex flex-wrap gap-1">
            {assigned.tags
              .filter((t) => t !== "american")
              .map((t) => (
                <span
                  key={t}
                  className="voice-pill rounded-full px-2 py-0.5 font-mono text-[9px] leading-none"
                >
                  {t}
                </span>
              ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}

function RosterSkeleton() {
  return (
    <div className="cast-card p-3.5">
      <div className="flex items-start gap-3">
        <div className="cast-shimmer h-9 w-9 shrink-0 rounded-full" />
        <div className="flex-1 space-y-2.5">
          <div className="cast-shimmer h-3 w-24 rounded" />
          <div className="cast-shimmer h-2.5 w-36 rounded" />
        </div>
      </div>
      <div className="cast-shimmer mt-4 h-8 w-full rounded-lg" />
    </div>
  );
}

export default function CharacterRoster({ loading = false }: { loading?: boolean }) {
  const characters = useCastingStore((s) => s.characters);
  const voices = useCastingStore((s) => s.voices);
  const voicesAreStub = useCastingStore((s) => s.voicesAreStub);

  const totalDialogue = useMemo(
    () => characters.reduce((sum, c) => sum + c.dialogue_lines, 0),
    [characters],
  );

  return (
    <div className="cast-panel flex flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--hairline)] px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden />
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
            Roster
          </h2>
        </div>
        <span className="font-mono text-[10px] text-zinc-500">
          {loading ? "loading…" : `${characters.length} roles · assign a voice`}
        </span>
      </div>

      {/* The API publishes no voice-catalog endpoint yet, so even the live path
          picks from fixtures. Say so here, where voices are chosen. */}
      {!loading && voicesAreStub && voices.length > 0 && (
        <p className="border-b border-[var(--hairline)] bg-white/[0.02] px-4 py-2 text-[10px] leading-relaxed text-zinc-500">
          <span className="font-mono uppercase tracking-wider text-amber-400/80">
            stub catalog
          </span>{" "}
          — the API exposes no voice list yet, so these {voices.length} voices are
          local fixtures shaped like the TTS adapter&apos;s. Fit scores are real;
          the voice names are not.
        </p>
      )}

      <div className="space-y-2.5 p-3">
        {loading && [0, 1, 2].map((i) => <RosterSkeleton key={i} />)}

        {!loading && characters.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
            <p className="text-sm font-medium text-zinc-300">No speaking roles</p>
            <p className="max-w-xs text-xs leading-relaxed text-zinc-500">
              This project&apos;s story graph has no attributed dialogue, so there
              is nothing to cast. Ingest a script or attribute some lines first.
            </p>
          </div>
        )}

        {!loading &&
          characters.map((c, i) => (
            <CharacterCard
              key={c.name}
              character={c}
              index={i}
              speaksShare={totalDialogue ? c.dialogue_lines / totalDialogue : 0}
            />
          ))}
      </div>
    </div>
  );
}
