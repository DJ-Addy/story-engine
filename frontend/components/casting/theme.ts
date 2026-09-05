// Presentation-only helpers for the Casting Studio. Centralizes the brand
// focus ring, the emotion accent palette (mapped to the --emotion-* CSS
// tokens in globals.css), and the severity vocabulary styling so the casting
// components stay cohesive. No behavior, store, or type contracts live here.

import type { CSSProperties } from "react";
import type { Severity } from "@/lib/types";
import { ApiError, ContractMismatchError } from "@/lib/apiClient";

/** Matches the amber focus ring used across the landing pages. */
export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950";

// Canonical delivery emotions -> their accent token (see EMOTIONS in lib/types).
const EMOTION_VARS: Record<string, string> = {
  angry: "var(--emotion-angry)",
  shouting: "var(--emotion-shouting)",
  urgent: "var(--emotion-urgent)",
  happy: "var(--emotion-happy)",
  excited: "var(--emotion-excited)",
  surprised: "var(--emotion-surprised)",
  sarcastic: "var(--emotion-sarcastic)",
  afraid: "var(--emotion-afraid)",
  whispering: "var(--emotion-whispering)",
  sad: "var(--emotion-sad)",
  calm: "var(--emotion-calm)",
  serious: "var(--emotion-serious)",
};

/** Inline style that colors an `.emotion-chip` for a given emotion label. */
export function emotionStyle(emotion: string): CSSProperties {
  return {
    ["--ec"]: EMOTION_VARS[emotion] ?? "var(--emotion-neutral)",
  } as CSSProperties;
}

/** Severity dot + text colors, cohesive with the score-band rose/amber/sky. */
export const SEVERITY: Record<Severity, { dot: string; text: string }> = {
  error: { dot: "bg-rose-500", text: "text-rose-300" },
  warn: { dot: "bg-amber-400", text: "text-amber-300" },
  info: { dot: "bg-sky-400", text: "text-sky-300" },
};

// --------------------------------------------------------------------------- //
// Failure copy
//
// Every panel that calls the API renders the same shape of failure, so the
// translation from a thrown value to on-screen words lives in one place. The
// server's own `detail` string is always preserved — it is written for humans
// ("Rights not attested for this project", "Cost cap exceeded: …") and is far
// more useful than anything invented here.
// --------------------------------------------------------------------------- //

export type FailureKind =
  | "auth" // 401 — no/expired token
  | "forbidden" // 403 — a gate refused (rights attestation)
  | "missing" // 404 — the resource has not been authored yet
  | "budget" // 402 — the cost governor refused
  | "contract" // a value the API schema cannot hold; never sent
  | "invalid" // 422 — the server rejected the body
  | "provider" // 502/503 — a model/TTS/video provider is unavailable
  | "server" // other 5xx
  | "network" // fetch never reached the server
  | "unknown";

export interface Failure {
  kind: FailureKind;
  /** Short line, in the panel's voice. */
  headline: string;
  /** The server's wording, or the thrown message. */
  detail: string;
  /** What the viewer can actually do about it, when there is something. */
  hint: string | null;
}

/** Normalize anything thrown by the api layer into displayable copy. */
export function describeFailure(err: unknown): Failure {
  if (err instanceof ContractMismatchError) {
    return {
      kind: "contract",
      headline: "The API cannot represent this",
      detail: err.message,
      hint: "Nothing was sent — adjust the values above and try again.",
    };
  }

  if (err instanceof ApiError) {
    const detail = err.message;
    if (err.status === 401) {
      return {
        kind: "auth",
        headline: "Not signed in",
        detail,
        hint: "This project's endpoints require a bearer token; sign in and retry.",
      };
    }
    if (err.status === 403) {
      return { kind: "forbidden", headline: "Refused", detail, hint: null };
    }
    if (err.status === 402) {
      return {
        kind: "budget",
        headline: "Cost cap reached",
        detail,
        hint: "Raise the project's cost cap to continue.",
      };
    }
    if (err.status === 404) {
      return { kind: "missing", headline: "Nothing to work with yet", detail, hint: null };
    }
    if (err.status === 422) {
      return { kind: "invalid", headline: "The server rejected this request", detail, hint: null };
    }
    if (err.status === 502 || err.status === 503) {
      // The API answers "provider unavailable / upstream failed" with the
      // provider's own wording, which usually names the missing configuration.
      return {
        kind: "provider",
        headline: "The AI provider is unavailable",
        detail,
        hint: "Nothing was charged. Everything that does not need a provider still works.",
      };
    }
    if (err.status >= 500) {
      return {
        kind: "server",
        headline: "The server failed",
        detail,
        hint: "Retry — if it persists, check the API logs.",
      };
    }
    return { kind: "unknown", headline: "Request failed", detail, hint: null };
  }

  if (err instanceof TypeError) {
    // fetch() rejects with a TypeError when it never reached the server.
    return {
      kind: "network",
      headline: "Could not reach the API",
      detail: err.message,
      hint: "The backend may be down, or /api/v1 may not be proxied to it.",
    };
  }

  return {
    kind: "unknown",
    headline: "Something went wrong",
    detail: err instanceof Error ? err.message : String(err),
    hint: null,
  };
}
