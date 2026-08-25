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

// --------------------------------------------------------------------------- //
// Casting studio: voices, voice-fit judge, animatic judge, ranking.
// Mirrors backend/app/adapters/base.py and backend/app/judge/model.py exactly.
// Judge scores are floats in [0, 1] (rounded to 3 decimals server-side), and
// findings reuse the `Severity` vocabulary above.
// --------------------------------------------------------------------------- //

/** A TTS voice from a provider adapter (backend `app.adapters.base.Voice`). */
export interface Voice {
  id: string;
  name: string;
  tags: string[];
}

/** A concrete mismatch between a character and its assigned voice. */
export interface VoiceFinding {
  code: string;
  severity: Severity;
  message: string;
}

/** An alternative voice from the pool that fits better. */
export interface VoiceSuggestion {
  voice_id: string;
  voice_name: string;
  score: number;
}

/** How well one character's assigned voice fits that character. */
export interface CharacterVoiceFit {
  character: string;
  voice_id: string;
  voice_name: string;
  score: number;
  rationale: string;
  line_count: number;
  speaks_share: number;
  dominant_emotions: string[];
  findings: VoiceFinding[];
  suggestions: VoiceSuggestion[];
}

/** Overall casting evaluation across every cast character. */
export interface VoiceFitResult {
  overall_score: number;
  rationale: string;
  characters: CharacterVoiceFit[];
  uncast_characters: string[];
}

/** A concrete quality issue in the previz coverage. */
export interface AnimaticFinding {
  code: string;
  severity: Severity;
  message: string;
  scene_ordinal: number | null;
  shot_ordinal: number | null;
}

/** Per-scene breakdown of the four animatic quality axes. */
export interface SceneAnimaticScore {
  scene_ordinal: number;
  score: number;
  coverage_score: number;
  continuity_score: number;
  variety_score: number;
  pacing_score: number;
  shot_count: number;
  findings: AnimaticFinding[];
}

/** Overall animatic evaluation across every scene with a shot list. */
export interface AnimaticJudgment {
  overall_score: number;
  rationale: string;
  coverage_score: number;
  continuity_score: number;
  variety_score: number;
  pacing_score: number;
  scenes: SceneAnimaticScore[];
  findings: AnimaticFinding[];
}

// --- Generic "pick the best" leaderboard --------------------------------- //
// Parametrized by whatever judge result the entries carry (`VoiceFitResult`,
// `AnimaticJudgment`, ...), so the same shape backs every ranking endpoint.

/** One candidate's place on the leaderboard, with its full judge result. */
export interface RankedEntry<T> {
  label: string;
  rank: number; // 1-based; 1 is the best-scoring candidate
  overall_score: number;
  result: T;
}

/** A leaderboard of judged candidates, best first, with the winning label. */
export interface RankingResult<T> {
  winner: string | null;
  entries: RankedEntry<T>[];
}

// --- Request shapes (mirror backend/app/api/schemas.py) ------------------- //

/** Body for POST .../judge/voices. */
export interface VoiceFitRequest {
  /** Character canonical name -> assigned voice. */
  casting: Record<string, Voice>;
  /** Pool suggestions are drawn from; defaults to the casting's own voices. */
  available_voices?: Voice[] | null;
}

/** One casting variant to rank: a label plus a character -> voice map. */
export interface VoiceCandidate {
  label: string;
  casting: Record<string, Voice>;
}

/** Body for POST .../judge/rank/voices. */
export interface VoiceRankRequest {
  candidates: VoiceCandidate[];
  available_voices?: Voice[] | null;
}

/** Canonical delivery-emotion vocabulary (backend `app.nlp.emotion.EMOTIONS`). */
export const EMOTIONS = [
  "angry",
  "happy",
  "sad",
  "afraid",
  "excited",
  "calm",
  "whispering",
  "shouting",
  "urgent",
  "sarcastic",
  "surprised",
  "serious",
] as const;

export type Emotion = (typeof EMOTIONS)[number];

// --------------------------------------------------------------------------- //
// Timeline editor (presentational, mock-first). UI shapes for the scrubbable
// multi-lane timeline — NOT a 1:1 backend contract. When real media is wired,
// the rendered WAV + the scene's shot list feed these lanes (see api.getTimeline).
// --------------------------------------------------------------------------- //

export type TimelineLaneKind = "visual" | "dialogue" | "ambience" | "sfx";

/** A labeled marker on the ruler (scene / beat boundary). */
export interface SceneMarker {
  startMs: number;
  label: string;
}

/** VISUAL lane: one shot's board clip. */
export interface VisualClip {
  id: string;
  startMs: number;
  durationMs: number;
  shotOrdinal: number;
  size: ShotSize;
  label: string;
  subjects: string[];
}

/** DIALOGUE lane: one spoken line. */
export interface DialogueClip {
  id: string;
  startMs: number;
  durationMs: number;
  character: string;
  emotion: Emotion;
  text: string;
}

/** AMBIENCE lane: a continuous bed block. `ducked` is set by the AI "duck" edit. */
export interface AmbienceBlock {
  id: string;
  startMs: number;
  durationMs: number;
  tag: string;
  ducked?: boolean;
}

/** SFX lane: a one-shot marker. */
export interface SfxMarker {
  id: string;
  atMs: number;
  name: string;
}

export interface TimelineLanes {
  visual: VisualClip[];
  dialogue: DialogueClip[];
  ambience: AmbienceBlock[];
  sfx: SfxMarker[];
}

/** Everything the timeline editor needs to open, in one payload. */
export interface TimelineData {
  projectId: string;
  sceneTitle: string;
  durationMs: number;
  scenes: SceneMarker[];
  lanes: TimelineLanes;
}

/** A cross-lane selection for the inspector. */
export interface TimelineSelection {
  lane: TimelineLaneKind;
  id: string;
}
