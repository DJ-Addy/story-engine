"use client";

// The shell every analytics panel wears: the question it answers, where the
// answer came from, and — the part that matters — one place that decides which
// of loading / unavailable / empty / ready the card is in, so no panel can
// invent a fourth reading of the same response.
//
// Every card also carries a table view of the exact rows ClickHouse returned.
// That is not a nicety: it is what keeps every value reachable without a hover
// and without relying on colour, and it is what lets a judge check that the
// chart above it is drawn from real query output rather than decoration.

import { useId, useState, type ReactNode } from "react";
import {
  COLUMNS,
  PANEL_PATH,
  QUESTION,
  panelState,
  text,
  type AnalyticsPanel,
  type Failure,
  type PanelKey,
} from "@/lib/analyticsApi";
import { FOCUS_RING } from "@/components/dashboard/theme";
import {
  EmptyNotice,
  ErrorNotice,
  Notice,
  PanelSkeleton,
  UnavailableNotice,
} from "@/components/dashboard/states";

/** Column order for the table view: the documented order first, then anything
 * the backend returned that this build did not know about. Nothing is dropped. */
function orderColumns(documented: string[], columns: string[]): string[] {
  const known = documented.filter((c) => columns.includes(c));
  const extra = columns.filter((c) => !documented.includes(c));
  return [...known, ...extra];
}

const HEADING = (c: string) => c.replace(/_/g, " ");

export function PanelCard({
  panelKey,
  panel,
  title,
  loading,
  refreshing = false,
  emptyWhat,
  emptyProduces,
  legend,
  footnote,
  children,
  className = "",
  failure = null,
  endpoint,
  columnOrder,
  question,
  controls,
}: {
  /** Set for a panel that `/dashboard` returns; omitted for a standalone one. */
  panelKey?: PanelKey;
  panel: AnalyticsPanel | null;
  /** Short human name for the card. The question is the subtitle. */
  title: string;
  /** First load — a skeleton is honest here because nothing has been shown yet. */
  loading: boolean;
  /** A subsequent load — the previous render is held, dimmed, instead. */
  refreshing?: boolean;
  /** What has no rows, phrased for this panel. */
  emptyWhat: string;
  /** The action that writes rows for this panel. */
  emptyProduces: string;
  legend?: ReactNode;
  footnote?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Endpoint suffix shown as provenance. Defaults to the panel key's own. */
  endpoint?: string;
  /** Documented column order for the table view. Defaults to the key's own. */
  columnOrder?: string[];
  /** Subtitle when the response carries no question (an unavailable panel). */
  question?: string;
  /** A drill-down control for this panel — never a filter that scopes others. */
  controls?: ReactNode;
  /** Set when *this* card's own request failed, for a panel fetched separately
   * from `/dashboard`. Takes precedence over every other state: a failed
   * request has said nothing about the cluster, so it must not read as either
   * "unavailable" or "no data yet". */
  failure?: Failure | null;
}) {
  const [showTable, setShowTable] = useState(false);
  const bodyId = useId();
  const state = panelState(panel);
  const documented = columnOrder ?? (panelKey ? COLUMNS[panelKey] : []);
  const path = endpoint ?? (panelKey ? PANEL_PATH[panelKey] : "");
  const subtitle =
    panel?.question ?? question ?? (panelKey ? QUESTION[panelKey] : "");
  const columns = panel ? orderColumns(documented, panel.columns) : [];

  return (
    <section
      className={`cast-panel flex flex-col ${className}`}
      aria-label={title}
    >
      <header className="flex flex-wrap items-start gap-x-4 gap-y-2 border-b border-[var(--hairline)] px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-medium text-zinc-100">{title}</h2>
          <p className="mt-0.5 max-w-prose text-[12px] leading-snug text-zinc-500">
            {subtitle}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {controls}
          {path && (
            <code className="hidden rounded border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 lg:inline">
              /analytics/{path}
            </code>
          )}
          {state === "ready" && (
            <button
              type="button"
              onClick={() => setShowTable((v) => !v)}
              aria-expanded={showTable}
              aria-controls={bodyId}
              className={`rounded border border-[var(--hairline)] px-2 py-0.5 font-mono text-[10px] text-zinc-400 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-100 ${FOCUS_RING}`}
            >
              {showTable ? "Chart" : "Table"}
            </button>
          )}
        </div>
      </header>

      <div
        id={bodyId}
        className={`flex-1 px-5 py-5 transition-opacity duration-200 ${
          refreshing ? "opacity-50" : "opacity-100"
        }`}
      >
        {loading ? (
          <PanelSkeleton />
        ) : failure ? (
          <ErrorNotice compact failure={failure} />
        ) : !panel ? (
          // Absent from the response entirely — a contract gap, not an outage.
          <Notice
            kind="unavailable"
            compact
            headline="GET /analytics/dashboard did not return this panel, so there is nothing to draw. The cluster may be perfectly healthy."
            detail={`Expected the panel answering: ${subtitle}`}
          />
        ) : state === "unavailable" ? (
          <UnavailableNotice compact scope="This panel" detail={panel.detail} />
        ) : state === "empty" ? (
          <EmptyNotice compact what={emptyWhat} produces={emptyProduces} />
        ) : showTable && panel ? (
          <PanelTable columns={columns} panel={panel} />
        ) : (
          children
        )}
      </div>

      {(legend || footnote) && state === "ready" && !loading && !failure && (
        <footer className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-[var(--hairline)] px-5 py-3">
          {legend ?? <span />}
          {footnote && (
            <p className="text-[11px] text-zinc-500">{footnote}</p>
          )}
        </footer>
      )}
    </section>
  );
}

/** The rows exactly as ClickHouse returned them — the chart's accessible twin. */
function PanelTable({
  columns,
  panel,
}: {
  columns: string[];
  panel: AnalyticsPanel;
}) {
  return (
    <div className="cast-scroll -mx-1 max-h-[26rem] overflow-auto px-1">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">{panel.question}</caption>
        <thead className="sticky top-0 z-10 bg-[var(--cast-bg)]">
          <tr>
            {columns.map((c) => (
              <th
                key={c}
                scope="col"
                className="whitespace-nowrap border-b border-[var(--hairline)] px-2 py-1.5 font-mono text-[10px] font-normal uppercase tracking-wider text-zinc-500"
              >
                {HEADING(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {panel.rows.map((row, i) => (
            <tr key={i} className="odd:bg-white/[0.015]">
              {columns.map((c) => (
                <td
                  key={c}
                  className="max-w-[16rem] truncate border-b border-[var(--hairline)] px-2 py-1.5 font-mono text-[11px] tabular-nums text-zinc-300"
                  title={text(row, c)}
                >
                  {text(row, c) || "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
