"use client";

import { useCallback, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { api } from "@/lib/api";
import { useCastingStore } from "@/lib/castingStore";
import type { AnimaticFinding, AnimaticJudgment } from "@/lib/types";
import { ScoreBar, ScoreDial, pctOf } from "@/components/casting/ScoreMeter";
import { EngineChip, FailurePanel } from "@/components/casting/JudgeStatus";
import { FOCUS_RING, SEVERITY } from "@/components/casting/theme";
import type { JudgeEngine } from "@/lib/types";

function Axis({ label, score }: { label: string; score: number }) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
          {label}
        </span>
        <span className="font-mono text-[10px] text-zinc-300">{pctOf(score)}</span>
      </div>
      <div className="mt-1.5">
        <ScoreBar score={score} />
      </div>
    </div>
  );
}

function FindingRow({ finding }: { finding: AnimaticFinding }) {
  return (
    <div className="flex items-start gap-2">
      <span
        className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${SEVERITY[finding.severity].dot}`}
      />
      <p className="text-[11px] leading-snug text-zinc-400">
        <span className="font-mono text-zinc-500">{finding.code}</span>
        {finding.scene_ordinal !== null && (
          <span className="font-mono text-zinc-600"> · S{finding.scene_ordinal}</span>
        )}
        {finding.shot_ordinal !== null && (
          <span className="font-mono text-zinc-600">/shot {finding.shot_ordinal}</span>
        )}{" "}
        — {finding.message}
      </p>
    </div>
  );
}

export default function AnimaticJudgePanel() {
  const reduce = useReducedMotion();
  const projectId = useCastingStore((s) => s.projectId);
  const [open, setOpen] = useState(false);
  const [judgment, setJudgment] = useState<AnimaticJudgment | null>(null);
  const [engine, setEngine] = useState<JudgeEngine | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const run = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.judgeAnimatic(projectId);
      setJudgment(result);
      setEngine(api.judgeEngine);
    } catch (err) {
      // The common failure is a 404 — "No shot lists to judge; author a shot
      // list first" — which reads as an empty state, not a fault.
      setJudgment(null);
      setEngine(null);
      setError(err);
    } finally {
      setBusy(false);
    }
  }, [projectId]);

  const openPanel = useCallback(() => {
    setOpen(true);
    if (judgment || busy) return;
    void run();
  }, [judgment, busy, run]);

  return (
    <>
      <motion.button
        whileTap={reduce ? undefined : { scale: 0.97 }}
        onClick={openPanel}
        className={`rounded-lg border border-[var(--hairline-strong)] bg-white/[0.02] px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100 ${FOCUS_RING}`}
      >
        Previz judge
      </motion.button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/75 p-4 backdrop-blur-md"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setOpen(false)}
          >
            <motion.div
              className="cast-panel cast-scroll max-h-[85vh] w-full max-w-lg overflow-auto"
              initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: 14 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: 14 }}
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[var(--hairline)] bg-[var(--surface-1)]/80 px-4 py-3 backdrop-blur">
                <div className="flex items-center gap-2.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-sky-400" aria-hidden />
                  <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
                    Animatic quality judge
                  </h2>
                  {judgment && engine && <EngineChip engine={engine} />}
                </div>
                <button
                  onClick={() => setOpen(false)}
                  className={`rounded-md px-1 text-lg leading-none text-zinc-500 transition-colors hover:text-zinc-200 ${FOCUS_RING}`}
                  aria-label="Close"
                >
                  ×
                </button>
              </div>

              <div className="p-4">
                {!busy && error !== null && (
                  <FailurePanel
                    error={error}
                    onRetry={run}
                    retryLabel="Judge again"
                  />
                )}
                {busy && (
                  <div className="space-y-4">
                    <div className="flex items-center gap-4">
                      <div className="cast-shimmer h-[78px] w-[78px] shrink-0 rounded-full" />
                      <div className="flex-1 space-y-2.5">
                        <div className="cast-shimmer h-2.5 w-full rounded" />
                        <div className="cast-shimmer h-2.5 w-2/3 rounded" />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      {[0, 1, 2, 3].map((i) => (
                        <div key={i} className="cast-shimmer h-6 rounded" />
                      ))}
                    </div>
                  </div>
                )}
                {judgment && (
                  <div className="space-y-4">
                    <div className="flex items-center gap-4">
                      <ScoreDial
                        score={judgment.overall_score}
                        size={78}
                        label="overall"
                      />
                      <p className="text-[11px] leading-snug text-zinc-400">
                        {judgment.rationale}
                      </p>
                    </div>

                    <div className="grid grid-cols-2 gap-x-4 gap-y-3.5 rounded-xl border border-[var(--hairline)] bg-[var(--surface-2)] p-4">
                      <Axis label="coverage" score={judgment.coverage_score} />
                      <Axis label="continuity" score={judgment.continuity_score} />
                      <Axis label="variety" score={judgment.variety_score} />
                      <Axis label="pacing" score={judgment.pacing_score} />
                    </div>

                    {judgment.findings.length > 0 && (
                      <div className="space-y-1.5 border-t border-[var(--hairline)] pt-3">
                        {judgment.findings.map((f, i) => (
                          <FindingRow key={`overall-${i}`} finding={f} />
                        ))}
                      </div>
                    )}

                    <div className="space-y-2 border-t border-[var(--hairline)] pt-3">
                      {judgment.scenes.map((scene) => (
                        <div key={scene.scene_ordinal} className="cast-card p-3.5">
                          <div className="flex items-center justify-between">
                            <span className="font-mono text-xs font-semibold text-zinc-200">
                              Scene {scene.scene_ordinal}
                            </span>
                            <span className="font-mono text-[10px] text-zinc-500">
                              {scene.shot_count} shots · {pctOf(scene.score)}
                            </span>
                          </div>
                          {scene.findings.length > 0 && (
                            <div className="mt-2.5 space-y-1.5">
                              {scene.findings.map((f, i) => (
                                <FindingRow
                                  key={`s${scene.scene_ordinal}-${i}`}
                                  finding={f}
                                />
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
