// Mock scene: "The Gilded Tankard" — a two-character tavern dialogue.
// Includes one deliberate axis cross (shot 6) and one lens jump (shot 8)
// so the continuity panel has real findings to show.

import type { Finding, GrammarProfile, ShotSpec } from "@/lib/types";

export const MOCK_SCENE_ID = "demo";
export const MOCK_SCENE_TITLE = "INT. THE GILDED TANKARD — NIGHT";
export const MOCK_GRAMMAR_PROFILE: GrammarProfile = "classical";
export const MOCK_SUBJECTS = ["Mara", "Voss"];

export const MOCK_SHOTS: ShotSpec[] = [
  {
    ordinal: 1,
    size: "ws",
    subjects: ["Mara", "Voss"],
    axis_side: "neutral",
    lens_mm: 24,
    camera_height: "eye",
    movement: "dolly",
    eyeline: "none",
    covers_lines: [1, 2],
    intent: "Establish tavern geography; Mara enters, Voss waits at corner table",
  },
  {
    ordinal: 2,
    size: "ms",
    subjects: ["Mara", "Voss"],
    axis_side: "a",
    lens_mm: 35,
    camera_height: "eye",
    movement: "static",
    eyeline: "none",
    covers_lines: [3, 4, 5],
    intent: "Two-shot over the table; establish the axis as Mara sits",
  },
  {
    ordinal: 3,
    size: "mcu",
    subjects: ["Mara"],
    axis_side: "a",
    lens_mm: 50,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [6, 7],
    intent: "Mara probes about the missing ledger; guarded delivery",
  },
  {
    ordinal: 4,
    size: "mcu",
    subjects: ["Voss"],
    axis_side: "a",
    lens_mm: 50,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [8, 9],
    intent: "Voss deflects; matching single to keep coverage symmetrical",
  },
  {
    ordinal: 5,
    size: "insert",
    subjects: ["Voss"],
    axis_side: "neutral",
    lens_mm: 85,
    camera_height: "high",
    movement: "static",
    eyeline: "none",
    covers_lines: [10],
    intent: "Insert: Voss slides a brass key across the table",
  },
  {
    ordinal: 6,
    size: "cu",
    subjects: ["Mara"],
    axis_side: "b",
    lens_mm: 65,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [11, 12],
    intent: "Deliberate axis cross as the power dynamic flips to Mara",
  },
  {
    ordinal: 7,
    size: "cu",
    subjects: ["Voss"],
    axis_side: "b",
    lens_mm: 65,
    camera_height: "low",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [13, 14],
    intent: "Voss cornered; low angle sells his shrinking leverage",
  },
  {
    ordinal: 8,
    size: "cu",
    subjects: ["Mara"],
    axis_side: "b",
    lens_mm: 135,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [15],
    intent: "Long-lens compression jump on Mara's ultimatum line",
  },
  {
    ordinal: 9,
    size: "pov",
    subjects: ["Mara"],
    axis_side: "neutral",
    lens_mm: 40,
    camera_height: "eye",
    movement: "handheld",
    eyeline: "to_camera",
    covers_lines: [16],
    intent: "Mara's POV: Voss's hand hesitating over the key",
  },
  {
    ordinal: 10,
    size: "mws",
    subjects: ["Mara", "Voss"],
    axis_side: "b",
    lens_mm: 32,
    camera_height: "eye",
    movement: "track",
    eyeline: "none",
    covers_lines: [17, 18],
    intent: "Resolve wide on the new axis side; Mara takes the key and exits",
  },
];

// What the backend validator returned for this scene. The AXIS_CROSS on shot 6
// has already been marked deliberate upstream; the LENS_JUMP has not.
export const MOCK_SERVER_FINDINGS: Finding[] = [
  {
    id: "srv-f-001",
    rule_code: "AXIS_CROSS",
    severity: "warn",
    message:
      "Shot 6 crosses the action axis (side 'b' after shot 4 on side 'a'). Screen direction will flip.",
    shot_ordinal: 6,
    deliberate: true,
    deliberate_note: "Intentional flip: power dynamic reverses on Mara's line 11.",
  },
  {
    id: "srv-f-002",
    rule_code: "LENS_JUMP",
    severity: "info",
    message:
      "Shot 8 jumps 70mm (65mm to 135mm) at the same size ('cu') as shot 7. Perspective will shift noticeably.",
    shot_ordinal: 8,
    deliberate: false,
    deliberate_note: null,
  },
];
