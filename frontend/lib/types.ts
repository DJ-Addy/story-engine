// Mirrors the backend (FastAPI) contract exactly. Do not diverge without a
// matching backend change.

export type ShotSize =
  | "ecu"
  | "cu"
  | "mcu"
  | "ms"
  | "mws"
  | "ws"
  | "ews"
  | "insert"
  | "pov";

export type AxisSide = "a" | "b" | "neutral";

export type CameraHeight = "low" | "eye" | "high" | "overhead";

export type Movement =
  | "static"
  | "pan"
  | "tilt"
  | "dolly"
  | "track"
  | "crane"
  | "handheld"
  | "zoom";

export type Eyeline = "on_axis" | "off_axis" | "to_camera" | "none";

export interface ShotSpec {
  ordinal: number;
  size: ShotSize;
  subjects: string[];
  axis_side: AxisSide;
  lens_mm: number | null;
  camera_height: CameraHeight;
  movement: Movement;
  eyeline: Eyeline;
  covers_lines: number[];
  intent: string;
}

export type Severity = "info" | "warn" | "error";

export interface Finding {
  id: string;
  rule_code: string;
  severity: Severity;
  message: string;
  shot_ordinal: number | null;
  deliberate: boolean;
  deliberate_note: string | null;
}

export type GrammarProfile = "classical" | "handheld" | "symmetrical" | "anime";

export const SHOT_SIZES: ShotSize[] = [
  "ecu",
  "cu",
  "mcu",
  "ms",
  "mws",
  "ws",
  "ews",
  "insert",
  "pov",
];

export const AXIS_SIDES: AxisSide[] = ["a", "b", "neutral"];

export const CAMERA_HEIGHTS: CameraHeight[] = ["low", "eye", "high", "overhead"];

export const MOVEMENTS: Movement[] = [
  "static",
  "pan",
  "tilt",
  "dolly",
  "track",
  "crane",
  "handheld",
  "zoom",
];

export const EYELINES: Eyeline[] = ["on_axis", "off_axis", "to_camera", "none"];
