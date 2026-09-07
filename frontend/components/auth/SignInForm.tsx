"use client";

// The one inline credentials form.
//
// The app has no /login route — every surface that needs a token has to offer
// one where the failure happened, or a 401 is a dead end. This used to be two
// hand-rolled forms (the analytics page's `SignInPanel`, the casting studio's
// `AuthPrompt` in JudgeStatus.tsx) and the workspace's cold-start panel would
// have made a third. So the form itself lives here once and the panels wrap it.
//
// It is not an auth system: it calls the `login` / `register` already exported
// by lib/apiClient, which is the only thing in the app that touches token
// storage. Nothing here reads or writes a token directly.

import { useState, type FormEvent } from "react";
import { login, register, ApiError } from "@/lib/apiClient";
import { describeFailure } from "@/components/casting/theme";
import { forgetDemoSession } from "@/lib/demoApi";
import { FOCUS_RING } from "@/components/dashboard/theme";
import { ActionButton } from "@/components/dashboard/states";

/**
 * What went wrong, in words that fit a login box.
 *
 * `request()` in apiClient rewrites every 401 to "Not authenticated — sign in
 * again", which is true of a stale token and nonsense on a form where you just
 * typed a password. POST /auth/login answers 401 for exactly one reason
 * (backend/app/api/routers/auth.py: "Invalid credentials"), and /auth/register
 * answers 409 for exactly one, so both are named here. Everything else keeps
 * the server's own wording via `describeFailure`.
 */
function credentialsError(err: unknown, creating: boolean): string {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      return "That email and password were not recognised.";
    }
    if (err.status === 409) {
      return "That email is already registered — sign in instead of creating.";
    }
    if (err.status === 422 && creating) {
      // The server's field errors ("password: too short") are the useful text.
      return describeFailure(err).detail;
    }
  }
  return describeFailure(err).detail;
}

export function SignInForm({
  onSignedIn,
  /** Namespaces the field ids, so two of these can coexist on one page. */
  idPrefix,
  /** Offer "create an account" as well. Off where a deployment's accounts are
   * provisioned elsewhere; on where a visitor genuinely has no account yet. */
  allowRegister = false,
  /** Focus the email field on mount — for a form revealed by a button press,
   * where the keyboard should follow the disclosure. */
  autoFocus = false,
  className = "mt-4 max-w-sm",
}: {
  onSignedIn: () => void;
  idPrefix: string;
  allowRegister?: boolean;
  autoFocus?: boolean;
  className?: string;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !email || !password) return;
    setBusy(true);
    setError(null);
    try {
      await (creating ? register(email, password) : login(email, password));
      // A token for a real account must not keep resolving the "demo"
      // reference to a project some earlier demo session seeded under a
      // different user — that project 404s for everyone but its owner
      // (backend/app/api/deps.py). Re-discovery decides it afresh.
      forgetDemoSession();
      onSignedIn();
    } catch (err) {
      setError(credentialsError(err, creating));
    } finally {
      setBusy(false);
    }
  }

  const field =
    `w-full rounded-md border border-[var(--hairline)] bg-white/[0.03] px-2.5 py-1.5 ` +
    `font-mono text-[12px] text-zinc-200 placeholder:text-zinc-600 ${FOCUS_RING}`;
  const label = "block font-mono text-[10px] uppercase tracking-wider text-zinc-500";

  return (
    <form onSubmit={submit} className={`${className} space-y-2.5`}>
      <div>
        <label htmlFor={`${idPrefix}-email`} className={label}>
          Email
        </label>
        <input
          id={`${idPrefix}-email`}
          type="email"
          autoComplete="username"
          // Only ever true for a form the user just revealed with a button
          // press, where moving focus into it is the continuation of that
          // press rather than a hijack of the page's focus on arrival.
          autoFocus={autoFocus}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={`mt-1 ${field}`}
        />
      </div>
      <div>
        <label htmlFor={`${idPrefix}-password`} className={label}>
          Password
        </label>
        <input
          id={`${idPrefix}-password`}
          type="password"
          autoComplete={creating ? "new-password" : "current-password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={`mt-1 ${field}`}
        />
      </div>
      <div className="flex flex-wrap items-center gap-3 pt-1">
        <ActionButton type="submit" tone="accent" disabled={busy || !email || !password}>
          {busy
            ? creating
              ? "Creating…"
              : "Signing in…"
            : creating
              ? "Create & sign in"
              : "Sign in"}
        </ActionButton>
        <span className="font-mono text-[10px] text-zinc-600">
          POST {creating ? "/auth/register" : "/auth/login"}
        </span>
      </div>
      {allowRegister && (
        <button
          type="button"
          onClick={() => {
            setCreating((c) => !c);
            setError(null);
          }}
          className={`rounded-sm font-mono text-[10px] text-zinc-500 underline underline-offset-2 transition-colors hover:text-zinc-300 ${FOCUS_RING}`}
        >
          {creating ? "I already have an account" : "Create an account instead"}
        </button>
      )}
      {error && (
        <p role="alert" className="break-words font-mono text-[11px] text-rose-300">
          {error}
        </p>
      )}
    </form>
  );
}
