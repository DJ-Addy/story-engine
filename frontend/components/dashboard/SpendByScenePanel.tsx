"use client";

// GET .../analytics/spend-by-scene (also panel 5 of /dashboard).
//
// The shot list's real budget. Rows arrive ordered by cost descending, which is
// the order that answers "where did the money go" — so the API's order is kept
// rather than re-sorted into scene order, which would answer a different
// question.
//
// One measure, one colour. The render counts and the provider set are text and
// tooltip rather than a second encoding: audio_renders and video_renders are
// counts, not money, and stacking them onto a cost bar would be a second scale
// on one axis.

import {
  list,
  num,
  numOr,
  sumOf,
  text,
  time,
  type AnalyticsPanel,
  type AnalyticsRow,
} from "@/lib/analyticsApi";
import { BarList, Plot, type BarDatum } from "@/components/dashboard/charts";
import { PanelCard } from "@/components/dashboard/PanelCard";
import { ago, count, duration, money, SERIES } from "@/components/dashboard/theme";

function toDatum(row: AnalyticsRow, i: number): BarDatum {
  const cost = numOr(row, "cost_cents", 0);
  const ordinal = num(row, "scene_ordinal");
  const label = ordinal === null ? text(row, "scene_ordinal") || "—" : `Scene ${ordinal}`;
  const audio = num(row, "audio_renders") ?? 0;
  const video = num(row, "video_renders") ?? 0;
  const providers = list(row, "providers");
  const outputMs = num(row, "output_ms");
  return {
    key: `${label}-${i}`,
    label,
    sub: `${count(audio)} audio · ${count(video)} video`,
    value: cost,
    color: SERIES.primary,
    valueLabel: money(cost),
    trailing: providers.length ? (
      <span className="hidden max-w-[10rem] truncate font-mono text-[10px] text-zinc-500 md:inline">
        {providers.join(" · ")}
      </span>
    ) : undefined,
    tip: {
      title: label,
      rows: [
        { label: "cost", value: money(cost), color: SERIES.primary },
        { label: "audio renders", value: count(audio) },
        { label: "video renders", value: count(video) },
        ...(outputMs !== null ? [{ label: "output", value: duration(outputMs) }] : []),
        ...(providers.length
          ? [{ label: "providers", value: providers.join(", ") }]
          : []),
        { label: "last render", value: ago(time(row, "last_render_at")) },
      ],
    },
  };
}

export function SpendByScenePanel({
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
  const rows = panel?.rows ?? [];
  const max = rows.reduce((acc, r) => Math.max(acc, numOr(r, "cost_cents", 0)), 0);
  const total = sumOf(rows, "cost_cents");
  const output = sumOf(rows, "output_ms");

  return (
    <PanelCard
      panelKey="spendByScene"
      panel={panel}
      title="Spend by scene"
      loading={loading}
      refreshing={refreshing}
      className={className}
      emptyWhat="no scene in this project has been rendered."
      emptyProduces="a scene render (POST /scenes/{ordinal}/render/audio or /render/video)"
      footnote={
        rows.length
          ? `${money(total)} across ${count(rows.length)} scenes for ${duration(output)} of finished media. Most expensive first.`
          : undefined
      }
    >
      <Plot>
        <BarList data={rows.map(toDatum)} max={max} labelWidth="7rem" />
      </Plot>
    </PanelCard>
  );
}
