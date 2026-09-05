"use client";

// GET .../analytics/cost-pressure (also panel 6 of /dashboard).
//
// The only panel where a refusal is visible at all: a blocked decision produces
// no render, so nothing in the operational store remembers it and without this
// table the caps look like they never fire.
//
// Two marks, two jobs:
//
//   the stacked bar   approved_cents against refused_cents per operation and
//                     provider. Approved is a plain series colour; refused
//                     wears the reserved *critical* status token, because it
//                     means something bad happened, and so it always carries
//                     the glyph and the word alongside.
//   the meter         peak_spent_cents against cap_cents — a single ratio
//                     against a limit, which is a meter and not a one-bar bar
//                     chart. Its fill escalates through the status scale as the
//                     headroom closes.

import {
  maxOf,
  num,
  numOr,
  sumOf,
  text,
  time,
  type AnalyticsPanel,
  type AnalyticsRow,
} from "@/lib/analyticsApi";
import {
  BandChip,
  Legend,
  Meter,
  Plot,
  StackedBarList,
  type StackDatum,
} from "@/components/dashboard/charts";
import { PanelCard } from "@/components/dashboard/PanelCard";
import {
  ago,
  count,
  money,
  SERIES,
  STATUS,
} from "@/components/dashboard/theme";

function toDatum(row: AnalyticsRow, i: number): StackDatum {
  const approved = numOr(row, "approved_cents", 0);
  const refused = numOr(row, "refused_cents", 0);
  const blocked = num(row, "blocked") ?? 0;
  const decisions = num(row, "decisions") ?? 0;
  const headroom = num(row, "min_headroom_cents");
  const operation = text(row, "operation") || "—";
  const provider = text(row, "provider");
  const overCap = headroom !== null && headroom < 0;
  return {
    key: `${operation}:${provider}:${i}`,
    label: operation,
    sub: provider || undefined,
    segments: [
      { label: "approved", value: approved, color: SERIES.primary },
      { label: "refused", value: refused, color: STATUS.critical, glyph: "▼" },
    ],
    total: approved + refused,
    valueLabel: money(approved + refused),
    trailing: overCap ? (
      <BandChip
        color={STATUS.critical}
        glyph="▼"
        word="over cap"
        value={money(headroom as number)}
      />
    ) : blocked > 0 ? (
      <BandChip
        color={STATUS.warning}
        glyph="●"
        word="blocked"
        value={count(blocked)}
      />
    ) : undefined,
    tip: {
      title: `${operation}${provider ? ` · ${provider}` : ""}`,
      rows: [
        { label: "approved_cents", value: money(approved), color: SERIES.primary },
        { label: "refused_cents", value: money(refused), color: STATUS.critical },
        { label: "decisions", value: count(decisions) },
        { label: "blocked", value: count(blocked) },
        ...(headroom !== null
          ? [{ label: "min headroom", value: money(headroom) }]
          : []),
        { label: "last decision", value: ago(time(row, "last_decision_at")) },
      ],
    },
  };
}

/** The meter's fill escalates through the reserved status scale. */
function pressureTone(spent: number, cap: number) {
  if (cap <= 0) return { color: SERIES.primary, glyph: "●", word: "no cap reported" };
  const used = spent / cap;
  if (used >= 1) return { color: STATUS.critical, glyph: "▼", word: "cap reached" };
  if (used >= 0.8) return { color: STATUS.warning, glyph: "●", word: "close to cap" };
  return { color: STATUS.good, glyph: "▲", word: "headroom" };
}

export function CostPressurePanel({
  panel,
  loading,
  refreshing,
  className,
}: {
  panel: AnalyticsPanel | null;
  loading: boolean;
  refreshing: boolean;
  className?: string;
}) {
  const rows = panel?.rows ?? []; // ordered approved_cents DESC by the query
  const max = rows.reduce(
    (acc, r) => Math.max(acc, numOr(r, "approved_cents", 0) + numOr(r, "refused_cents", 0)),
    0,
  );
  // These are the peaks the *rows* report, not a project-wide total — the query
  // takes max() per group, so summing them would double-count.
  const cap = maxOf(rows, "cap_cents");
  const peak = maxOf(rows, "peak_spent_cents");
  const blocked = sumOf(rows, "blocked");
  const tone = pressureTone(peak, cap);

  return (
    <PanelCard
      panelKey="costPressure"
      panel={panel}
      title="Cost pressure"
      loading={loading}
      refreshing={refreshing}
      className={className}
      emptyWhat="the cost governor has recorded no decision for this project."
      emptyProduces="any operation that asks the governor for budget — every render does"
      legend={
        <Legend
          items={[
            { label: "approved_cents", color: SERIES.primary, shape: "bar" },
            { label: "▼ refused_cents", color: STATUS.critical, shape: "bar" },
          ]}
        />
      }
      footnote={
        blocked > 0
          ? `${count(blocked)} decisions refused. A refusal produces no render, so these appear in no other view.`
          : "No decision was refused; every request fitted inside the cap."
      }
    >
      <Plot className="space-y-5">
        <StackedBarList data={rows.map(toDatum)} max={max} labelWidth="9rem" />

        {cap > 0 && (
          <div className="border-t border-[var(--hairline)] pt-4">
            <Meter
              value={peak}
              limit={cap}
              color={tone.color}
              caption="Highest spend the governor saw before a decision, against the largest cap it reported"
              valueLabel={money(peak)}
              limitLabel={money(cap)}
            />
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-zinc-500">
              <span aria-hidden style={{ color: tone.color }}>
                {tone.glyph}
              </span>
              <span>{tone.word}</span>
              <span className="text-zinc-600">
                · peak_spent_cents and cap_cents are per-row maxima, not a project total
              </span>
            </p>
          </div>
        )}
      </Plot>
    </PanelCard>
  );
}
