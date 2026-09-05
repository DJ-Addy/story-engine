"use client";

// GET .../analytics/voice-trend — the one panel `/dashboard` does not carry, so
// it is fetched on its own and owns its own loading and failure state.
//
// This is the query that most obviously could not run against the operational
// store: a window function over an ordered event stream, giving a 5-point
// rolling mean per character. A single judgement is noise; the rolling line is
// the evidence that a casting change actually helped.
//
// FORM. Several characters over time is a multi-line chart, and multi-line is
// where categorical palettes go wrong — one hue per character, cycled past the
// token ceiling, converging at the right edge. So it is faceted into small
// multiples instead: one panel per character, each a *single* series, sharing
// one y-axis and one legend. Raw scores are dots in the dim shade, the rolling
// mean is the 2px line in the lit shade — one hue, two shades, because they are
// the same measure at two smoothings rather than two identities.
//
// The x position is judgement order, not clock time: judgements arrive in
// bursts, and a real time axis would collapse each burst into an unreadable
// clump. The actual timestamp is in every point's readout and in the table.

import { useMemo } from "react";
import {
  num,
  numOr,
  text,
  time,
  VOICE_TREND_COLUMNS,
  type AnalyticsPanel,
  type AnalyticsRow,
  type Failure,
} from "@/lib/analyticsApi";
import { Legend, Plot, useTip } from "@/components/dashboard/charts";
import { PanelCard } from "@/components/dashboard/PanelCard";
import {
  ago,
  count,
  INK,
  score100,
  SHADE,
  STATUS,
  SURFACE,
} from "@/components/dashboard/theme";

/** Characters given their own facet before the rest is left to the table. */
const MAX_FACETS = 6;

const W = 260;
const H = 78;
const PAD_X = 10;
const PAD_Y = 10;

interface Point {
  x: number;
  y: number;
  yAvg: number;
  score: number;
  rolling: number;
  voice: string;
  label: string;
  at: number | null;
}

interface Facet {
  character: string;
  points: Point[];
}

function facets(rows: AnalyticsRow[]): { facets: Facet[]; hidden: number } {
  const byCharacter = new Map<string, AnalyticsRow[]>();
  for (const row of rows) {
    const name = text(row, "character_name") || "—";
    const bucket = byCharacter.get(name);
    if (bucket) bucket.push(row);
    else byCharacter.set(name, [row]);
  }
  const ordered = [...byCharacter.entries()].sort(
    (a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]),
  );
  const shown = ordered.slice(0, MAX_FACETS).map(([character, all]) => {
    const span = Math.max(1, all.length - 1);
    return {
      character,
      points: all.map((row, i) => {
        const score = numOr(row, "score", 0);
        const rolling = numOr(row, "rolling_avg", score);
        return {
          x: PAD_X + (i / span) * (W - PAD_X * 2),
          y: PAD_Y + (1 - Math.max(0, Math.min(1, score))) * (H - PAD_Y * 2),
          yAvg: PAD_Y + (1 - Math.max(0, Math.min(1, rolling))) * (H - PAD_Y * 2),
          score,
          rolling,
          voice: text(row, "voice_name") || "—",
          label: text(row, "candidate_label") || text(row, "mode") || "",
          at: time(row, "event_time"),
        };
      }),
    };
  });
  return { facets: shown, hidden: Math.max(0, ordered.length - MAX_FACETS) };
}

/** The y position of the judge's "strong" threshold, drawn as one recessive
 * hairline so a reader can see which side of it a character sits on. */
const STRONG_Y = PAD_Y + (1 - 0.75) * (H - PAD_Y * 2);

