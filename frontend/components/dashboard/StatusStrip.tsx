"use client";

// GET .../analytics/status — the honesty strip.
//
// This is the one place that can tell a judge *why* the panels below look the
// way they do, so it never summarises: it shows the host, the transport, the
// database, which of the expected tables the cluster actually has, and the
// write buffer's own counters. `tables_present` comes from system.tables, so a
// green strip is proof the MCP server answered a real query — not merely that
// credentials are set.
//
// Three states, each with its own colour, glyph and word:
//   live            configured and reachable
//   unreachable     configured, but the cluster did not answer
//   not configured  CLICKHOUSE_HOST is unset; there is no cluster to reach

import type { AnalyticsStatus, Failure } from "@/lib/analyticsApi";
import { count, STATUS } from "@/components/dashboard/theme";
import { ErrorNotice } from "@/components/dashboard/states";

type Health = "live" | "unreachable" | "unconfigured";

const HEALTH: Record<Health, { color: string; glyph: string; word: string }> = {
  live: { color: STATUS.good, glyph: "▲", word: "ClickHouse live" },
  unreachable: { color: STATUS.critical, glyph: "▼", word: "Unreachable" },
  unconfigured: { color: STATUS.warning, glyph: "●", word: "Not configured" },
};

export function healthOf(status: AnalyticsStatus | null): Health | null {
  if (!status) return null;
  if (!status.configured) return "unconfigured";
  return status.reachable ? "live" : "unreachable";
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="font-mono text-[10px] uppercase tracking-wider text-zinc-600">
        {label}
      </p>
      <p className="truncate font-mono text-[11px] text-zinc-300" title={value}>
        {value || "—"}
      </p>
    </div>
  );
}

export function StatusStrip({
  status,
  failure,
  loading,
}: {
  status: AnalyticsStatus | null;
  failure: Failure | null;
  loading: boolean;
}) {
  if (failure) {
    return <ErrorNotice compact failure={failure} />;
  }
  if (loading || !status) {
    return (
      <div className="cast-panel px-5 py-4">
        <div className="cast-shimmer h-4 w-56 rounded" aria-hidden />
      </div>
    );
  }

  const health = HEALTH[healthOf(status) as Health];
  const missing = status.tables_expected.filter(
    (t) => !status.tables_present.includes(t),
  );
  const dropped = Number(status.buffer.dropped ?? 0);
  const failed = Number(status.buffer.failed ?? 0);
  const written = Number(status.buffer.written ?? 0);
  const buffered = Number(status.buffer.buffered ?? 0);
  const recorderOn = status.buffer.enabled === true;

  return (
    <section className="cast-panel px-5 py-4" aria-label="ClickHouse status">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-center gap-2">
          <span aria-hidden className="text-sm leading-none" style={{ color: health.color }}>
            {health.glyph}
          </span>
          <span
            className="font-mono text-[11px] uppercase tracking-[0.16em]"
            style={{ color: health.color }}
          >
            {health.word}
          </span>
        </div>

        <div className="grid flex-1 grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
          <Fact label="host" value={status.host} />
          <Fact label="database" value={status.database} />
          <Fact label="transport" value={status.transport} />
          <Fact
            label="tables"
            value={`${status.tables_present.length}/${status.tables_expected.length} present`}
          />
          <Fact
            label="write buffer"
            value={
              recorderOn
                ? `${count(written)} written · ${count(buffered)} pending`
                : "recorder off"
            }
          />
        </div>
      </div>

      {status.detail && (
        <p className="mt-3 break-words rounded border border-[var(--hairline)] bg-black/30 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-zinc-400">
          {status.detail}
        </p>
      )}

      {(missing.length > 0 || dropped > 0 || failed > 0) && (
        <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px]">
          {missing.length > 0 && (
            <li className="flex items-center gap-1.5">
              <span aria-hidden style={{ color: STATUS.warning }}>
                ●
              </span>
              <span className="text-zinc-400">
                missing tables:{" "}
                <span className="font-mono text-zinc-300">{missing.join(", ")}</span>{" "}
                — the migration has not run against this cluster
              </span>
            </li>
          )}
          {dropped > 0 && (
            <li className="flex items-center gap-1.5">
              <span aria-hidden style={{ color: STATUS.warning }}>
                ●
              </span>
              <span className="text-zinc-400">
                {count(dropped)} events dropped — the panels below are missing those rows
              </span>
            </li>
          )}
          {failed > 0 && (
            <li className="flex items-center gap-1.5">
              <span aria-hidden style={{ color: STATUS.critical }}>
                ▼
              </span>
              <span className="text-zinc-400">{count(failed)} writes failed</span>
            </li>
          )}
        </ul>
      )}
    </section>
  );
}
