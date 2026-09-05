"use client";

// A light "AI as the editor" affordance. Each canned suggestion applies a mock
// mutation to the timeline data in the store (shift/trim clips, duck ambience,
// insert a reaction shot); the lanes reflow with Motion's layout animation.
// This is a STUB over the mock data — it gestures at the product's vision of AI
// as the primary editing surface, not a real model call.

import { motion, useReducedMotion } from "motion/react";
import { AI_EDITS, useTimelineStore } from "@/lib/timelineStore";
import { FOCUS_RING } from "@/components/casting/theme";

export default function AiAssistStrip() {
  const reduce = useReducedMotion();
  const applied = useTimelineStore((s) => s.appliedEdits);
  const applyAiEdit = useTimelineStore((s) => s.applyAiEdit);
  const hasData = useTimelineStore((s) => s.data !== null);

  return (
    <div className="cast-panel p-3">
      <div className="mb-2.5 flex items-center gap-2">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-hidden />
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
          AI assist
        </h2>
        <span className="rounded-full border border-[var(--hairline)] bg-white/[0.03] px-2 py-0.5 font-mono text-[9px] uppercase tracking-wide text-zinc-500">
          stub · local only
        </span>
        <span className="ml-auto hidden text-[11px] text-zinc-600 sm:inline">
          Editing is meant to be driven by prompts — these reshape the loaded
          timeline in the browser and are never saved.
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {AI_EDITS.map((edit) => {
          const done = applied.includes(edit.id);
          return (
            <motion.button
              key={edit.id}
              whileTap={reduce || done ? undefined : { scale: 0.97 }}
              onClick={() => applyAiEdit(edit.id)}
              disabled={done || !hasData}
              title={edit.description}
              className={`group flex max-w-xs items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors disabled:cursor-not-allowed ${
                done
                  ? "border-emerald-500/30 bg-emerald-500/[0.08] text-emerald-200"
                  : "border-amber-500/25 bg-amber-500/[0.06] text-amber-100 hover:border-amber-400/60 hover:bg-amber-500/12 disabled:opacity-40"
              } ${FOCUS_RING}`}
            >
              <span className="flex min-w-0 flex-col">
                <span className="text-xs font-medium">{edit.label}</span>
                <span className="truncate text-[10px] text-zinc-400 group-hover:text-zinc-300">
                  {edit.description}
                </span>
              </span>
              <span className="ml-1 shrink-0 font-mono text-[10px]">
                {done ? "✓ applied" : "apply"}
              </span>
            </motion.button>
          );
        })}
      </div>
    </div>
  );
}