function FacetChart({ facet }: { facet: Facet }) {
  const tip = useTip();
  const line = facet.points.map((p) => `${p.x.toFixed(1)},${p.yAvg.toFixed(1)}`).join(" ");
  const last = facet.points[facet.points.length - 1];

  return (
    <div className="cast-card px-3 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="truncate font-mono text-[11px] uppercase tracking-[0.12em] text-amber-300/80">
          {facet.character}
        </h3>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-zinc-500">
          {count(facet.points.length)} judged
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="mt-2 w-full"
        role="img"
        aria-label={`${facet.character}: ${facet.points.length} voice-fit scores in judgement order, with a 5-point rolling mean. Latest rolling mean ${last ? score100(last.rolling) : "—"}.`}
      >
        {/* One recessive hairline at the judge's "strong" threshold. */}
        <line
          x1={PAD_X}
          x2={W - PAD_X}
          y1={STRONG_Y}
          y2={STRONG_Y}
          stroke={STATUS.good}
          strokeOpacity={0.28}
          strokeWidth={1}
        />
        <text
          x={W - PAD_X}
          y={STRONG_Y - 3}
          textAnchor="end"
          fill={INK.muted}
          style={{ fontSize: 8 }}
        >
          75
        </text>

        {facet.points.length > 1 && (
          <polyline
            points={line}
            fill="none"
            stroke={SHADE.lit}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )}

        {facet.points.map((p, i) => (
          <g
            key={i}
            {...tip({
              title: `${facet.character} · ${p.voice}`,
              rows: [
                { label: "score", value: score100(p.score), color: SHADE.dim },
                { label: "rolling mean", value: score100(p.rolling), color: SHADE.lit },
                ...(p.label ? [{ label: "candidate", value: p.label }] : []),
                { label: "judged", value: ago(p.at) },
              ],
            })}
            className="cursor-default outline-none"
          >
            {/* A hit target larger than the mark, per the interaction rules. */}
            <circle cx={p.x} cy={p.y} r={10} fill="transparent" />
            <circle
              cx={p.x}
              cy={p.y}
              r={3}
              fill={SHADE.dim}
              stroke={SURFACE}
              strokeWidth={2}
            />
          </g>
        ))}

        {last && (
          <text
            x={Math.min(W - PAD_X, last.x + 6)}
            y={Math.max(10, last.yAvg - 5)}
            textAnchor="end"
            fill={INK.secondary}
            style={{ fontSize: 10, fontVariantNumeric: "tabular-nums" }}
          >
            {score100(last.rolling)}
          </text>
        )}
      </svg>
      <p className="mt-1 text-[10px] text-zinc-600">oldest → newest</p>
    </div>
  );
}

export function VoiceTrendPanel({
  panel,
  loading,
  refreshing,
  failure,
  className,
}: {
  panel: AnalyticsPanel | null;
  loading: boolean;
  refreshing: boolean;
  failure: Failure | null;
  className?: string;
}) {
  const { facets: shown, hidden } = useMemo(
    () => (panel ? facets(panel.rows) : { facets: [], hidden: 0 }),
    [panel],
  );
  const judgements = panel?.rows.length ?? 0;
  const withRolling = panel
    ? panel.rows.filter((r) => num(r, "rolling_avg") !== null).length
    : 0;

  return (
    <PanelCard
      panel={panel}
      failure={failure}
      title="Casting score over time"
      endpoint="voice-trend"
      columnOrder={VOICE_TREND_COLUMNS}
      question="How has casting scored over time?"
      loading={loading}
      refreshing={refreshing}
      className={className}
      emptyWhat="no voice-fit judgement has been written for this project yet."
      emptyProduces="a voice-fit judge run (POST /judge/voices) — each run appends one row per candidate"
      legend={
        <Legend
          items={[
            { label: "score", color: SHADE.dim, shape: "dot" },
            { label: "5-point rolling mean", color: SHADE.lit, shape: "line" },
            { label: "strong threshold (75)", color: STATUS.good, shape: "line" },
          ]}
        />
      }
      footnote={
        hidden > 0
          ? `${count(judgements)} judgements; top ${MAX_FACETS} characters faceted, ${hidden} more in the table view.`
          : `${count(judgements)} judgements, ${count(withRolling)} carrying the window's rolling mean.`
      }
    >
      <Plot>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {shown.map((f) => (
            <FacetChart key={f.character} facet={f} />
          ))}
        </div>
      </Plot>
    </PanelCard>
  );
}
