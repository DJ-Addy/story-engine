// The pure model behind the Script view: how a set of timed lines and a set of
// timed shots become a screenplay with a margin.
//
// No store, no data access, no JSX — the same rule `components/timeline/layout`
// follows, so the beat maths can be read without reading a component.

import type { DialogueClip, VisualClip } from "@/lib/types";

/**
 * The backend maps a line with no attributed character onto the narrator voice
 * (app/api/routers/scenes.py), and `httpApi` mirrors that as this name. A beat
 * spoken by them is action, not dialogue, so it is set as prose with no cue.
 */
export const NARRATOR = "NARRATOR";

export interface ScriptBeat {
  line: DialogueClip;
  /** The shot covering this line's onset, when the scene has a shot list. */
  shot: VisualClip | null;
  /**
   * True on the first line of a run covered by the same shot. The margin chip
   * is drawn once per run rather than once per line, so the eye reads a shot as
   * covering a BLOCK of script — which is what coverage actually means — rather
   * than as a label repeated down the page.
   */
  opensShot: boolean;
}

/** The shot under a moment: the last clip that has started. Same rule as the
 * program monitor's, so the script and the picture never disagree about which
 * shot a moment belongs to. */
function shotAt(clips: VisualClip[], ms: number): VisualClip | null {
  if (clips.length === 0) return null;
  let best = clips[0];
  for (const c of clips) {
    if (c.startMs <= ms && c.startMs >= best.startMs) best = c;
  }
  return best;
}

export function buildBeats(
  lines: DialogueClip[],
  shots: VisualClip[],
): ScriptBeat[] {
  const ordered = [...lines].sort((a, b) => a.startMs - b.startMs);
  let previousShotId: string | null = null;
  return ordered.map((line) => {
    const shot = shotAt(shots, line.startMs);
    const opensShot = shot !== null && shot.id !== previousShotId;
    previousShotId = shot?.id ?? previousShotId;
    return { line, shot, opensShot };
  });
}

/**
 * The line the playhead is on: the one being spoken, or failing that the last
 * one that started. Between lines the previous beat stays lit, because that is
 * still where you are in the script — nothing is "no line".
 */
export function activeLineId(
  lines: DialogueClip[],
  currentMs: number,
): string | null {
  let best: DialogueClip | null = null;
  for (const l of lines) {
    if (l.startMs <= currentMs && (best === null || l.startMs >= best.startMs)) {
      best = l;
    }
  }
  return best?.id ?? null;
}

// A character's cue colour. Drawn from the tokens already in globals.css so the
// script reads as the same product as the lanes; assigned by hashing the name
// so a character keeps their colour between scenes and across reloads without
// anything having to store it.
const CUE_TOKENS = [
  "var(--tl-visual)",
  "var(--emotion-calm)",
  "var(--emotion-whispering)",
  "var(--emotion-sarcastic)",
  "var(--emotion-happy)",
  "var(--emotion-excited)",
];

export function cueColor(character: string): string {
  if (character === NARRATOR) return "rgb(var(--emotion-neutral) / 0.9)";
  let h = 0;
  for (let i = 0; i < character.length; i += 1) {
    h = (h * 31 + character.charCodeAt(i)) % 9973;
  }
  return `rgb(${CUE_TOKENS[h % CUE_TOKENS.length]} / 0.96)`;
}
