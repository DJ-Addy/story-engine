"use client";

import { useEffect, useRef, useState } from "react";
import { useSceneStore } from "@/lib/store";
import type { ShotSpec } from "@/lib/types";
import {
  AXIS_SIDES,
  CAMERA_HEIGHTS,
  EYELINES,
  MOVEMENTS,
  SHOT_SIZES,
} from "@/lib/types";

const LENS_MIN = 8;
const LENS_MAX = 300;

type ColumnType = "readonly" | "enum" | "number" | "text";

interface Column {
  key: string;
  label: string;
  type: ColumnType;
  options?: readonly string[];
  width: string;
  mono?: boolean;
  getValue(shot: ShotSpec): string;
  apply?(shot: ShotSpec, raw: string): Partial<ShotSpec>;
}

// Column order matters twice over: it is the on-screen order AND the tab order
// (see EDITABLE_KEYS). `intent` sits third so the workspace's narrow shot list
// reads "# · size · what the shot is for" before anything has to be scrolled.
const COLUMNS: Column[] = [
  {
    key: "ordinal",
    label: "#",
    type: "readonly",
    width: "w-10",
    mono: true,
    getValue: (s) => String(s.ordinal),
  },
  {
    key: "size",
    label: "Size",
    type: "enum",
    options: SHOT_SIZES,
    width: "w-16",
    mono: true,
    getValue: (s) => s.size,
    apply: (_s, raw) => ({ size: raw as ShotSpec["size"] }),
  },
  {
    key: "intent",
    label: "Intent",
    type: "text",
    width: "min-w-56",
    getValue: (s) => s.intent,
    apply: (_s, raw) => ({ intent: raw }),
  },
  {
    key: "subjects",
    label: "Subjects",
    type: "text",
    width: "w-32",
    getValue: (s) => s.subjects.join(", "),
    apply: (_s, raw) => ({
      subjects: raw
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean),
    }),
  },
  {
    key: "axis_side",
    label: "Axis",
    type: "enum",
    options: AXIS_SIDES,
    width: "w-16",
    mono: true,
    getValue: (s) => s.axis_side,
    apply: (_s, raw) => ({ axis_side: raw as ShotSpec["axis_side"] }),
  },
  {
    key: "lens_mm",
    label: "Lens",
    type: "number",
    width: "w-16",
    mono: true,
    getValue: (s) => (s.lens_mm === null ? "—" : `${s.lens_mm}mm`),
    apply: (_s, raw) => {
      const n = Number(raw);
      if (raw.trim() === "" || Number.isNaN(n)) return { lens_mm: null };
      return { lens_mm: Math.min(LENS_MAX, Math.max(LENS_MIN, Math.round(n))) };
    },
  },
  {
    key: "camera_height",
    label: "Height",
    type: "enum",
    options: CAMERA_HEIGHTS,
    width: "w-20",
    mono: true,
    getValue: (s) => s.camera_height,
    apply: (_s, raw) => ({ camera_height: raw as ShotSpec["camera_height"] }),
  },
  {
    key: "movement",
    label: "Move",
    type: "enum",
    options: MOVEMENTS,
    width: "w-20",
    mono: true,
    getValue: (s) => s.movement,
    apply: (_s, raw) => ({ movement: raw as ShotSpec["movement"] }),
  },
  {
    key: "eyeline",
    label: "Eyeline",
    type: "enum",
    options: EYELINES,
    width: "w-20",
    mono: true,
    getValue: (s) => s.eyeline,
    apply: (_s, raw) => ({ eyeline: raw as ShotSpec["eyeline"] }),
  },
  {
    key: "covers_lines",
    label: "Lines",
    type: "text",
    width: "w-20",
    mono: true,
    getValue: (s) => s.covers_lines.join(","),
    apply: (_s, raw) => ({
      covers_lines: raw
        .split(",")
        .map((x) => Number(x.trim()))
        .filter((n) => Number.isInteger(n) && n > 0),
    }),
  },
];

const EDITABLE_KEYS = COLUMNS.filter((c) => c.type !== "readonly").map((c) => c.key);

function rawEditValue(col: Column, shot: ShotSpec): string {
  if (col.key === "lens_mm") return shot.lens_mm === null ? "" : String(shot.lens_mm);
  return col.getValue(shot);
}

