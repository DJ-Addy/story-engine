"use client";

// GET .../analytics/voice-leaderboard (also panel 1 of /dashboard).
//
// The question is "of every voice we have ever tried for this character, which
// fits best and how consistently" — so the chart is grouped by character, not
// by voice: comparing MARA's candidates against DOYLE's would be comparing
// nothing. Within a character the bar is the average score across every
// judgement, and the grey tick is the best single score, which is the spread
// that tells a consistent voice from a lucky one.
//
// The bar wears a *status* colour rather than a series colour because the value
// means good or bad: the bands are the judge's own thresholds (strong >= 0.75,
// adequate >= 0.55 — backend/app/judge/voices.py). Every band therefore ships
// with its glyph and its word beside the bar, never colour alone.

import {
  byDesc,
  num,
  numOr,
  text,
  time,
  type AnalyticsPanel,
  type AnalyticsRow,
} from "@/lib/analyticsApi";
import { BandChip, BarList, Legend, Plot, type BarDatum } from "@/components/dashboard/charts";
import { PanelCard } from "@/components/dashboard/PanelCard";
import {
  ago,
  band,
  count,
  INK,
  percent,
  score100,
  STATUS,
} from "@/components/dashboard/theme";

/** Voices shown per character before the rest is left to the table view. */
const PER_CHARACTER = 4;

/** The reference tick's colour: de-emphasis grey, deliberately not an identity. */
const BEST_TICK = INK.secondary;

interface Group {
  character: string;
  rows: AnalyticsRow[];
  hidden: number;
  judgements: number;
}

function group(rows: AnalyticsRow[]): Group[] {
  const byCharacter = new Map<string, AnalyticsRow[]>();
  for (const row of rows) {
    const name = text(row, "character_name") || "—";
    const bucket = byCharacter.get(name);
    if (bucket) bucket.push(row);
    else byCharacter.set(name, [row]);
  }
  return [...byCharacter.entries()]
    .map(([character, all]) => {
      const ranked = byDesc(all, "avg_score");
      return {
        character,
        rows: ranked.slice(0, PER_CHARACTER),
        hidden: Math.max(0, ranked.length - PER_CHARACTER),
        judgements: ranked.reduce((a, r) => a + (num(r, "judgements") ?? 0), 0),
      };
    })
    .sort((a, b) => b.judgements - a.judgements || a.character.localeCompare(b.character));
}

function toDatum(row: AnalyticsRow, character: string, rank: number): BarDatum {
  const avg = numOr(row, "avg_score", 0);
  const best = num(row, "best_score");
  const latest = num(row, "latest_score");
  const b = band(avg);
  const voice = text(row, "voice_name") || text(row, "voice_id") || "—";
  return {
    key: `${character}:${text(row, "voice_id") || voice}:${rank}`,
    label: (
      <span className="flex items-baseline gap-2">
        <span className="font-mono text-[10px] text-zinc-600">{rank}</span>
        <span className="truncate">{voice}</span>
      </span>
    ),
    sub: `${count(num(row, "judgements") ?? 0)} judged · ${count(
      num(row, "lines_judged") ?? 0,
    )} lines`,
    value: avg,
    color: b.color,
    valueLabel: score100(avg),
    marker: best === null ? undefined : { value: best, color: BEST_TICK },
    trailing: <BandChip color={b.color} glyph={b.glyph} word={b.name} />,
    tip: {
      title: `${character} · ${voice}`,
      rows: [
        { label: "average score", value: score100(avg), color: b.color },
        ...(best !== null
          ? [{ label: "best score", value: score100(best), color: BEST_TICK }]
          : []),
        ...(latest !== null ? [{ label: "latest score", value: score100(latest) }] : []),
        {
          label: "speaks share",
          value: percent(numOr(row, "speaks_share", 0)),
        },
        {
          label: "warnings / errors",
          value: `${count(num(row, "warnings") ?? 0)} / ${count(num(row, "errors") ?? 0)}`,
        },
        { label: "last judged", value: ago(time(row, "last_judged_at")) },
      ],
    },
  };
}

export function VoiceLeaderboardPanel({
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
  const groups = panel ? group(panel.rows) : [];
  const hiddenTotal = groups.reduce((a, g) => a + g.hidden, 0);

  return (
    <PanelCard
      panelKey="voiceLeaderboard"
      panel={panel}
      title="Voice leaderboard"
      loading={loading}
      refreshing={refreshing}
      className={className}
      emptyWhat="no voice has been judged for any character in this project."
      emptyProduces="a voice-fit judge run (POST /judge/voices) in the Casting Studio"
      legend={
        <Legend
          items={[
            { label: "▲ strong 75+", color: STATUS.good, shape: "bar" },
            { label: "● adequate 55+", color: STATUS.warning, shape: "bar" },
            { label: "▼ poor", color: STATUS.critical, shape: "bar" },
            { label: "best single score", color: BEST_TICK, shape: "dot" },
          ]}
        />
      }
      footnote={
        hiddenTotal > 0
          ? `Bar is the average over every judgement; top ${PER_CHARACTER} voices per character shown, ${hiddenTotal} more in the table view.`
          : "Bar is the average over every judgement. Scores are shown 0-100."
      }
    >
      <Plot className="space-y-5">
        {groups.map((g) => (
          <div key={g.character}>
            <div className="mb-2 flex items-baseline justify-between gap-3 border-b border-[var(--hairline)] pb-1.5">
              <h3 className="truncate font-mono text-[11px] uppercase tracking-[0.14em] text-amber-300/80">
                {g.character}
              </h3>
              <span className="shrink-0 font-mono text-[10px] tabular-nums text-zinc-600">
                {count(g.judgements)} judgements
              </span>
            </div>
            <BarList
              max={1}
              labelWidth="11rem"
              data={g.rows.map((row, i) => toDatum(row, g.character, i + 1))}
            />
          </div>
        ))}
      </Plot>
    </PanelCard>
  );
}
