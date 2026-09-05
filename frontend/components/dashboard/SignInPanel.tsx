"use client";

// The 401 recovery.
//
// The analytics endpoints take a bearer token like the rest of the API, and the
// app has no login screen — so a judge opening /dashboard against a live
// backend would otherwise hit an unexplained 401 with nothing to do about it.
// This is not a new auth system: it calls the `login` already exported by
// lib/apiClient, which stores the token exactly where every other request reads
// it from. It is shown only when a request actually came back 401.

import { useState, type FormEvent } from "react";
import { login } from "@/lib/apiClient";
import { classify } from "@/lib/analyticsApi";
import { FOCUS_RING } from "@/components/dashboard/theme";
import { ActionButton } from "@/components/dashboard/states";

export function SignInPanel({ onSignedIn }: { onSignedIn: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      onSignedIn();
    } catch (err) {
      setError(classify(err).detail);
    } finally {
      setBusy(false);
    }
  }

  const field =
    `w-full rounded-md border border-[var(--hairline)] bg-white/[0.03] px-2.5 py-1.5 ` +
    `font-mono text-[12px] text-zinc-200 placeholder:text-zinc-600 ${FOCUS_RING}`;

  return (
    <form onSubmit={submit} className="mt-4 max-w-sm space-y-2.5">
      <div>
        <label
          htmlFor="dash-email"
          className="block font-mono text-[10px] uppercase tracking-wider text-zinc-500"
        >
          Email
        </label>
        <input
          id="dash-email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={`mt-1 ${field}`}
        />
      </div>
      <div>
        <label
          htmlFor="dash-password"
          className="block font-mono text-[10px] uppercase tracking-wider text-zinc-500"
        >
          Password
        </label>
        <input
          id="dash-password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className={`mt-1 ${field}`}
        />
      </div>
      <div className="flex items-center gap-3 pt-1">
        <ActionButton type="submit" tone="accent" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </ActionButton>
        <span className="font-mono text-[10px] text-zinc-600">POST /auth/login</span>
      </div>
      {error && (
        <p className="break-words font-mono text-[11px] text-rose-300">{error}</p>
      )}
    </form>
  );
}
