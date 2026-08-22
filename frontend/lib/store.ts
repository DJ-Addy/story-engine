import { create } from "zustand";
import type { Finding, ShotSpec } from "@/lib/types";
import { validateShots } from "@/lib/continuity";

export type ValidatorMode = "strict" | "silent";

export interface EditingCell {
  ordinal: number;
  column: string;
}

interface DeliberateMark {
  deliberate: boolean;
  note: string | null;
}

interface SceneState {
  shots: ShotSpec[];
  /** Authoritative findings from the last server validation. */
  serverFindings: Finding[];
  /** Merged view: optimistic client findings reconciled with server metadata. */
  findings: Finding[];
  /** User "mark as deliberate" decisions, keyed by rule_code:shot_ordinal. */
  deliberateMarks: Record<string, DeliberateMark>;
  selectedOrdinal: number | null;
  hoveredOrdinal: number | null;
  editingCell: EditingCell | null;
  validatorMode: ValidatorMode;

  loadScene(shots: ShotSpec[], serverFindings: Finding[]): void;
  updateShot(ordinal: number, patch: Partial<ShotSpec>): void;
  reorderShot(ordinal: number, direction: "up" | "down"): void;
  markDeliberate(findingId: string, deliberate: boolean, note: string | null): void;
  selectShot(ordinal: number | null): void;
  hoverShot(ordinal: number | null): void;
  setEditingCell(cell: EditingCell | null): void;
  setValidatorMode(mode: ValidatorMode): void;
}

const markKey = (f: Pick<Finding, "rule_code" | "shot_ordinal">) =>
  `${f.rule_code}:${f.shot_ordinal ?? "scene"}`;

/**
 * Optimistic findings are recomputed on every edit; server findings supply
 * ids and deliberate flags for matching rule/shot pairs, and any server
 * finding from a rule not mirrored client-side is passed through untouched.
 */
function mergeFindings(
  shots: ShotSpec[],
  serverFindings: Finding[],
  marks: Record<string, DeliberateMark>,
): Finding[] {
  const optimistic = validateShots(shots);
  const serverByKey = new Map(serverFindings.map((f) => [markKey(f), f]));
  const mirroredRules = new Set(["AXIS_CROSS", "LENS_JUMP"]);

  const merged = optimistic.map((f) => {
    const key = markKey(f);
    const server = serverByKey.get(key);
    const mark = marks[key];
    return {
      ...f,
      id: server?.id ?? f.id,
      deliberate: mark?.deliberate ?? server?.deliberate ?? false,
      deliberate_note: mark
        ? mark.note
        : (server?.deliberate_note ?? null),
    };
  });

  const passthrough = serverFindings
    .filter((f) => !mirroredRules.has(f.rule_code))
    .map((f) => {
      const mark = marks[markKey(f)];
      return mark
        ? { ...f, deliberate: mark.deliberate, deliberate_note: mark.note }
        : f;
    });

  return [...merged, ...passthrough];
}

export const useSceneStore = create<SceneState>((set, get) => ({
  shots: [],
  serverFindings: [],
  findings: [],
  deliberateMarks: {},
  selectedOrdinal: null,
  hoveredOrdinal: null,
  editingCell: null,
  validatorMode: "strict",

  loadScene(shots, serverFindings) {
    set({
      shots,
      serverFindings,
      deliberateMarks: {},
      findings: mergeFindings(shots, serverFindings, {}),
      selectedOrdinal: null,
      editingCell: null,
    });
  },

  updateShot(ordinal, patch) {
    const { shots, serverFindings, deliberateMarks } = get();
    const next = shots.map((s) => (s.ordinal === ordinal ? { ...s, ...patch } : s));
    set({
      shots: next,
      findings: mergeFindings(next, serverFindings, deliberateMarks),
    });
  },

  reorderShot(ordinal, direction) {
    const { shots, serverFindings, deliberateMarks, selectedOrdinal } = get();
    const ordered = [...shots].sort((a, b) => a.ordinal - b.ordinal);
    const idx = ordered.findIndex((s) => s.ordinal === ordinal);
    const swapWith = direction === "up" ? idx - 1 : idx + 1;
    if (idx < 0 || swapWith < 0 || swapWith >= ordered.length) return;

    [ordered[idx], ordered[swapWith]] = [ordered[swapWith], ordered[idx]];
    const renumbered = ordered.map((s, i) => ({ ...s, ordinal: i + 1 }));

    const movedNewOrdinal = swapWith + 1;
    set({
      shots: renumbered,
      findings: mergeFindings(renumbered, serverFindings, deliberateMarks),
      selectedOrdinal: selectedOrdinal === ordinal ? movedNewOrdinal : selectedOrdinal,
      editingCell: null,
    });
  },

  markDeliberate(findingId, deliberate, note) {
    const { findings, shots, serverFindings, deliberateMarks } = get();
    const finding = findings.find((f) => f.id === findingId);
    if (!finding) return;
    const marks = {
      ...deliberateMarks,
      [markKey(finding)]: { deliberate, note },
    };
    set({
      deliberateMarks: marks,
      findings: mergeFindings(shots, serverFindings, marks),
    });
  },

  selectShot(ordinal) {
    set({ selectedOrdinal: ordinal });
  },

  hoverShot(ordinal) {
    set({ hoveredOrdinal: ordinal });
  },

  setEditingCell(cell) {
    set({ editingCell: cell });
  },

  setValidatorMode(mode) {
    set({ validatorMode: mode });
  },
}));

/** Findings still requiring attention (not marked deliberate). */
export function unresolvedFindingsForShot(findings: Finding[], ordinal: number): Finding[] {
  return findings.filter((f) => f.shot_ordinal === ordinal && !f.deliberate);
}
