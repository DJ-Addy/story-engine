"use client";

// The one place a viewer can find out whether the numbers on screen came from
// the API or from fixtures. Mounted in the header of every data-backed page.
//
// This exists because the mock/live switch used to be invisible: a deployment
// that forgot NEXT_PUBLIC_USE_MOCK_API served fabricated scores that looked
// exactly like real ones. The default is fixed in lib/api.ts; this badge makes
// the outcome impossible to miss either way.

import { API_BASE_URL } from "@/lib/apiClient";
import { API_MODE, API_MODE_IS_UNSAFE_DEPLOY, API_MODE_REASON } from "@/lib/api";
import { FOCUS_RING } from "@/components/casting/theme";

type Skin = {
  dot: string;
  pill: string;
  text: string;
  short: string;
  headline: string;
};

const LIVE: Skin = {
  dot: "bg-emerald-400 shadow-[0_0_8px_1px_rgb(52_211_153_/_0.7)]",
  pill: "border-emerald-500/35 bg-emerald-500/[0.08]",
  text: "text-emerald-200",
  short: "live api",
  headline: "Live FastAPI backend",
};

const MOCK_DEV: Skin = {
  dot: "bg-amber-400 shadow-[0_0_8px_1px_rgb(251_191_36_/_0.6)]",
  pill: "border-amber-500/35 bg-amber-500/[0.08]",
  text: "text-amber-200",
  short: "mock data",
  headline: "Fixture data — nothing here is real",
};

const MOCK_SHIPPED: Skin = {
  dot: "bg-rose-400 shadow-[0_0_10px_2px_rgb(251_113_133_/_0.8)] motion-safe:animate-pulse",
  pill: "border-rose-500/50 bg-rose-500/[0.12]",
  text: "text-rose-200",
  short: "mock data — not real",
  headline: "A shipped build is serving fixture data",
};

export default function ApiModeBadge({ className = "" }: { className?: string }) {
  const skin =
    API_MODE === "live" ? LIVE : API_MODE_IS_UNSAFE_DEPLOY ? MOCK_SHIPPED : MOCK_DEV;

  const detail =
    API_MODE === "live"
      ? `Every score, timeline and render on this page is fetched from ${API_BASE_URL}. Failures are shown as failures — never replaced with sample data.`
      : "Scores, timelines and renders on this page come from fixtures in lib/mock.ts and a client-side port of the judge heuristic. Do not read them as results.";

  return (
    <span className={`group relative inline-flex ${className}`}>
      <span
        tabIndex={0}
        role="status"
        aria-label={`Data source: ${skin.headline}`}
        className={`inline-flex cursor-help items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider ${skin.pill} ${skin.text} ${FOCUS_RING}`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${skin.dot}`} aria-hidden />
        {skin.short}
      </span>

      {/* Hover / focus detail. Pointer-events-none so it never eats a click. */}
      <span className="pointer-events-none absolute right-0 top-[calc(100%+8px)] z-50 w-72 origin-top-right scale-95 rounded-xl border border-[var(--hairline-strong)] bg-[var(--surface-1)] p-3 opacity-0 shadow-[0_20px_60px_-20px_rgba(0,0,0,0.9)] backdrop-blur-md transition-[opacity,transform] duration-150 group-hover:scale-100 group-hover:opacity-100 group-focus-within:scale-100 group-focus-within:opacity-100">
        <span className={`block font-mono text-[10px] uppercase tracking-wider ${skin.text}`}>
          {skin.headline}
        </span>
        <span className="mt-1.5 block text-[11px] leading-relaxed text-zinc-400">
          {detail}
        </span>
        <span className="mt-2 block border-t border-[var(--hairline)] pt-2 font-mono text-[10px] leading-relaxed text-zinc-600">
          {API_MODE_REASON}
        </span>
      </span>
    </span>
  );
}
