"use client";

// GET .../analytics/animatic-trend (also panel 2 of /dashboard).
//
// The panel's own question is "which scenes improved and which regressed", and
// the API answers it with argMin/argMax over event_time — first score and
// latest score in one pass. That is a before/after pair per scene, so the form
// is a dumbbell: one hue in two shades for the two ends, and the connector
// carrying the direction of travel. A grouped bar chart would make the reader
// do the subtraction the query already did.
//
// Underneath, the four quality axes the judge scores separately (coverage,
// continuity, variety, pacing) are a scene × axis grid, which is a heatmap on
// one sequential hue. They are averages, not first/latest, so they answer a
// different question and get their own mark rather than being crammed into the
// dumbbell.

import {
  byAsc,
  num,
  numOr,
  text,
  time,
  type AnalyticsPanel,
  type AnalyticsRow,
} from "@/lib/analyticsApi";
import {
  DumbbellList,
  HeatGrid,
  Legend,
  Plot,
  RampLegend,
  type DumbbellDatum,
  type HeatRow,
} from "@/components/dashboard/charts";
import { PanelCard } from "@/components/dashboard/PanelCard";
import {
  ago,
  count,
  direction,
  rampStep,
  score100,
  SHADE,
  signed,
} from "@/components/dashboard/theme";

const AXES: { column: string; label: string }[] = [
  { column: "avg_coverage", label: "coverage" },
  { column: "avg_continuity", label: "continuity" },
  { column: "avg_variety", label: "variety" },
  { column: "avg_pacing", label: "pacing" },
];

const sceneLabel = (row: AnalyticsRow): string => {
  const ordinal = num(row, "scene_ordinal");
  return ordinal === null ? text(row, "scene_ordinal") || "—" : `Scene ${ordinal}`;
};

function toDumbbell(row: AnalyticsRow): DumbbellDatum {
  const first = numOr(row, "first_score", 0);
  const latest = numOr(row, "latest_score", 0);
  // `delta` is returned by the query; it is not recomputed here.
  const delta = numOr(row, "delta", latest - first);
  const dir = direction(delta);
  const label = sceneLabel(row);
  return {
    key: label,
    label,
    sub: `${count(num(row, "judgements") ?? 0)} judged`,
    from: first,
    to: latest,
    connector: dir ? { color: dir.color, glyph: dir.glyph, word: dir.name } : null,
    fromColor: SHADE.dim,
    toColor: SHADE.lit,
    valueLabel: signed(delta * 100, 0),
    tip: {
      title: label,
      rows: [
        { label: "first score", value: score100(first), color: SHADE.dim },
        { label: "latest score", value: score100(latest), color: SHADE.lit },
        { label: "delta", value: signed(delta * 100, 0) },
        { label: "average", value: score100(numOr(row, "avg_score", 0)) },
        { label: "max shots", value: count(num(row, "max_shots") ?? 0) },
        {
          label: "warnings / errors",
          value: `${count(num(row, "warnings") ?? 0)} / ${count(num(row, "errors") ?? 0)}`,
        },
        { label: "last judged", value: ago(time(row, "last_judged_at")) },
      ],
    },
  };
}

function toHeatRow(row: AnalyticsRow): HeatRow {
  const label = sceneLabel(row);
  return {
    key: label,
    label,
    cells: AXES.map((a) => {
      const v = num(row, a.column);
      return { column: a.label, value: v, display: v === null ? "—" : score100(v) };
    }),
  };
}

export function AnimaticTrendPanel({
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
  const rows = panel ? byAsc(panel.rows, "scene_ordinal") : [];
  const hasAxes = rows.some((r) => AXES.some((a) => num(r, a.column) !== null));

  return (
    <PanelCard
      panelKey="animaticTrend"
      panel={panel}
      title="Animatic trend by scene"
      loading={loading}
      refreshing={refreshing}
      className={className}
      emptyWhat="no scene in this project has been judged by the animatic judge."
      emptyProduces="an animatic judge run (POST /judge/animatic) over a scene's shot list"
      legend={
        <Legend
          items={[
            { label: "first score", color: SHADE.dim, shape: "dot" },
            { label: "latest score", color: SHADE.lit, shape: "dot" },
            { label: "improved ▲ / regressed ▼", color: SHADE.lit, shape: "line" },
          ]}
        />
      }
      footnote="Scores 0-100. Delta is the API's own latest − first, not a recomputation."
    >
      <Plot className="space-y-6">
        <DumbbellList
          data={rows.map(toDumbbell)}
          domain={[0, 1]}
          labelWidth="7rem"
        />

        {hasAxes && (
          <div className="border-t border-[var(--hairline)] pt-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <h3 className="font-mono text-[11px] uppercase tracking-[0.14em] text-zinc-400">
                Quality axes · average per scene
              </h3>
              <RampLegend low="0" high="100" />
            </div>
            <HeatGrid
              rows={rows.map(toHeatRow)}
              columns={AXES.map((a) => a.label)}
              colorFor={rampStep}
            />
          </div>
        )}
      </Plot>
    </PanelCard>
  );
}
