"use client";

// GET .../analytics/bake-offs (also panel 3 of /dashboard).
//
// One row per ranking run, reassembled from the shared run_id every
// /judge/rank/* call writes under. The interesting quantity is not the winner's
// score on its own but how far clear it finished, so the mark is a paired track
// from the losing end to the winning end with the margin direct-labelled.
//
// A NOTE ON THE COLUMN NAME. The API calls the lower end `runner_up_score`, and
// it is computed as `argMax(score, candidate_rank)` — the *lowest-ranked*
// candidate, which is the runner-up only when a run had exactly two candidates.
// The labels below therefore say what the column is called and the footnote
// says what it measures, rather than quietly implying second place.

import {
  num,
  numOr,
  text,
  time,
  type AnalyticsPanel,
  type AnalyticsRow,
} from "@/lib/analyticsApi";
import {
  DumbbellList,
  Legend,
  Plot,
  type DumbbellDatum,
} from "@/components/dashboard/charts";
import { PanelCard } from "@/components/dashboard/PanelCard";
import {
  count,
  direction,
  score100,
  SHADE,
  shortId,
  signed,
  when,
} from "@/components/dashboard/theme";

function toDatum(row: AnalyticsRow, i: number): DumbbellDatum {
  const winner = numOr(row, "winner_score", 0);
  const lowest = numOr(row, "runner_up_score", 0);
  const margin = numOr(row, "margin", winner - lowest);
  const dir = direction(margin);
  const runId = text(row, "run_id");
  const label = text(row, "winner") || "—";
  const judge = text(row, "judge") || "judge";
  const candidates = num(row, "candidates") ?? 0;
  return {
    key: runId || `run-${i}`,
    label,
    sub: `${judge} · ${count(candidates)} candidates`,
    from: lowest,
    to: winner,
    // A margin of zero is a genuine tie, so it gets no direction glyph.
    connector: dir ? { color: dir.color, glyph: dir.glyph, word: dir.name } : null,
    fromColor: SHADE.dim,
    toColor: SHADE.lit,
    valueLabel: signed(margin * 100, 0),
    tip: {
      title: `${label} · ${judge}`,
      rows: [
        { label: "winner_score", value: score100(winner), color: SHADE.lit },
        { label: "runner_up_score", value: score100(lowest), color: SHADE.dim },
        { label: "margin", value: signed(margin * 100, 0) },
        { label: "candidates", value: count(candidates) },
        { label: "ran at", value: when(time(row, "ran_at")) },
        ...(runId ? [{ label: "run_id", value: shortId(runId) }] : []),
      ],
    },
  };
}

export function BakeOffsPanel({
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
  const rows = panel?.rows ?? []; // already ordered ran_at DESC by the query

  return (
    <PanelCard
      panelKey="bakeOffs"
      panel={panel}
      title="Bake-offs"
      loading={loading}
      refreshing={refreshing}
      className={className}
      emptyWhat="no ranking run has been recorded for this project."
      emptyProduces="a ranking run (POST /judge/rank/voices), which writes every candidate under one run_id"
      legend={
        <Legend
          items={[
            { label: "winner_score", color: SHADE.lit, shape: "dot" },
            { label: "runner_up_score", color: SHADE.dim, shape: "dot" },
            { label: "margin", color: SHADE.lit, shape: "line" },
          ]}
        />
      }
      footnote="Newest run first. runner_up_score is the API's argMax over candidate_rank — the lowest-ranked candidate, which is second place only in a two-candidate run."
    >
      <Plot>
        <DumbbellList
          data={rows.map(toDatum)}
          domain={[0, 1]}
          labelWidth="11rem"
        />
      </Plot>
    </PanelCard>
  );
}
