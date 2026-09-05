"use client";

import { useState, type CSSProperties } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { api } from "@/lib/api";
import { useCastingStore } from "@/lib/castingStore";
import { ScoreBar, pctOf } from "@/components/casting/ScoreMeter";
import { EngineChip, FailurePanel } from "@/components/casting/JudgeStatus";
import { FOCUS_RING } from "@/components/casting/theme";

function medal(rank: number): { style: CSSProperties; label: string | null } {
  const token =
    rank === 1
      ? "--medal-gold"
      : rank === 2
        ? "--medal-silver"
        : rank === 3
          ? "--medal-bronze"
          : null;
  if (!token) {
    return {
      style: {
        backgroundColor: "rgba(255,255,255,0.06)",
        color: "rgb(161 161 170)",
        borderColor: "var(--hairline)",
      },
      label: null,
    };
  }
  return {
    style: {
      backgroundColor: `rgb(var(${token}) / 0.18)`,
      color: `rgb(var(${token}))`,
      borderColor: `rgb(var(${token}) / 0.55)`,
    },
    label: rank === 1 ? "gold" : rank === 2 ? "silver" : "bronze",
  };
}

export default function CandidateLeaderboard() {
  const reduce = useReducedMotion();
  const projectId = useCastingStore((s) => s.projectId);
  const voices = useCastingStore((s) => s.voices);
  const casting = useCastingStore((s) => s.casting);
  const candidates = useCastingStore((s) => s.candidates);
  const ranking = useCastingStore((s) => s.ranking);
  const rankingStale = useCastingStore((s) => s.rankingStale);
  const rankingEngine = useCastingStore((s) => s.rankingEngine);
  const addCandidate = useCastingStore((s) => s.addCandidate);
  const removeCandidate = useCastingStore((s) => s.removeCandidate);
  const loadCandidate = useCastingStore((s) => s.loadCandidate);
  const setRanking = useCastingStore((s) => s.setRanking);

  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // A snapshot of an empty casting would be rejected by the judge, so the
  // capture itself is gated on having cast something.
  const canSnapshot = Object.keys(casting).length > 0;

  const add = () => {
    if (!canSnapshot) return;
    addCandidate(label || `Take ${candidates.length + 1}`);
    setLabel("");
  };

  const rank = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.rankVoices(projectId, {
        candidates: candidates.map((c) => ({ label: c.label, casting: c.casting })),
        available_voices: voices.length ? voices : null,
      });
      // Stamped with the engine that ranked, exactly like the fit panel.
      setRanking(result, api.judgeEngine);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cast-panel flex flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--hairline)] px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden />
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
            Candidates &amp; leaderboard
          </h2>
          {rankingStale && ranking && (
            <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 font-mono text-[10px] text-amber-300">
              out of date
            </span>
          )}
          {ranking && rankingEngine && <EngineChip engine={rankingEngine} />}
        </div>
        <motion.button
          whileTap={reduce || candidates.length < 2 ? undefined : { scale: 0.97 }}
          onClick={rank}
          disabled={busy || candidates.length < 2}
          className={`rounded-lg border border-sky-500/40 bg-sky-500/10 px-4 py-1.5 text-xs font-semibold text-sky-200 transition-colors hover:border-sky-400/70 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`}
          title={
            candidates.length < 2
              ? "Add at least 2 candidates to rank"
              : "Rank candidates"
          }
        >
          {busy ? "Ranking…" : "Rank takes"}
        </motion.button>
      </div>

      <div className="grid gap-5 p-4 lg:grid-cols-[320px_1fr]">
        {/* Candidates column */}
        <div className="space-y-3">
          <p className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">
            Snapshot a take
          </p>
          <div className="flex gap-2">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
              placeholder={`Take ${candidates.length + 1}`}
              className={`min-w-0 flex-1 rounded-lg border border-[var(--hairline-strong)] bg-[var(--surface-3)] px-3 py-1.5 text-[11px] text-zinc-100 placeholder-zinc-600 outline-none transition-colors focus:border-sky-500 ${FOCUS_RING}`}
              aria-label="Candidate label"
            />
            <button
              onClick={add}
              disabled={!canSnapshot}
              title={
                canSnapshot
                  ? "Snapshot the current casting"
                  : "Cast at least one character before snapshotting a take"
              }
              className={`shrink-0 rounded-lg border border-[var(--hairline-strong)] px-3 py-1.5 text-[11px] font-medium text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`}
            >
              + Add casting
            </button>
          </div>

          {candidates.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {candidates.map((c) => (
                <span
                  key={c.label}
                  className="flex items-center gap-1.5 rounded-full border border-[var(--hairline-strong)] bg-[var(--surface-3)] px-2.5 py-1 font-mono text-[10px] text-zinc-300"
                >
                  {c.label}
                  <button
                    onClick={() => removeCandidate(c.label)}
                    className="text-zinc-500 transition-colors hover:text-rose-400"
                    title={`Remove ${c.label}`}
                    aria-label={`Remove ${c.label}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <p className="text-[11px] leading-relaxed text-zinc-500">
            Snapshot the current casting under a label, tweak the voices, and
            snapshot again. With two or more takes you can rank them to pick the
            best-sounding cast.
          </p>
        </div>

        {/* Leaderboard column */}
        <div>
          <div className="mb-2.5 flex items-center gap-2">
            <p className="font-mono text-[9px] uppercase tracking-wider text-zinc-500">
              Leaderboard
            </p>
            {rankingStale && ranking && (
              <span className="font-mono text-[9px] text-amber-400">
                re-rank to refresh
              </span>
            )}
          </div>

          {error !== null && !busy && (
            <div className="mb-2">
              <FailurePanel
                error={error}
                onRetry={candidates.length >= 2 ? rank : undefined}
                retryLabel="Rank again"
                compact
              />
            </div>
          )}

          {busy && (
            <div className="space-y-2">
              {candidates.slice(0, 4).map((c) => (
                <div key={c.label} className="cast-card flex items-center gap-3 p-3">
                  <div className="cast-shimmer h-6 w-6 shrink-0 rounded-full" />
                  <span className="truncate font-mono text-[12px] text-zinc-500">
                    {c.label}
                  </span>
                  <div className="cast-shimmer ml-auto h-3 w-8 rounded" />
                </div>
              ))}
            </div>
          )}

          {!busy && error === null && (!ranking || ranking.entries.length === 0) && (
            <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--hairline-strong)] px-6 py-12 text-center">
              <p className="text-sm font-medium text-zinc-300">No ranking yet</p>
              <p className="max-w-sm text-xs leading-relaxed text-zinc-500">
                Add at least two takes, then rank them. The best-scoring cast rises
                to the top with a winner&apos;s laurel.
              </p>
            </div>
          )}

          {!busy && ranking && ranking.entries.length > 0 && (
            <AnimatePresence initial={false}>
              {ranking.entries.map((entry, i) => {
                const isWinner = entry.label === ranking.winner;
                const m = medal(entry.rank);
                return (
                  <motion.div
                    key={entry.label}
                    layout
                    initial={reduce ? false : { opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={reduce ? undefined : { opacity: 0, y: -8 }}
                    transition={{
                      duration: 0.4,
                      delay: reduce ? 0 : i * 0.06,
                      ease: [0.16, 1, 0.3, 1],
                    }}
                    className={`relative mb-2 overflow-hidden rounded-xl p-3 ${
                      isWinner
                        ? "winner-sheen border border-amber-400/50 bg-amber-500/[0.07] shadow-[0_0_44px_-14px_rgba(251,191,36,0.55)]"
                        : "cast-card cast-hover"
                    }`}
                  >
                    <div className="relative flex items-center gap-3">
                      <span
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border font-mono text-[11px] font-semibold"
                        style={m.style}
                      >
                        {entry.rank}
                      </span>
                      <span className="flex min-w-0 flex-1 items-center gap-2">
                        <span className="truncate font-mono text-[12px] text-zinc-100">
                          {entry.label}
                        </span>
                        {isWinner && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-400/10 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-amber-300">
                            <svg
                              viewBox="0 0 24 24"
                              className="h-2.5 w-2.5"
                              fill="currentColor"
                              aria-hidden
                            >
                              <path d="M12 2l2.9 6.1 6.6.7-4.9 4.5 1.4 6.6L12 17.8 6 20.4l1.4-6.6L2.5 8.8l6.6-.7L12 2z" />
                            </svg>
                            winner
                          </span>
                        )}
                      </span>
                      <span
                        className="font-mono text-sm font-semibold tabular-nums"
                        style={{
                          color: isWinner ? "var(--band-strong)" : "rgb(228 228 231)",
                        }}
                      >
                        {pctOf(entry.overall_score)}
                      </span>
                      <button
                        onClick={() => loadCandidate(entry.label)}
                        className={`shrink-0 rounded-md border border-[var(--hairline-strong)] px-2 py-1 font-mono text-[10px] text-zinc-400 transition-colors hover:border-sky-500 hover:text-sky-300 ${FOCUS_RING}`}
                        title="Load this casting into the editor"
                      >
                        load
                      </button>
                    </div>
                    <div className="relative mt-2.5 pl-10 pr-1">
                      <ScoreBar score={entry.overall_score} delay={i * 0.06} />
                    </div>
                  </motion.div>
                );
              })}
            </AnimatePresence>
          )}
        </div>
      </div>
    </div>
  );
}
