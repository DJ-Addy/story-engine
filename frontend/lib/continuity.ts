// Client-side mirror of a subset of the backend continuity validator.
// Used for optimistic feedback on shot edits; the server result reconciles later.

import type { Finding, ShotSpec } from "@/lib/types";

const LENS_JUMP_THRESHOLD_MM = 60;

function axisCross(shots: ShotSpec[]): Finding[] {
  const findings: Finding[] = [];
  let lastSided: ShotSpec | null = null;

  for (const shot of shots) {
    if (shot.axis_side === "neutral") continue;
    if (lastSided !== null && shot.axis_side !== lastSided.axis_side) {
      findings.push({
        id: `optimistic:AXIS_CROSS:${shot.ordinal}`,
        rule_code: "AXIS_CROSS",
        severity: "warn",
        message: `Shot ${shot.ordinal} crosses the action axis (side '${shot.axis_side}' after shot ${lastSided.ordinal} on side '${lastSided.axis_side}'). Screen direction will flip.`,
        shot_ordinal: shot.ordinal,
        deliberate: false,
        deliberate_note: null,
      });
    }
    lastSided = shot;
  }
  return findings;
}

function lensJump(shots: ShotSpec[]): Finding[] {
  const findings: Finding[] = [];

  for (let i = 1; i < shots.length; i++) {
    const prev = shots[i - 1];
    const curr = shots[i];
    if (prev.lens_mm === null || curr.lens_mm === null) continue;
    if (prev.size !== curr.size) continue;
    const diff = Math.abs(curr.lens_mm - prev.lens_mm);
    if (diff > LENS_JUMP_THRESHOLD_MM) {
      findings.push({
        id: `optimistic:LENS_JUMP:${curr.ordinal}`,
        rule_code: "LENS_JUMP",
        severity: "info",
        message: `Shot ${curr.ordinal} jumps ${diff}mm (${prev.lens_mm}mm to ${curr.lens_mm}mm) at the same size ('${curr.size}') as shot ${prev.ordinal}. Perspective will shift noticeably.`,
        shot_ordinal: curr.ordinal,
        deliberate: false,
        deliberate_note: null,
      });
    }
  }
  return findings;
}

/** Run all client-mirrored rules against a shot list, in ordinal order. */
export function validateShots(shots: ShotSpec[]): Finding[] {
  const ordered = [...shots].sort((x, y) => x.ordinal - y.ordinal);
  return [...axisCross(ordered), ...lensJump(ordered)];
}
