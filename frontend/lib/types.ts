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

// --- Judge provenance ------------------------------------------------------ //
// A score on screen was produced either by the FastAPI judge or by the local
// TypeScript port of the same heuristic (`lib/judge.ts`). The two CAN drift, so
// every result carries the engine that produced it and the UI labels it. They
// are never blended: one engine answers a request, end to end.

export type JudgeEngine = "backend" | "local-heuristic";

/** Human-facing copy for a `JudgeEngine`, rendered next to any score. */
export const JUDGE_ENGINE_META: Record<
  JudgeEngine,
  { label: string; detail: string }
> = {
  backend: {
    label: "backend judge",
    detail:
      "Scored by the FastAPI judge (app/judge) over this project's stored story graph.",
  },
  "local-heuristic": {
    label: "local heuristic",
    detail:
      "Scored in the browser by lib/judge.ts, a port of the backend heuristic. Indicative only — the server is authoritative.",
  },
};

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

/** DIALOGUE lane: one spoken line. `emotion` carries "neutral" for a line the
 * IR left untagged — the backend's `AttributedLine.emotion` is nullable, and
 * `emotionStyle` already renders that as the neutral accent. */
export interface DialogueClip {
  id: string;
  startMs: number;
  durationMs: number;
  character: string;
  emotion: Emotion | "neutral";
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
/**
 * Where a timeline's onsets came from. `rendered` is measured against a stored
 * WAV to the millisecond; `estimated` is planned from the script by the same
 * planner, using a reading-speed heuristic for durations, because no render
 * exists yet. The editor is fully usable either way, but an estimate must never
 * be presented as a measurement.
 */
export type TimingSource = "rendered" | "estimated";

export interface TimelineData {
  projectId: string;
  /** Whether the onsets below are measured or planned. */
  timingSource: TimingSource;
  /** Which scene these lanes belong to. The render endpoints address a shot by
   * (scene_ordinal, shot_ordinal), so the visual lane is useless without it. */
  sceneOrdinal: number;
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

// --------------------------------------------------------------------------- //
// Shot video renders (backend/app/api/routers/renders.py).
//
//   POST /api/v1/projects/{id}/render/video          -> VideoRenderOut  (201)
//   GET  /api/v1/projects/{id}/render/video/{s}/{sh} -> video/mp4 bytes, OR a
//                                                       JSON provider-URL card,
//                                                       OR 404 when unrendered.
//
// A render costs real provider credits, so "nothing rendered yet" is the normal
// state, not an error — see `ShotVideoStatus`.
// --------------------------------------------------------------------------- //

/** Body for POST .../render/video. `duration_s` is clamped 1..60 server-side. */
export interface VideoRenderRequest {
  scene_ordinal: number;
  shot_ordinal: number;
  duration_s: number;
}

/** Response of POST .../render/video — mirrors backend `VideoRenderOut`. */
export interface VideoRenderResult {
  scene_ordinal: number;
  shot_ordinal: number;
  duration_ms: number;
  cost_cents: number;
  provider: string;
  model: string;
  /** Which generation path the renderer took. */
  source: "image" | "text";
  output_urls: string[];
  /** True when GET .../render/video/{s}/{sh} will hand back playable bytes. */
  has_video: boolean;
}

/** A stored clip for one shot, normalized for a `<video>` element.
 *
 * `src` is null when the render exists but this browser cannot play it directly
 * — the provider delivered to `gs://` and no bucket read-through is configured.
 * That is a legitimate, honest state: show the reference, not a broken player. */
export interface ShotVideo {
  sceneOrdinal: number;
  shotOrdinal: number;
  /** Playable URL for `<video src>`, or null (see above). */
  src: string | null;
  /** True when `src` came from `URL.createObjectURL` and must be revoked. */
  srcIsObjectUrl: boolean;
  /** Non-playable provider references (`gs://…`), shown verbatim. */
  providerUrls: string[];
  durationMs: number | null;
  provider: string | null;
  model: string | null;
}

/** Lifecycle of one shot's video in the editor. `none` is the common case. */
export type ShotVideoStatus =
  | "unknown" // not looked up yet
  | "probing" // GET in flight
  | "none" // 404 — nothing rendered for this shot
  | "rendering" // POST in flight
  | "ready" // a render exists (playable, or a provider reference)
  | "error"; // the lookup or the render failed — `error` says why

// --------------------------------------------------------------------------- //
// Edit assistant (backend/app/render/assist.py + app/render/timeline_edits.py).
//
//   POST /api/v1/projects/{id}/scenes/{ordinal}/assist        -> AssistProposal
//   POST /api/v1/projects/{id}/scenes/{ordinal}/timeline/edits -> SceneTimeline
//
// The op union below is the SAME closed vocabulary the edits endpoint accepts,
// mirrored here so the panel is type-safe about what it sends back. It never
// needs to interpret one: the server writes a human `summary` per op precisely
// so the vocabulary is not defined a second time in TypeScript.
// --------------------------------------------------------------------------- //

/** A shot to insert. Mirrors backend `NewShot`: `ShotSpec` minus the ordinal,
 * with the mechanical camera fields optional (the server defaults them to a
 * neutral setup and the continuity validator re-runs afterwards). */
export interface NewShotSpec {
  size: ShotSize;
  subjects: string[];
  covers_lines: number[];
  intent: string;
  axis_side?: AxisSide;
  lens_mm?: number;
  camera_height?: CameraHeight;
  /** Backend vocabulary — narrower than the frontend `Movement`/`Eyeline`. */
  movement?: "static" | "pan" | "tilt" | "dolly" | "handheld" | "crane";
  eyeline?: "left" | "right" | "to_camera" | "none";
}

/** One op the timeline editor accepts. Six, and only six. */
export type TimelineEditOp =
  | {
      op: "reassign_line_character";
      line_ordinal: number;
      character_name: string | null;
      allow_new?: boolean;
    }
  | { op: "set_line_emotion"; line_ordinal: number; emotion: string | null }
  | { op: "set_scene_pacing"; pacing: number }
  | { op: "set_ambience_duck"; depth: number }
  | { op: "insert_shot"; after_ordinal: number | null; shot: NewShotSpec }
  | { op: "set_shot_coverage"; shot_ordinal: number; covers_lines: number[] };

/** One proposed op plus the server's wording for it. */
export interface ProposedEdit {
  edit: TimelineEditOp;
  summary: string;
}

/** One turn of the conversation. The assistant is stateless, so the panel
 * carries the history and sends it back with each message. */
export interface AssistTurn {
  role: "user" | "assistant";
  content: string;
}

/** Body for POST .../assist. */
export interface AssistRequest {
  message: string;
  history: AssistTurn[];
}

/**
 * The assistant's answer: prose plus a batch that is already validated against
 * this scene's real state, so Apply is a batch that will land.
 *
 * `undo` is the inverse batch, already ordered for replay, and is empty exactly
 * when `undo_blocked_by` says why there isn't one (inserting a shot has no
 * inverse op). `dropped` names the ops the model produced that were refused —
 * shown, not swallowed. `estimated_cost_cents` is the cost governor's pre-flight
 * number, which is what the project was charged; it is never a measurement of
 * what the provider billed.
 */
export interface AssistProposal {
  reply: string;
  edits: ProposedEdit[];
  undo: TimelineEditOp[];
  undo_summary: string[];
  undo_blocked_by: string | null;
  dropped: string[];
  provider: string;
  model: string;
  estimated_cost_cents: number;
}

/** What POST .../timeline/edits reports back about the render it invalidated.
 * An edit changes the IR, never the WAV, so a scene with audio comes back
 * stale until it is rendered again. */
export interface EditsApplied {
  stale: boolean;
  staleReasons: string[];
}
