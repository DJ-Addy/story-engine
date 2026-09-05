"use client";

// GET .../analytics/spend (also panel 4 of /dashboard).
//
// Cost, output and latency per provider/model. The bar is `cost_cents` — one
// measure, so one colour; colouring each provider differently would spend the
// identity channel re-encoding the length the reader can already see.
//
// The tick on the same track is `estimated_cents`. The query's own docstring
// calls that pair the interesting one: the cost governor spends the *estimate*,
// so a persistent gap between the tick and the bar end means the caps are
// protecting the wrong number. Same hue, two shades — it is the same measure
// twice, not a second series.

import {
  num,
  numOr,
  sumOf,
  text,
  type AnalyticsPanel,
  type AnalyticsRow,
} from "@/lib/analyticsApi";
import { BarList, Legend, Plot, type BarDatum } from "@/components/dashboard/charts";
import { PanelCard } from "@/components/dashboard/PanelCard";
import {
  count,
  duration,
  latency,
  money,
  SERIES,
  SHADE,
} from "@/components/dashboard/theme";

function toDatum(row: AnalyticsRow, i: number): BarDatum {
  const cost = numOr(row, "cost_cents", 0);
  const estimated = num(row, "estimated_cents");
  const provider = text(row, "provider") || "—";
  const kind = text(row, "kind");
  const model = text(row, "model");
  const avgMs = num(row, "avg_latency_ms");
  const p95Ms = num(row, "p95_latency_ms");
  const outputMs = num(row, "output_ms");
  return {
    key: `${provider}:${kind}:${model}:${i}`,
    label: provider,
    sub: [kind, model].filter(Boolean).join(" · ") || undefined,
    value: cost,
    color: SERIES.primary,
    valueLabel: money(cost),
    marker: estimated === null ? undefined : { value: estimated, color: SHADE.dim },
    tip: {
      title: `${provider}${model ? ` · ${model}` : ""}`,
      rows: [
        { label: "actual cost", value: money(cost), color: SERIES.primary },
        ...(estimated !== null
          ? [{ label: "estimated cost", value: money(estimated), color: SHADE.dim }]
          : []),
        { label: "renders", value: count(num(row, "renders") ?? 0) },
        ...(outputMs !== null ? [{ label: "output", value: duration(outputMs) }] : []),
        ...(avgMs !== null ? [{ label: "avg latency", value: latency(avgMs) }] : []),
        ...(p95Ms !== null ? [{ label: "p95 latency", value: latency(p95Ms) }] : []),
      ],
    },
  };
}

export function SpendByProviderPanel({
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
  const rows = panel?.rows ?? []; // already ordered cost_cents DESC by the query
  const max = rows.reduce(
    (acc, r) => Math.max(acc, numOr(r, "cost_cents", 0), numOr(r, "estimated_cents", 0)),
    0,
  );
  const total = sumOf(rows, "cost_cents");
  const estimated = sumOf(rows, "estimated_cents");
  const gap = estimated - total;

  return (
    <PanelCard
      panelKey="spendByProvider"
      panel={panel}
      title="Spend by provider"
      loading={loading}
      refreshing={refreshing}
      className={className}
      emptyWhat="no render has been billed to any provider in this project."
      emptyProduces="an audio or video render (POST /render/audio, /render/video)"
      legend={
        <Legend
          items={[
            { label: "actual cost_cents", color: SERIES.primary, shape: "bar" },
            { label: "estimated_cents", color: SHADE.dim, shape: "dot" },
          ]}
        />
      }
      footnote={
        rows.length
          ? `${money(total)} actual against ${money(estimated)} estimated across ${count(rows.length)} provider rows — the governor spends the estimate, so the ${money(Math.abs(gap))} gap is what the caps are actually protecting.`
          : undefined
      }
    >
      <Plot>
        <BarList data={rows.map(toDatum)} max={max} labelWidth="10rem" />
      </Plot>
    </PanelCard>
  );
}
