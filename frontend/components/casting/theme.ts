// Presentation-only helpers for the Casting Studio. Centralizes the brand
// focus ring, the emotion accent palette (mapped to the --emotion-* CSS
// tokens in globals.css), and the severity vocabulary styling so the casting
// components stay cohesive. No behavior, store, or type contracts live here.

import type { CSSProperties } from "react";
import type { Severity } from "@/lib/types";

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
