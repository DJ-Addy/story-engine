"use client";

// The states this dashboard spends most of its life in.
//
// A ClickHouse-backed page has four distinct non-happy answers, and collapsing
// any two of them lies to the reader:
//
//   unavailable  the cluster is unconfigured or unreachable. `available: false`
//                arrives at HTTP 200 — it is an answer, not a failure. There is
//                no number here and there never was one.
//   empty        the cluster answered and the answer is zero rows. The pipeline
//                is healthy; nothing has been judged or rendered yet.
//   error        the request never reached an answer: no token, wrong project,
//                backend down. The backend has said nothing about the data.
//   loading      in flight, first time. On a *re*fetch the previous render is
//                held at reduced opacity instead (see PanelCard) — a skeleton
//                flash on refresh is a layout jump for no information.
//
// Each carries its own colour, glyph and word, so the distinction survives
// colour-blindness, a projector's washed-out gamut, and a greyscale still from
// the demo video.

import type { ReactNode } from "react";
import { FOCUS_RING, STATUS } from "@/components/dashboard/theme";
import type { Failure } from "@/lib/analyticsApi";

type NoticeKind = "unavailable" | "empty" | "error";

const NOTICE: Record<
  NoticeKind,
  { color: string; glyph: string; word: string; tint: string }
> = {
  unavailable: {
    color: STATUS.warning,
    glyph: "⚠",
    word: "Unavailable",
    tint: "rgba(217,119,6,0.07)",
  },
  empty: {
    color: "#71717a",
    glyph: "○",
    word: "No data yet",
    tint: "rgba(255,255,255,0.02)",
  },
  error: {
    color: STATUS.critical,
    glyph: "✕",
    word: "Request failed",
    tint: "rgba(244,63,94,0.07)",
  },
};

/**
 * The one notice shape. `headline` says what happened in a sentence; `detail`
 * carries the backend's own words verbatim where there are any — never
 * paraphrased, because "connection refused" and "table not found" send a reader
 * to different places.
 */
export function Notice({
  kind,
  headline,
  detail,
  children,
  actions,
  compact = false,
}: {
  kind: NoticeKind;
  headline: string;
  /** The backend's message, shown as-is in mono. */
  detail?: string | null;
  /** What is missing, or what to run to produce rows. */
  children?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}) {
  const n = NOTICE[kind];
  return (
    <div
      className={`rounded-xl border border-[var(--hairline)] ${compact ? "px-4 py-4" : "px-5 py-6"}`}
      style={{ background: n.tint }}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className="text-sm leading-none" style={{ color: n.color }}>
          {n.glyph}
        </span>
        <span
          className="font-mono text-[10px] uppercase tracking-[0.16em]"
          style={{ color: n.color }}
        >
          {n.word}
        </span>
      </div>
      <p className="mt-2 max-w-prose text-[13px] leading-relaxed text-zinc-300">
        {headline}
      </p>
      {detail && (
        <p className="mt-2 max-w-prose break-words rounded border border-[var(--hairline)] bg-black/30 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-zinc-400">
          {detail}
        </p>
      )}
      {children && <div className="mt-3 text-[12px] text-zinc-500">{children}</div>}
      {actions && <div className="mt-4 flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

/** The unavailable notice, with the cluster facts a reader needs to fix it. */
export function UnavailableNotice({
  detail,
  scope,
  missing,
  actions,
  compact,
}: {
  detail: string | null;
  /** "This panel" / "Every panel" — what the outage covers. */
  scope: string;
  /** Cluster facts: host, database, absent tables. */
  missing?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <Notice
      kind="unavailable"
      compact={compact}
      headline={`${scope} is unavailable because ClickHouse did not answer. Nothing is being estimated or filled in — there are no numbers to show.`}
      detail={detail ?? "The API returned no reason."}
      actions={actions}
    >
      {missing}
    </Notice>
  );
}

/** The empty notice — visibly *not* the unavailable one. */
export function EmptyNotice({
  what,
  produces,
  compact,
}: {
  /** What has no rows, in the panel's own words. */
  what: string;
  /** The action that writes rows for this panel. */
  produces: string;
  compact?: boolean;
}) {
  return (
    <Notice
      kind="empty"
      compact={compact}
      headline={`ClickHouse answered, and the answer is zero rows: ${what}`}
    >
      <span>
        Rows appear here after {produces}. The cluster is reachable — this panel
        is empty, not broken.
      </span>
    </Notice>
  );
}

/** The transport-failure notice. Distinct from both of the above. */
export function ErrorNotice({
  failure,
  actions,
  compact,
}: {
  failure: Failure;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <Notice
      kind="error"
      compact={compact}
      headline={`${failure.title}. The request did not reach an answer, so nothing below reflects the cluster's real state.`}
      detail={failure.detail}
      actions={actions}
    />
  );
}

/** First-load placeholder. Reuses the studio's shimmer so the page does not
 * introduce a second loading language. */
export function PanelSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-4">
          <div className="cast-shimmer h-3 w-32 rounded" />
          <div
            className="cast-shimmer h-2.5 flex-1 rounded"
            style={{ maxWidth: `${90 - i * 11}%` }}
          />
        </div>
      ))}
    </div>
  );
}

/** The page's one button style, so actions read the same everywhere. */
export function ActionButton({
  onClick,
  children,
  tone = "quiet",
  type = "button",
  disabled,
}: {
  onClick?: () => void;
  children: ReactNode;
  tone?: "quiet" | "accent";
  type?: "button" | "submit";
  disabled?: boolean;
}) {
  const base =
    "rounded-md px-2.5 py-1.5 font-mono text-[11px] transition-colors disabled:opacity-50";
  const skin =
    tone === "accent"
      ? "border border-amber-400/30 bg-amber-400/10 text-amber-200 hover:bg-amber-400/20"
      : "border border-[var(--hairline)] bg-white/[0.03] text-zinc-300 hover:border-[var(--hairline-strong)] hover:text-zinc-100";
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${skin} ${FOCUS_RING}`}
    >
      {children}
    </button>
  );
}
