// Dashboard visual vocabulary: a validated chart palette, the judge's score
// bands, and the formatters every panel shares.
//
// Local to `components/dashboard/` on purpose — the casting studio's tokens in
// globals.css are a *UI* palette (surfaces, emotion accents, medals) and were
// never validated as chart marks, so the marks below are their own set. They
// stay inside the studio's family (sky / violet / emerald / amber / rose on the
// same near-black ground) so the page still reads as the same product.
//
// PALETTE PROVENANCE — every hex here was chosen by running the dataviz
// validator against this app's own chart surface (#141417: --surface-1 composited
// over --cast-bg), not by eye:
//
//   categorical pair  #0d9ddb, #8b5cf6   all-pairs, dark:
//                     band PASS · chroma PASS · CVD ΔE 9.9 (deutan) PASS ·
//                     normal-vision ΔE 19.1 PASS · contrast 6.0 / 4.3 PASS
//   sequential ramp   #1a68a4 → #7ad2f6  ordinal check, dark:
//                     monotone L PASS · adjacent ΔL PASS · dim-end contrast
//                     3.12:1 PASS · single hue (21° spread) PASS
//
// Only two categorical slots exist because only two are earned: every panel
// here plots a single measure. Where two marks share a track (average vs best,
// first vs latest, actual vs estimated) they are one hue in two shades — the
// documented dumbbell treatment — not two identities.
//
// The status scale is reserved and never used for identity. Its members are not
// mutually distinguishable under deuteranopia (good/critical sit at ΔE 5.3), so
// every use pairs the colour with a glyph AND a word. That pairing is the
// mitigation, and it is not optional: see `Band` and `STATUS` below.

/** The surface charts are drawn on — --surface-1 composited over --cast-bg. */
export const SURFACE = "#141417";

/** Marks. Slot 1 is the default single-series hue; slot 2 appears only where
 * two genuinely distinct identities share one chart. */
export const SERIES = {
  primary: "#0d9ddb",
  secondary: "#8b5cf6",
} as const;

/** One hue, two shades — for the two ends of a dumbbell / paired track. */
export const SHADE = {
  /** The earlier / estimated / reference end. */
  dim: "#1a68a4",
  /** The later / actual / current end. */
  lit: "#2fabe0",
} as const;

/** Sequential sky ramp, low → high. Validated as an ordinal ramp. */
export const RAMP = ["#1a68a4", "#0d80bd", "#2fabe0", "#7ad2f6"] as const;

/** Reserved status scale. Never used for series identity, never used alone. */
export const STATUS = {
  good: "#0da97a",
  warning: "#d97706",
  critical: "#f43f5e",
} as const;

/** Chart chrome. Text always wears an ink token, never a series colour. */
export const INK = {
  primary: "#e4e4e7",
  secondary: "#a1a1aa",
  muted: "#71717a",
  grid: "rgba(255,255,255,0.06)",
  axis: "rgba(255,255,255,0.12)",
  /** The 2px separator between touching marks: the page ground, not a stroke. */
  gap: "#141417",
} as const;

/** Focus ring, matching the rest of the app (components/casting/theme.ts). */
export const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950";

// --------------------------------------------------------------------------- //
// Score bands
//
// The thresholds are the judge's own, not a UI invention: backend/app/judge/
// voices.py :: _rationale_for bands a score as strong >= 0.75, adequate >= 0.55,
// poor below. A band is a *status*, so it always ships as colour + glyph + word.
// --------------------------------------------------------------------------- //

export type BandName = "strong" | "adequate" | "poor";

export interface Band {
  name: BandName;
  color: string;
  /** Text glyph, so the band survives colour-blindness, print and greyscale. */
  glyph: string;
}

export function band(score: number): Band {
  if (score >= 0.75) return { name: "strong", color: STATUS.good, glyph: "▲" };
  if (score >= 0.55)
    return { name: "adequate", color: STATUS.warning, glyph: "●" };
  return { name: "poor", color: STATUS.critical, glyph: "▼" };
}

/** Direction of a change, as colour + glyph + word. `null` when it did not move. */
export function direction(delta: number): Band | null {
  if (Math.abs(delta) < 0.0005) return null;
  return delta > 0
    ? { name: "strong", color: STATUS.good, glyph: "▲" }
    : { name: "poor", color: STATUS.critical, glyph: "▼" };
}

/** A hex colour at a given alpha, so a track can be a dimmer step of the fill's
 * own hue rather than an unrelated grey. Non-hex input is returned untouched. */
export function alpha(hex: string, a: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgb(${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255} / ${a})`;
}

/** Pick a ramp step for a 0..1 value. Out-of-range values clamp. */
export function rampStep(t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  const i = Math.min(RAMP.length - 1, Math.floor(clamped * RAMP.length));
  return RAMP[i];
}

// --------------------------------------------------------------------------- //
// Formatters
//
// Every one of these takes the unit the backend actually returns. Cents stay
// cents until the last moment; milliseconds are milliseconds. Nothing is
// rescaled into a unit the API did not send.
// --------------------------------------------------------------------------- //

/** Integer cents → a currency string. The API returns cents; this is the only
 * place that divides by 100. No currency symbol is implied beyond the dollar
 * sign the cost governor's own units use (backend cost_cap_cents). */
export function money(cents: number): string {
  const dollars = Math.abs(cents) / 100;
  // A negative cost-governor headroom must read "-$4.50", never "$-4.50".
  const sign = cents < 0 ? "−" : "";
  const body =
    dollars >= 1000
      ? dollars.toLocaleString(undefined, { maximumFractionDigits: 0 })
      : dollars.toLocaleString(undefined, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        });
  return `${sign}$${body}`;
}

/** A count, thousands-separated. */
export const count = (n: number): string => n.toLocaleString();

/** Milliseconds of produced media → h/m/s. `output_ms` and `duration_ms`. */
export function duration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "0s";
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Milliseconds of latency → ms or s. Distinct from `duration`: a latency of
 * 480ms must not read as "0s". */
export function latency(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** A 0..1 score as 0-100, the way the casting studio shows it. */
export const score100 = (score: number): string => String(Math.round(score * 100));

/** A 0..1 share as a percentage. */
export const percent = (share: number): string => `${Math.round(share * 100)}%`;

/** A signed value, always carrying its sign so a delta is never ambiguous. */
export const signed = (n: number, digits = 0): string =>
  `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}`;

/** Epoch ms → a short absolute timestamp. `null` renders as an em dash so an
 * absent time never masquerades as the epoch. */
export function when(ms: number | null): string {
  if (ms === null) return "—";
  const d = new Date(ms);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Epoch ms → "4m ago". Falls back to the absolute form beyond a week. */
export function ago(ms: number | null, now = Date.now()): string {
  if (ms === null) return "—";
  const secs = Math.round((now - ms) / 1000);
  if (secs < 0) return when(ms);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days <= 7) return `${days}d ago`;
  return when(ms);
}

/** Shorten an opaque id (a ClickHouse run_id) without pretending it is a name. */
export const shortId = (id: string): string =>
  id.length <= 12 ? id : `${id.slice(0, 8)}…${id.slice(-3)}`;