function CellEditor({
  column,
  shot,
  onCommit,
  onCancel,
}: {
  column: Column;
  shot: ShotSpec;
  onCommit(raw: string, moveRight: boolean): void;
  onCancel(): void;
}) {
  const [draft, setDraft] = useState(() => rawEditValue(column, shot));
  const ref = useRef<HTMLInputElement | HTMLSelectElement | null>(null);

  useEffect(() => {
    ref.current?.focus();
    if (ref.current instanceof HTMLInputElement) ref.current.select();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onCommit(draft, false);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    } else if (e.key === "Tab") {
      e.preventDefault();
      onCommit(draft, true);
    }
    e.stopPropagation();
  };

  const baseClass =
    "w-full bg-zinc-800 border border-sky-600 rounded-sm px-1 py-0 text-xs text-zinc-100 outline-none";

  if (column.type === "enum") {
    return (
      <select
        ref={(el) => {
          ref.current = el;
        }}
        className={baseClass}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => onCommit(draft, false)}
      >
        {column.options!.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }

  return (
    <input
      ref={(el) => {
        ref.current = el;
      }}
      className={baseClass}
      type={column.type === "number" ? "number" : "text"}
      min={column.type === "number" ? LENS_MIN : undefined}
      max={column.type === "number" ? LENS_MAX : undefined}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => onCommit(draft, false)}
    />
  );
}

