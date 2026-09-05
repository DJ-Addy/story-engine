"use client";

// Hand-rolled chart primitives for the analytics dashboard.
//
// No charting library, for the same reason `AxisDiagram.tsx` and the timeline
// grid are hand-rolled: every form here is a bar, a paired track, or a grid of
// cells, and a library would cost a dependency plus a fight with its default
// look to reach the app's.
//
// The marks follow one fixed spec throughout, so the panels read as one system:
//
//   bars        <= 12px thick, 4px rounded at the data end, square at the
//               baseline, grown from a single baseline
//   markers     >= 8px, carrying a 2px ring in the surface colour so they stay
//               legible where they overlap a bar or another marker
//   separators  a 2px gap in the surface colour between touching marks —
//               never a stroke drawn around a mark
//   grid/axes   solid hairlines one step off the surface, recessive
//   text        always an ink token, never the series colour; identity comes
//               from a coloured mark beside the text
//
// Every mark is hoverable *and* focusable and shows the same readout either
// way — but the tooltip only ever repeats what the panel's table view already
// shows, so no value is reachable by hover alone.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { alpha, INK, RAMP, SURFACE } from "@/components/dashboard/theme";

// --------------------------------------------------------------------------- //
// Tooltip layer
// --------------------------------------------------------------------------- //

export interface TipRow {
  label: string;
  value: string;
  /** Draws a short line-key in the series colour beside the row. */
  color?: string;
}

export interface TipContent {
  title: string;
  rows: TipRow[];
}

type Placer = (el: Element | null, content: TipContent | null) => void;

const TipContext = createContext<Placer | null>(null);

interface Placed {
  left: number;
  top: number;
  content: TipContent;
}

/**
 * The positioned container every chart draws inside. Owns one tooltip for all
 * of its marks, anchored to the hovered or focused element rather than to the
 * pointer, so keyboard and mouse land in the same place.
 */
