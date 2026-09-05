"use client";

// Two small pieces every judge panel shares:
//
//   <EngineChip>   who produced the score on screen. The backend judge and the
//                  browser port in lib/judge.ts can drift, so a score is never
//                  shown without saying which one answered. They are never
//                  blended — one engine handles a request end to end.
//   <FailurePanel> a failed request, in the app's own voice, keeping the
//                  server's `detail` verbatim and offering a retry.

import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { JudgeEngine } from "@/lib/types";
import { JUDGE_ENGINE_META } from "@/lib/types";
import { login, register } from "@/lib/apiClient";
import { describeFailure, FOCUS_RING } from "@/components/casting/theme";

export function EngineChip({
  engine,
  className = "",
}: {
  engine: JudgeEngine;
  className?: string;
}) {
  const meta = JUDGE_ENGINE_META[engine];
  const live = engine === "backend";
  return (
    <span
      title={meta.detail}
      className={`inline-flex cursor-help items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider ${
        live
          ? "border-emerald-500/30 bg-emerald-500/[0.07] text-emerald-300/90"
          : "border-[var(--hairline-strong)] bg-white/[0.03] text-zinc-400"
      } ${className}`}
    >
      <span
        aria-hidden
        className={`h-1 w-1 rounded-full ${live ? "bg-emerald-400" : "bg-zinc-500"}`}
      />
      {meta.label}
    </span>
  );
}

/**
 * Inline sign-in, shown only for a 401. Every project endpoint is bearer-gated
 * (`get_current_user` in backend/app/api/deps.py) and the app has no login
 * route, so without this an auth failure is a dead end: the page would report
 * "not signed in" with no way to sign in. Tokens go through `lib/apiClient`,
 * which is the only thing that touches storage.
 */
function AuthPrompt({ onSignedIn }: { onSignedIn?: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) return;
    setBusy(true);
    setProblem(null);
    try {
      await (creating ? register(email, password) : login(email, password));
      onSignedIn?.();
    } catch (err) {
      setProblem(describeFailure(err).detail);
    } finally {
      setBusy(false);
    }
  };

  const field =
    "min-w-0 flex-1 rounded-lg border border-[var(--hairline-strong)] bg-[var(--surface-3)] px-3 py-1.5 text-[11px] text-zinc-100 placeholder-zinc-600 outline-none transition-colors focus:border-sky-500";

  return (
    <form onSubmit={submit} className="mt-3 border-t border-[var(--hairline)] pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="email"
          aria-label="Email"
          className={`${field} ${FOCUS_RING}`}
        />
        <input
          type="password"
          autoComplete={creating ? "new-password" : "current-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="password"
          aria-label="Password"
          className={`${field} ${FOCUS_RING}`}
        />
        <button
          type="submit"
          disabled={busy || !email || !password}
          className={`shrink-0 rounded-lg bg-amber-400 px-3 py-1.5 text-[11px] font-semibold text-zinc-950 transition-colors hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`}
        >
          {busy ? "…" : creating ? "Create & sign in" : "Sign in"}
        </button>
      </div>
      <div className="mt-1.5 flex items-center gap-3">
        <button
          type="button"
          onClick={() => setCreating((c) => !c)}
          className={`rounded-sm font-mono text-[10px] text-zinc-500 underline underline-offset-2 transition-colors hover:text-zinc-300 ${FOCUS_RING}`}
        >
          {creating ? "I already have an account" : "Create an account instead"}
        </button>
        {problem && <span className="text-[10px] text-rose-300">{problem}</span>}
      </div>
    </form>
  );
}

export function FailurePanel({
  error,
  onRetry,
  retryLabel = "Try again",
  compact = false,
}: {
  error: unknown;
  onRetry?: () => void;
  retryLabel?: string;
  compact?: boolean;
}) {
  const reduce = useReducedMotion();
  const f = describeFailure(error);
  // A 404 is usually "you haven't authored this yet", not a fault — it gets the
  // calmer amber treatment rather than the rose of a genuine failure.
  const soft = f.kind === "missing";

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      role="alert"
      className={`rounded-xl border px-4 ${compact ? "py-3" : "py-5"} ${
        soft
          ? "border-amber-500/30 bg-amber-500/[0.06]"
          : "border-rose-500/30 bg-rose-500/[0.06]"
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border ${
            soft
              ? "border-amber-500/40 text-amber-300"
              : "border-rose-500/40 text-rose-300"
          }`}
        >
          <svg
            viewBox="0 0 24 24"
            className="h-3.5 w-3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <path d="M12 8v5" />
            <path d="M12 16.5v.01" />
          </svg>
        </span>
        <div className="min-w-0 flex-1">
          <p
            className={`text-sm font-medium ${soft ? "text-amber-100" : "text-rose-100"}`}
          >
            {f.headline}
          </p>
          <p className="mt-1 break-words text-[11px] leading-relaxed text-zinc-400">
            {f.detail}
          </p>
          {f.hint && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-500">{f.hint}</p>
          )}
        </div>
        {onRetry && (
          <button
            onClick={onRetry}
            className={`shrink-0 rounded-md border px-2.5 py-1 font-mono text-[10px] transition-colors ${
              soft
                ? "border-amber-500/40 text-amber-200 hover:border-amber-400 hover:text-amber-100"
                : "border-rose-500/40 text-rose-200 hover:border-rose-400 hover:text-rose-100"
            } ${FOCUS_RING}`}
          >
            {retryLabel}
          </button>
        )}
      </div>

      {f.kind === "auth" && <AuthPrompt onSignedIn={onRetry} />}
    </motion.div>
  );
}
