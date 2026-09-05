"use client";

// The headline row.
//
// Every figure here is a sum or a count over the rows the panels below already
// show — nothing is fetched separately and nothing is estimated. That has one
// consequence worth stating on screen, and the caption does: these are totals
// over the rows the query *returned*, and the queries carry a LIMIT, so at the
// row cap they are totals of what is shown rather than of all of history.
//
// A tile whose source panel is unavailable reads "—", never 0. A cluster that
// cannot be reached has not told us there were no renders.

import {
  panelState,
  selectPanel,
  sumOf,
  type AnalyticsDashboard,
} from "@/lib/analyticsApi";
import { HeroFigure, StatTile } from "@/components/dashboard/charts";
import { count, duration, money, STATUS } from "@/components/dashboard/theme";

const UNAVAILABLE = "—";

export function SummaryBar({
  dashboard,
  limit,
}: {
  dashboard: AnalyticsDashboard | null;
  limit: number;
}) {
  const spend = selectPanel(dashboard, "spendByProvider");
  const leaderboard = selectPanel(dashboard, "voiceLeaderboard");
  const scenes = selectPanel(dashboard, "animaticTrend");
  const pressure = selectPanel(dashboard, "costPressure");

  const spendReady = panelState(spend) !== "unavailable";
  const leaderboardReady = panelState(leaderboard) !== "unavailable";
  const scenesReady = panelState(scenes) !== "unavailable";
  const pressureReady = panelState(pressure) !== "unavailable";

  const spendRows = spend?.rows ?? [];
  const totalCents = sumOf(spendRows, "cost_cents");
  const renders = sumOf(spendRows, "renders");
  const output = sumOf(spendRows, "output_ms");
  const judgements = sumOf(leaderboard?.rows ?? [], "judgements");
  const blocked = sumOf(pressure?.rows ?? [], "blocked");
  const atCap = spendRows.length >= limit;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
      <div className="cast-panel flex flex-col justify-center px-6 py-6 lg:col-span-4">
        <HeroFigure
          label="Spend on this project"
          value={spendReady ? money(totalCents) : UNAVAILABLE}
          note={
            spendReady
              ? `summed from ${count(spendRows.length)} provider rows${atCap ? ` (at the ${count(limit)}-row cap)` : ""}`
              : "the spend panel is unavailable — no figure is being estimated"
          }
        />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:col-span-8 xl:grid-cols-5">
        <StatTile
          label="Renders"
          value={spendReady ? count(renders) : UNAVAILABLE}
          note={spendReady ? "audio + video, all providers" : "panel unavailable"}
        />
        <StatTile
          label="Media produced"
          value={spendReady ? duration(output) : UNAVAILABLE}
          note={spendReady ? "sum of output_ms" : "panel unavailable"}
        />
        <StatTile
          label="Voice judgements"
          value={leaderboardReady ? count(judgements) : UNAVAILABLE}
          note={
            leaderboardReady
              ? `across ${count(leaderboard?.rows.length ?? 0)} character/voice pairs`
              : "panel unavailable"
          }
        />
        <StatTile
          label="Scenes judged"
          value={scenesReady ? count(scenes?.rows.length ?? 0) : UNAVAILABLE}
          note={scenesReady ? "by the animatic judge" : "panel unavailable"}
        />
        <StatTile
          label="Refused by the cap"
          value={pressureReady ? count(blocked) : UNAVAILABLE}
          note={pressureReady ? "cost-governor decisions" : "panel unavailable"}
          accent={
            pressureReady && blocked > 0
              ? { color: STATUS.critical, glyph: "▼", word: "blocked" }
              : undefined
          }
        />
      </div>
    </div>
  );
}
