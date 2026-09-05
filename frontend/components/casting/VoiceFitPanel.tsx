"use client";

import { useCallback, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { api } from "@/lib/api";
import { useCastingStore } from "@/lib/castingStore";
import type { CharacterVoiceFit, VoiceFinding } from "@/lib/types";
import { ScoreDial, scoreTone, pctOf } from "@/components/casting/ScoreMeter";
import { EngineChip, FailurePanel } from "@/components/casting/JudgeStatus";
import { emotionStyle, FOCUS_RING, SEVERITY } from "@/components/casting/theme";

const TONE_LABEL = {
  strong: "strong fit",
  adequate: "adequate",
  poor: "poor fit",
} as const;

function FindingRow({ finding }: { finding: VoiceFinding }) {
  const style = SEVERITY[finding.severity];
  return (
    <div className="flex items-start gap-2">
      <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${style.dot}`} />
      <p className="text-[11px] leading-snug text-zinc-400">
        <span className={`font-mono ${style.text}`}>{finding.code}</span> —{" "}
        {finding.message}
      </p>
    </div>
  );
}

function FitCard({
  fit,
  onApply,
}: {
  fit: CharacterVoiceFit;
  onApply(character: string, voiceId: string): void;
}) {
  const tone = scoreTone(fit.score);
  return (
    <div className="cast-card cast-hover p-4">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <h3 className="font-mono text-sm font-semibold tracking-wide text-zinc-100">
              {fit.character}
            </h3>
            <span className="font-mono text-[10px] text-zinc-500">
              as{" "}
              <span className="text-sky-300">{fit.voice_name}</span>
            </span>
          </div>
          {fit.dominant_emotions.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {fit.dominant_emotions.map((e) => (
                <span
                  key={e}
                  style={emotionStyle(e)}
                  className="emotion-chip rounded-full px-2 py-0.5 font-mono text-[10px] leading-none"
                >
                  {e}
                </span>
              ))}
            </div>
          )}
          <p className="mt-2.5 text-[11px] leading-snug text-zinc-500">
            {fit.rationale}
          </p>
        </div>
        <ScoreDial score={fit.score} size={62} label={TONE_LABEL[tone]} />
      </div>

      {fit.findings.length > 0 && (
        <div className="mt-3.5 space-y-1.5 border-t border-[var(--hairline)] pt-3">
          {fit.findings.map((f, i) => (
            <FindingRow key={`${f.code}-${i}`} finding={f} />
          ))}
        </div>
      )}

      {fit.suggestions.length > 0 && (
        <div className="mt-3.5 border-t border-[var(--hairline)] pt-3">
          <p className="mb-2 font-mono text-[9px] uppercase tracking-wider text-zinc-500">
            Suggested alternatives
          </p>
          <div className="flex flex-wrap gap-1.5">
            {fit.suggestions.map((s) => (
              <motion.button
                key={s.voice_id}
                whileTap={{ scale: 0.96 }}
                onClick={() => onApply(fit.character, s.voice_id)}
                className={`group flex items-center gap-1.5 rounded-full border border-emerald-500/25 bg-emerald-500/[0.08] px-2.5 py-1 text-[11px] text-emerald-200 transition-colors hover:border-emerald-400/60 hover:bg-emerald-500/15 ${FOCUS_RING}`}
                title={`Reassign ${fit.character} to ${s.voice_name}`}
              >
                <span>{s.voice_name}</span>
                <span className="font-mono text-[10px] text-emerald-400">
                  {pctOf(s.score)}
                </span>
                <span className="text-emerald-500/70 transition-opacity group-hover:text-emerald-300">
                  apply
                </span>
              </motion.button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function FitSkeleton() {
  return (
    <div className="cast-card p-4">
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="cast-shimmer h-3.5 w-28 rounded" />
          <div className="cast-shimmer h-2.5 w-40 rounded" />
          <div className="cast-shimmer h-2.5 w-full rounded" />
        </div>
        <div className="cast-shimmer h-[62px] w-[62px] shrink-0 rounded-full" />
      </div>
    </div>
  );
}

export default function VoiceFitPanel() {
  const reduce = useReducedMotion();
  const projectId = useCastingStore((s) => s.projectId);
  const characters = useCastingStore((s) => s.characters);
  const casting = useCastingStore((s) => s.casting);
  const voices = useCastingStore((s) => s.voices);
  const fit = useCastingStore((s) => s.fit);
  const fitStale = useCastingStore((s) => s.fitStale);
  const fitEngine = useCastingStore((s) => s.fitEngine);
  const setFit = useCastingStore((s) => s.setFit);
  const assignVoice = useCastingStore((s) => s.assignVoice);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const runJudge = useCallback(async () => {
    // The backend declares `casting` with min_length=1; sending an empty map is
    // a 422, so refuse locally with wording the user can act on.
    const current = useCastingStore.getState().casting;
    if (Object.keys(current).length === 0) {
      setError(new Error("Assign a voice to at least one character first."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.judgeVoices(projectId, {
        casting: current,
        // Only send a pool when there is one; `null` tells the server to fall
        // back to the casting's own voices rather than rejecting an empty list.
        available_voices: voices.length ? voices : null,
      });
      // Stamp the result with whichever judge answered, so the score on screen
      // is attributable and the two engines are never silently blended.
      setFit(result, api.judgeEngine);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }, [projectId, voices, setFit]);

  const applySuggestion = useCallback(
    async (character: string, voiceId: string) => {
      const voice = voices.find((v) => v.id === voiceId);
      if (!voice) return;
      assignVoice(character, voice);
      await runJudge();
    },
    [voices, assignVoice, runJudge],
  );

  const castCount = Object.keys(casting).length;
  const uncastCount = characters.filter((c) => !casting[c.name]).length;

  return (
    <div className="cast-panel flex flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--hairline)] px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden />
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
            Voice-fit judge
          </h2>
          {fit && (
            <span
              className={`rounded-full border px-2 py-0.5 font-mono text-[10px] ${
                fitStale
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                  : "border-[var(--hairline)] bg-white/[0.03] text-zinc-400"
              }`}
            >
              {fitStale ? "casting changed" : `overall ${pctOf(fit.overall_score)}`}
            </span>
          )}
          {fit && fitEngine && <EngineChip engine={fitEngine} />}
        </div>
        <motion.button
          whileTap={reduce ? undefined : { scale: 0.97 }}
          onClick={runJudge}
          disabled={busy || castCount === 0}
          title={
            castCount === 0
              ? "Assign a voice to at least one character first"
              : undefined
          }
          className={`rounded-lg bg-amber-400 px-3.5 py-1.5 text-xs font-semibold text-zinc-950 shadow-[0_0_24px_-10px_rgba(251,191,36,0.7)] transition-colors hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none ${FOCUS_RING}`}
        >
          {busy ? "Judging…" : fit ? "Re-judge casting" : "Judge this casting"}
        </motion.button>
      </div>

      {/* Indeterminate progress line while judging */}
      <div className="relative h-px w-full overflow-hidden bg-transparent">
        <AnimatePresence>
          {busy && (
            <motion.div
              key="bar"
              className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-amber-400 to-transparent"
              initial={{ x: "-120%" }}
              animate={{ x: "420%" }}
              exit={{ opacity: 0 }}
              transition={{
                duration: 1,
                repeat: Infinity,
                ease: "easeInOut",
              }}
            />
          )}
        </AnimatePresence>
      </div>

      <div className="space-y-2.5 p-3">
        {error !== null && (
          <FailurePanel
            error={error}
            onRetry={castCount > 0 ? runJudge : undefined}
            retryLabel="Judge again"
            compact
          />
        )}

        {/* Partial cast: the judge still runs, and reports who was left out. */}
        {!busy && castCount > 0 && uncastCount > 0 && (
          <p className="rounded-lg border border-[var(--hairline)] bg-white/[0.02] px-3 py-2 text-[11px] leading-relaxed text-zinc-500">
            {uncastCount} of {characters.length} role
            {characters.length === 1 ? "" : "s"} {uncastCount === 1 ? "is" : "are"}{" "}
            still uncast. Judging now scores the {castCount} cast role
            {castCount === 1 ? "" : "s"} and lists the rest as uncast.
          </p>
        )}

        {!fit && !busy && (
          <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
            <span
              aria-hidden
              className="flex h-12 w-12 items-center justify-center rounded-full border border-[var(--hairline-strong)] bg-[var(--surface-3)] text-amber-400"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6l1.4 1.4m10 10 1.4 1.4m0-12.8-1.4 1.4m-10 10-1.4 1.4" />
                <circle cx="12" cy="12" r="4" />
              </svg>
            </span>
            <p className="text-sm font-medium text-zinc-300">
              {castCount === 0 ? "Nothing cast yet" : "No judgment yet"}
            </p>
            <p className="max-w-xs text-xs leading-relaxed text-zinc-500">
              {castCount === 0
                ? "Pick a voice for at least one character in the roster — the judge needs a casting to score."
                : "Judge the casting. The judge scores every character's fit and suggests better voices from the pool."}
            </p>
          </div>
        )}

        {!fit && busy && (
          <div className="space-y-2.5">
            <div className="cast-card flex items-center gap-4 p-4">
              <div className="cast-shimmer h-[72px] w-[72px] shrink-0 rounded-full" />
              <div className="flex-1 space-y-2.5">
                <div className="cast-shimmer h-3 w-3/4 rounded" />
                <div className="cast-shimmer h-2.5 w-1/2 rounded" />
              </div>
            </div>
            <FitSkeleton />
            <FitSkeleton />
            <FitSkeleton />
          </div>
        )}

        {fit && (
          <div className="space-y-2.5">
            <div className="cast-card flex items-center gap-4 p-4">
              <ScoreDial score={fit.overall_score} size={78} label="overall" />
              <div className="min-w-0">
                <p className="text-[11px] leading-snug text-zinc-400">
                  {fit.rationale}
                </p>
                {fit.uncast_characters.length > 0 && (
                  <p className="mt-1.5 font-mono text-[10px] text-amber-400">
                    Uncast: {fit.uncast_characters.join(", ")}
                  </p>
                )}
              </div>
            </div>

            {fitStale && (
              <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/[0.07] px-3.5 py-2">
                <span className="text-[11px] text-amber-200">
                  The casting changed since this judgment.
                </span>
                <button
                  onClick={runJudge}
                  disabled={busy}
                  className={`shrink-0 rounded-md font-mono text-[11px] text-amber-200 underline underline-offset-2 hover:text-amber-100 disabled:opacity-50 ${FOCUS_RING}`}
                >
                  re-judge
                </button>
              </div>
            )}

            <AnimatePresence initial={false}>
              {fit.characters.map((c, i) => (
                <motion.div
                  key={c.character}
                  layout
                  initial={reduce ? false : { opacity: 0, y: 12 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    duration: 0.35,
                    delay: reduce ? 0 : i * 0.05,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                >
                  <FitCard fit={c} onApply={applySuggestion} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}