export default function ShotListEditor() {
  const shots = useSceneStore((s) => s.shots);
  const findings = useSceneStore((s) => s.findings);
  const selectedOrdinal = useSceneStore((s) => s.selectedOrdinal);
  // Hover is shared state, not a CSS :hover — the timeline's visual lane writes
  // it too, so pointing at a clip lights up its row here and vice versa.
  const hoveredOrdinal = useSceneStore((s) => s.hoveredOrdinal);
  const editingCell = useSceneStore((s) => s.editingCell);
  const updateShot = useSceneStore((s) => s.updateShot);
  const reorderShot = useSceneStore((s) => s.reorderShot);
  const selectShot = useSceneStore((s) => s.selectShot);
  const hoverShot = useSceneStore((s) => s.hoverShot);
  const setEditingCell = useSceneStore((s) => s.setEditingCell);

  const [selectedCol, setSelectedCol] = useState<string>("size");
  const containerRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<Map<number, HTMLTableRowElement>>(new Map());

  const ordered = [...shots].sort((a, b) => a.ordinal - b.ordinal);

  useEffect(() => {
    if (selectedOrdinal !== null) {
      rowRefs.current
        .get(selectedOrdinal)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [selectedOrdinal]);

  const worstUnresolvedSeverity = (ordinal: number): "error" | "warn" | "info" | null => {
    const active = findings.filter((f) => f.shot_ordinal === ordinal && !f.deliberate);
    if (active.some((f) => f.severity === "error")) return "error";
    if (active.some((f) => f.severity === "warn")) return "warn";
    if (active.length > 0) return "info";
    return null;
  };

  const startEditing = (ordinal: number, colKey: string) => {
    const col = COLUMNS.find((c) => c.key === colKey);
    if (!col || col.type === "readonly") return;
    selectShot(ordinal);
    setSelectedCol(colKey);
    setEditingCell({ ordinal, column: colKey });
  };

  const commitEdit = (raw: string, moveRight: boolean) => {
    if (!editingCell) return;
    const col = COLUMNS.find((c) => c.key === editingCell.column);
    const shot = shots.find((s) => s.ordinal === editingCell.ordinal);
    if (col?.apply && shot) {
      updateShot(editingCell.ordinal, col.apply(shot, raw));
    }
    setEditingCell(null);
    if (moveRight) {
      const idx = EDITABLE_KEYS.indexOf(editingCell.column);
      const next = EDITABLE_KEYS[Math.min(idx + 1, EDITABLE_KEYS.length - 1)];
      setSelectedCol(next);
    }
    containerRef.current?.focus();
  };

  const cancelEdit = () => {
    setEditingCell(null);
    containerRef.current?.focus();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (editingCell) return;
    if (ordered.length === 0) return;

    const rowIdx =
      selectedOrdinal === null
        ? -1
        : ordered.findIndex((s) => s.ordinal === selectedOrdinal);
    const colIdx = EDITABLE_KEYS.indexOf(selectedCol);

    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = ordered[Math.min(rowIdx + 1, ordered.length - 1)] ?? ordered[0];
      selectShot(next.ordinal);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const next = ordered[Math.max(rowIdx - 1, 0)] ?? ordered[0];
      selectShot(next.ordinal);
    } else if (e.key === "ArrowRight" || (e.key === "Tab" && !e.shiftKey)) {
      e.preventDefault();
      setSelectedCol(EDITABLE_KEYS[Math.min(colIdx + 1, EDITABLE_KEYS.length - 1)]);
    } else if (e.key === "ArrowLeft" || (e.key === "Tab" && e.shiftKey)) {
      e.preventDefault();
      setSelectedCol(EDITABLE_KEYS[Math.max(colIdx - 1, 0)]);
    } else if (e.key === "Enter" && selectedOrdinal !== null) {
      e.preventDefault();
      startEditing(selectedOrdinal, selectedCol);
    }
  };

  return (
    <div className="cast-panel flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--hairline)] px-3 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
          Shot list
        </h2>
        <span className="truncate font-mono text-[10px] text-zinc-500">
          {ordered.length} shots · arrows move · enter edits · tab commits →
        </span>
      </div>
      {/* data-local-arrow-keys: this grid owns the arrow keys inside it, and the
          workspace's transport shortcuts read the attribute and keep off. */}
      <div
        ref={containerRef}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        data-local-arrow-keys="true"
        className="cast-scroll min-h-0 flex-1 overflow-auto outline-none focus:ring-1 focus:ring-sky-800/60"
      >
        <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 z-10 bg-[#141417]">
            <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500">
              <th className="w-14 border-b border-[var(--hairline)] px-1 py-1.5"></th>
              {COLUMNS.map((c) => (
                <th key={c.key} className={`border-b border-[var(--hairline)] px-2 py-1.5 ${c.width}`}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ordered.map((shot, i) => {
              const severity = worstUnresolvedSeverity(shot.ordinal);
              const isSelected = selectedOrdinal === shot.ordinal;
              const isHovered = !isSelected && hoveredOrdinal === shot.ordinal;
              const rowTint =
                severity === "error"
                  ? "bg-red-950/40"
                  : severity === "warn"
                    ? "bg-amber-950/40"
                    : severity === "info"
                      ? "bg-sky-950/25"
                      : "";
              return (
                <tr
                  key={shot.ordinal}
                  ref={(el) => {
                    if (el) rowRefs.current.set(shot.ordinal, el);
                    else rowRefs.current.delete(shot.ordinal);
                  }}
                  className={`border-b border-[var(--hairline)] hover:bg-zinc-900/60 ${rowTint} ${
                    isSelected
                      ? "outline outline-1 -outline-offset-1 outline-sky-600 bg-zinc-900/80"
                      : isHovered
                        ? "bg-sky-950/30 outline outline-1 -outline-offset-1 outline-sky-800/70"
                        : ""
                  }`}
                  onClick={() => selectShot(shot.ordinal)}
                  onMouseEnter={() => hoverShot(shot.ordinal)}
                  onMouseLeave={() => hoverShot(null)}
                >
                  <td className="px-1 py-0.5 whitespace-nowrap">
                    <button
                      className="text-zinc-600 hover:text-zinc-200 px-0.5 disabled:opacity-30"
                      disabled={i === 0}
                      title="Move shot up"
                      onClick={(e) => {
                        e.stopPropagation();
                        reorderShot(shot.ordinal, "up");
                      }}
                    >
                      ▲
                    </button>
                    <button
                      className="text-zinc-600 hover:text-zinc-200 px-0.5 disabled:opacity-30"
                      disabled={i === ordered.length - 1}
                      title="Move shot down"
                      onClick={(e) => {
                        e.stopPropagation();
                        reorderShot(shot.ordinal, "down");
                      }}
                    >
                      ▼
                    </button>
                  </td>
                  {COLUMNS.map((col) => {
                    const isEditing =
                      editingCell?.ordinal === shot.ordinal &&
                      editingCell.column === col.key;
                    const isCellSelected =
                      isSelected && selectedCol === col.key && col.type !== "readonly";
                    return (
                      <td
                        key={col.key}
                        className={`px-2 py-1 align-top ${col.mono ? "font-mono" : ""} ${
                          col.type === "readonly"
                            ? "text-zinc-500"
                            : "text-zinc-200 cursor-text"
                        } ${isCellSelected && !isEditing ? "ring-1 ring-inset ring-sky-500/70 bg-sky-950/30" : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (col.type === "readonly") {
                            selectShot(shot.ordinal);
                          } else {
                            startEditing(shot.ordinal, col.key);
                          }
                        }}
                      >
                        {isEditing ? (
                          <CellEditor
                            column={col}
                            shot={shot}
                            onCommit={commitEdit}
                            onCancel={cancelEdit}
                          />
                        ) : (
                          <span className={col.key === "intent" ? "text-zinc-400" : ""}>
                            {col.getValue(shot)}
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