export function Plot({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const [tip, setTip] = useState<Placed | null>(null);

  const place = useCallback<Placer>((el, content) => {
    if (!el || !content || !host.current) {
      setTip(null);
      return;
    }
    const hb = host.current.getBoundingClientRect();
    const b = el.getBoundingClientRect();
    setTip({
      left: b.left - hb.left + b.width / 2,
      top: b.top - hb.top,
      content,
    });
  }, []);

  return (
    <TipContext.Provider value={place}>
      <div ref={host} className={`relative ${className}`}>
        {children}
        {tip && (
          <div
            role="tooltip"
            className="pointer-events-none absolute z-30 -translate-x-1/2 -translate-y-full pb-2"
            style={{ left: tip.left, top: tip.top }}
          >
            <div className="min-w-[10rem] max-w-[18rem] rounded-lg border border-[var(--hairline-strong)] bg-zinc-950/95 px-3 py-2 shadow-[0_20px_46px_-22px_rgba(0,0,0,0.9)] backdrop-blur">
              <p className="truncate font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                {tip.content.title}
              </p>
              <ul className="mt-1.5 space-y-1">
                {tip.content.rows.map((r) => (
                  <li key={r.label} className="flex items-baseline gap-2">
                    {r.color && (
                      <span
                        aria-hidden
                        className="mt-1 h-0.5 w-3 shrink-0 rounded-full"
                        style={{ background: r.color }}
                      />
                    )}
                    <span className="font-mono text-xs font-semibold tabular-nums text-zinc-100">
                      {r.value}
                    </span>
                    <span className="truncate text-[11px] text-zinc-500">
                      {r.label}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>
    </TipContext.Provider>
  );
}

/**
 * One hook call per chart component, returning a factory. Called inside a
 * `.map()` a hook would break the rules of hooks; a factory does not.
 */
export function useTip() {
  const place = useContext(TipContext);
  return useCallback(
    (content: TipContent) => ({
      tabIndex: 0,
      // A focusable mark needs a name: the same readout the tooltip shows, so a
      // screen reader and a pointer get the same values.
      role: "img",
      "aria-label": `${content.title}: ${content.rows
        .map((r) => `${r.label} ${r.value}`)
        .join(", ")}`,
      onPointerEnter: (e: { currentTarget: Element }) =>
        place?.(e.currentTarget, content),
      onPointerLeave: () => place?.(null, null),
      onFocus: (e: { currentTarget: Element }) => place?.(e.currentTarget, content),
      onBlur: () => place?.(null, null),
    }),
    [place],
  );
}

// --------------------------------------------------------------------------- //
// Legend
// --------------------------------------------------------------------------- //

export interface LegendItem {
  label: string;
  color: string;
  /** `bar` for fills, `dot` for markers, `line` for connectors. Mirrors the mark. */
  shape?: "bar" | "dot" | "line";
}

/** Always present where a chart carries two or more marks. */
export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
      {items.map((it) => (
        <li key={it.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={
              it.shape === "dot"
                ? "h-2 w-2 rounded-full"
                : it.shape === "line"
                  ? "h-0.5 w-3.5 rounded-full"
                  : "h-2 w-3.5 rounded-[2px]"
            }
            style={{
              background: it.color,
              boxShadow: it.shape === "dot" ? `0 0 0 2px ${SURFACE}` : undefined,
            }}
          />
          <span className="text-[11px] text-zinc-500">{it.label}</span>
        </li>
      ))}
    </ul>
  );
}

/** The scale key a sequential fill needs to be readable. */
export function RampLegend({
  low,
  high,
  caption,
}: {
  low: string;
  high: string;
  caption?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="font-mono text-[10px] tabular-nums text-zinc-500">{low}</span>
      <span aria-hidden className="flex h-2 w-24 overflow-hidden rounded-[2px]">
        {RAMP.map((c) => (
          <span key={c} className="h-full flex-1" style={{ background: c }} />
        ))}
      </span>
      <span className="font-mono text-[10px] tabular-nums text-zinc-500">{high}</span>
      {caption && <span className="text-[11px] text-zinc-500">{caption}</span>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Shared geometry
// --------------------------------------------------------------------------- //

const pct = (v: number, max: number): number =>
  max <= 0 ? 0 : Math.max(0, Math.min(100, (v / max) * 100));

/** A bar's fill. Rounded only at the data end; square against the baseline. */
const barStyle = (width: number, color: string): CSSProperties => ({
  width: `${width}%`,
  background: color,
  borderRadius: "0 4px 4px 0",
});

/** The 2px surface ring that keeps an overlapping marker legible. */
const ringed = (color: string): CSSProperties => ({
  background: color,
  boxShadow: `0 0 0 2px ${SURFACE}`,
});

// --------------------------------------------------------------------------- //
// Bar list — magnitude, one measure, one colour
// --------------------------------------------------------------------------- //

export interface BarDatum {
  key: string;
  /** The category. Rendered in an ink token, never in the mark's colour. */
  label: ReactNode;
  /** A second, quieter line under the label. */
  sub?: ReactNode;
  value: number;
  color: string;
  /** The value, direct-labelled at the bar's tip. */
  valueLabel: string;
  /** A second mark on the same track — the *other* end of a pair. */
  marker?: { value: number; color: string };
  /** Trailing content: a band chip, a provider list. */
  trailing?: ReactNode;
  tip: TipContent;
}

export function BarList({
  data,
  max,
  labelWidth = "12rem",
}: {
  data: BarDatum[];
  /** The axis maximum. Passed in so sibling charts can share a scale. */
  max: number;
  labelWidth?: string;
}) {
  const tip = useTip();
  return (
    <ul className="space-y-2.5">
      {data.map((d) => (
        <li key={d.key} className="grid grid-cols-1 items-center gap-x-3 gap-y-1 sm:grid-cols-[var(--lw)_1fr] sm:gap-x-4" style={{ ["--lw"]: labelWidth } as CSSProperties}>
          <div className="min-w-0">
            <p className="truncate text-[13px] leading-tight text-zinc-200">{d.label}</p>
            {d.sub && (
              <p className="truncate font-mono text-[10px] leading-tight text-zinc-500">
                {d.sub}
              </p>
            )}
          </div>
          <div className="flex min-w-0 items-center gap-3">
            <div
              {...tip(d.tip)}
              className="group relative h-6 min-w-0 flex-1 cursor-default rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70"
            >
              {/* Track: the baseline the bar grows from. */}
              <span
                aria-hidden
                className="absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-[2px] rounded-r-none"
                style={{ background: "rgba(255,255,255,0.05)" }}
              />
              <span
                aria-hidden
                className="absolute left-0 top-1/2 h-2.5 -translate-y-1/2 transition-[width] duration-500 ease-out group-hover:brightness-110"
                style={barStyle(pct(d.value, max), d.color)}
              />
              {d.marker !== undefined && (
                <span
                  aria-hidden
                  className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full"
                  style={{
                    left: `${pct(d.marker.value, max)}%`,
                    ...ringed(d.marker.color),
                  }}
                />
              )}
            </div>
            <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-zinc-300">
              {d.valueLabel}
            </span>
            {d.trailing && <div className="shrink-0">{d.trailing}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------- //
// Dumbbell — before → after for one item, one hue in two shades
// --------------------------------------------------------------------------- //

export interface DumbbellDatum {
  key: string;
  label: ReactNode;
  sub?: ReactNode;
  from: number;
  to: number;
  /** The connector carries the direction of travel, so it ships with a glyph. */
  connector: { color: string; glyph: string; word: string } | null;
  fromColor: string;
  toColor: string;
  valueLabel: string;
  tip: TipContent;
}

export function DumbbellList({
  data,
  domain,
  labelWidth = "8rem",
}: {
  data: DumbbellDatum[];
  /** [min, max] of the shared axis — a dumbbell is meaningless without one. */
  domain: [number, number];
  labelWidth?: string;
}) {
  const tip = useTip();
  const [lo, hi] = domain;
  const span = hi - lo || 1;
  const at = (v: number) => ((Math.max(lo, Math.min(hi, v)) - lo) / span) * 100;

  return (
    <ul className="space-y-2.5">
      {data.map((d) => {
        const a = at(d.from);
        const b = at(d.to);
        return (
          <li
            key={d.key}
            className="grid grid-cols-1 items-center gap-x-3 gap-y-1 sm:grid-cols-[var(--lw)_1fr] sm:gap-x-4"
            style={{ ["--lw"]: labelWidth } as CSSProperties}
          >
            <div className="min-w-0">
              <p className="truncate text-[13px] leading-tight text-zinc-200">
                {d.label}
              </p>
              {d.sub && (
                <p className="truncate font-mono text-[10px] leading-tight text-zinc-500">
                  {d.sub}
                </p>
              )}
            </div>
            <div className="flex min-w-0 items-center gap-3">
              <div
                {...tip(d.tip)}
                className="relative h-6 min-w-0 flex-1 cursor-default rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70"
              >
                <span
                  aria-hidden
                  className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2"
                  style={{ background: INK.grid }}
                />
                <span
                  aria-hidden
                  className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full"
                  style={{
                    left: `${Math.min(a, b)}%`,
                    width: `${Math.abs(b - a)}%`,
                    background: d.connector?.color ?? INK.axis,
                    opacity: d.connector ? 0.85 : 0.5,
                  }}
                />
                <span
                  aria-hidden
                  className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full"
                  style={{ left: `${a}%`, ...ringed(d.fromColor) }}
                />
                <span
                  aria-hidden
                  className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full"
                  style={{ left: `${b}%`, ...ringed(d.toColor) }}
                />
              </div>
              <span className="flex w-20 shrink-0 items-center justify-end gap-1 font-mono text-xs tabular-nums">
                {d.connector && (
                  <span aria-hidden style={{ color: d.connector.color }}>
                    {d.connector.glyph}
                  </span>
                )}
                <span className="text-zinc-300">{d.valueLabel}</span>
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// --------------------------------------------------------------------------- //
// Stacked bar — part to whole, 2px surface gaps between segments
// --------------------------------------------------------------------------- //

export interface StackSegment {
  label: string;
  value: number;
  color: string;
  /** Set for a segment whose colour is a reserved status token. */
  glyph?: string;
}

export interface StackDatum {
  key: string;
  label: ReactNode;
  sub?: ReactNode;
  segments: StackSegment[];
  total: number;
  valueLabel: string;
  trailing?: ReactNode;
  tip: TipContent;
}

export function StackedBarList({
  data,
  max,
  labelWidth = "12rem",
}: {
  data: StackDatum[];
  max: number;
  labelWidth?: string;
}) {
  const tip = useTip();
  return (
    <ul className="space-y-2.5">
      {data.map((d) => (
        <li
          key={d.key}
          className="grid grid-cols-1 items-center gap-x-3 gap-y-1 sm:grid-cols-[var(--lw)_1fr] sm:gap-x-4"
          style={{ ["--lw"]: labelWidth } as CSSProperties}
        >
          <div className="min-w-0">
            <p className="truncate text-[13px] leading-tight text-zinc-200">{d.label}</p>
            {d.sub && (
              <p className="truncate font-mono text-[10px] leading-tight text-zinc-500">
                {d.sub}
              </p>
            )}
          </div>
          <div className="flex min-w-0 items-center gap-3">
            <div
              {...tip(d.tip)}
              className="relative h-6 min-w-0 flex-1 cursor-default rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70"
            >
              <span
                aria-hidden
                className="absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-[2px] rounded-r-none"
                style={{ background: "rgba(255,255,255,0.05)" }}
              />
              <span
                aria-hidden
                className="absolute left-0 top-1/2 flex h-2.5 -translate-y-1/2 overflow-hidden"
                style={{ width: `${pct(d.total, max)}%`, borderRadius: "0 4px 4px 0" }}
              >
                {d.segments
                  .filter((s) => s.value > 0)
                  .map((s, i) => (
                    <span
                      key={s.label}
                      className="h-full"
                      style={{
                        flexGrow: s.value,
                        flexBasis: 0,
                        background: s.color,
                        // The separator is a gap in the surface colour, not a
                        // border drawn around the segment.
                        marginLeft: i === 0 ? 0 : 2,
                      }}
                    />
                  ))}
              </span>
            </div>
            <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-zinc-300">
              {d.valueLabel}
            </span>
            {d.trailing && <div className="shrink-0">{d.trailing}</div>}
          </div>
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------- //
// Meter — one ratio against a limit
// --------------------------------------------------------------------------- //

/** The unfilled track is a dimmer step of the fill's own ramp, so the state
 * reads across the whole bar rather than only where it is filled. */
export function Meter({
  value,
  limit,
  color,
  caption,
  valueLabel,
  limitLabel,
}: {
  value: number;
  limit: number;
  color: string;
  caption: string;
  valueLabel: string;
  limitLabel: string;
}) {
  const filled = pct(value, limit);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[11px] text-zinc-500">{caption}</span>
        <span className="font-mono text-xs tabular-nums text-zinc-300">
          {valueLabel}
          <span className="text-zinc-600"> / {limitLabel}</span>
        </span>
      </div>
      <div
        className="mt-1.5 h-2.5 w-full overflow-hidden rounded-[2px]"
        style={{ background: alpha(color, 0.14) }}
        role="meter"
        aria-valuenow={Math.round(filled)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={caption}
      >
        <div
          className="h-full transition-[width] duration-500 ease-out"
          style={{ width: `${filled}%`, background: color, borderRadius: "0 4px 4px 0" }}
        />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Heat grid — a magnitude across two dimensions, one sequential hue
// --------------------------------------------------------------------------- //

export interface HeatRow {
  key: string;
  label: string;
  cells: { column: string; value: number | null; display: string }[];
}

/**
 * Cells are separated by a 2px gap in the surface colour. The value is written
 * inside each cell (two or three characters always fit at this cell size), and
 * the label colour is chosen by the fill's own step so it clears contrast on
 * both the dimmest and brightest end.
 */
export function HeatGrid({
  rows,
  columns,
  colorFor,
}: {
  rows: HeatRow[];
  columns: string[];
  colorFor: (value: number) => string;
}) {
  const tip = useTip();
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[22rem]">
        <div
          className="grid gap-0.5"
          style={{ gridTemplateColumns: `5.5rem repeat(${columns.length}, minmax(3rem, 1fr))` }}
        >
          <span aria-hidden />
          {columns.map((c) => (
            <span
              key={c}
              className="truncate pb-1 text-center font-mono text-[10px] uppercase tracking-wider text-zinc-500"
            >
              {c}
            </span>
          ))}
          {rows.map((r) => (
            <Fragmentish key={r.key}>
              <span className="flex items-center truncate pr-2 text-[12px] text-zinc-400">
                {r.label}
              </span>
              {r.cells.map((cell) => {
                const known = cell.value !== null;
                const bright = known && cell.value !== null && cell.value >= 0.62;
                return (
                  <span
                    key={cell.column}
                    {...tip({
                      title: `${r.label} · ${cell.column}`,
                      rows: [{ label: cell.column, value: cell.display }],
                    })}
                    className="flex h-9 cursor-default items-center justify-center rounded-[3px] font-mono text-[11px] tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-amber-400/70"
                    style={{
                      background: known ? colorFor(cell.value as number) : "rgba(255,255,255,0.03)",
                      color: known ? (bright ? "#0a0a0b" : INK.primary) : INK.muted,
                    }}
                  >
                    {cell.display}
                  </span>
                );
              })}
            </Fragmentish>
          ))}
        </div>
      </div>
    </div>
  );
}

/** A keyed fragment: the grid needs each row's cells as direct children. */
function Fragmentish({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

// --------------------------------------------------------------------------- //
// Stat tiles
// --------------------------------------------------------------------------- //

/**
 * The hero figure — exactly one per view, in the app's own sans, with
 * proportional figures (tabular-nums makes a large number look loose).
 */
export function HeroFigure({
  value,
  label,
  note,
}: {
  value: string;
  label: string;
  note?: string;
}) {
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-500">
        {label}
      </p>
      <p className="mt-1 text-5xl font-semibold leading-none text-zinc-100 md:text-6xl">
        {value}
      </p>
      {note && <p className="mt-2 text-[11px] text-zinc-500">{note}</p>}
    </div>
  );
}

export function StatTile({
  label,
  value,
  note,
  accent,
}: {
  label: string;
  value: string;
  note?: string;
  /** A status colour, only where the number *means* good or bad. */
  accent?: { color: string; glyph: string; word: string };
}) {
  return (
    <div className="cast-card px-4 py-3">
      <p className="truncate font-mono text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </p>
      <p className="mt-1 flex items-baseline gap-1.5 text-2xl font-semibold leading-none text-zinc-100">
        {accent && (
          <span aria-hidden className="text-sm" style={{ color: accent.color }}>
            {accent.glyph}
          </span>
        )}
        {value}
      </p>
      {(note || accent) && (
        <p className="mt-1.5 truncate text-[11px] text-zinc-500">
          {accent ? `${accent.word}${note ? ` · ${note}` : ""}` : note}
        </p>
      )}
    </div>
  );
}

/** A status as colour + glyph + word. Status is never colour alone. */
export function BandChip({
  color,
  glyph,
  word,
  value,
}: {
  color: string;
  glyph: string;
  word: string;
  value?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1 rounded border border-[var(--hairline)] bg-white/[0.03] px-1.5 py-0.5">
      <span aria-hidden className="text-[10px] leading-none" style={{ color }}>
        {glyph}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-400">
        {word}
      </span>
      {value && (
        <span className="font-mono text-[10px] tabular-nums text-zinc-300">{value}</span>
      )}
    </span>
  );
}

/** Small helper so a panel can size a shared axis once. */
export function useAxisMax(values: number[]): number {
  return useMemo(() => {
    const top = values.reduce((a, b) => Math.max(a, b), 0);
    return top > 0 ? top : 1;
  }, [values]);
}
