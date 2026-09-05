// Real `StoryEngineApi` implementation against the FastAPI backend under
// /api/v1 (see backend/app/api/routers/). `lib/api.ts` picks this or `MockApi`
// from one env var, so no UI component changes when the switch is flipped.
//
// Two things this file has to reconcile:
//
// 1. The UI addresses a scene by one opaque string; the API addresses it by
//    (project_id, scene_ordinal). `parseSceneRef` bridges that.
// 2. Not every panel has an endpoint yet. Where one is missing this file falls
//    back explicitly and names the endpoint it is waiting for — it never
//    invents a URL. Every such spot is tagged `GAP:`.

import type { CastingData, SceneData, StoryEngineApi } from "@/lib/api";
import type {
  AmbienceBlock,
  AnimaticJudgment,
  DialogueClip,
  Emotion,
  Eyeline,
  Finding,
  GrammarProfile,
  JudgeEngine,
  RankingResult,
  SceneMarker,
  SfxMarker,
  ShotSize,
  ShotSpec,
  ShotVideo,
  TimelineData,
  VideoRenderRequest,
  VideoRenderResult,
  VisualClip,
  Voice,
  VoiceFitRequest,
  VoiceFitResult,
  VoiceRankRequest,
} from "@/lib/types";
import { EMOTIONS, SHOT_SIZES } from "@/lib/types";
import type { CharacterSignal } from "@/lib/judge";
import { MOCK_VOICE_POOL } from "@/lib/mock";
import {
  API_BASE_URL,
  ApiError,
  clearToken,
  ContractMismatchError,
  getToken,
  request,
  requestOptional,
} from "@/lib/apiClient";

// --------------------------------------------------------------------------- //
// Wire shapes — mirror backend/app/api/schemas.py and app/ingest/elements.py.
// snake_case throughout; the mappers below are the only place that converts.
// --------------------------------------------------------------------------- //

interface ProjectOut {
  id: string;
  owner_id: string;
  title: string;
  grammar_profile: string;
  validator_mode: string;
  rights_attested: boolean;
  cost_cap_cents: number;
  cost_spent_cents: number;
}

interface AttributedLineWire {
  ordinal: number;
  kind: string; // narration|dialogue|action|parenthetical|transition
  text: string;
  character_name: string | null;
  emotion: string | null;
  attribution_confidence: number | null;
  attribution_source: string | null;
}

interface NormalizedSceneWire {
  ordinal: number;
  slugline: string | null;
  interior: boolean | null;
  location: string | null;
  time_of_day: string | null;
  lines: AttributedLineWire[];
}

interface StoryGraphWire {
  scenes: NormalizedSceneWire[];
  characters: { canonical_name: string; aliases: string[]; line_count: number }[];
}

/** backend app/shotlist/schema.py — note the narrower vocabularies below. */
interface ShotSpecWire {
  ordinal: number;
  size: string;
  subjects: string[];
  axis_side: string;
  lens_mm: number;
  camera_height: string;
  movement: string;
  eyeline: string;
  covers_lines: number[];
  intent: string;
}

interface ShotListWire {
  scene_ordinal: number;
  action_axis: string;
  shots: ShotSpecWire[];
}

interface FindingWire {
  id: string;
  rule_code: string;
  severity: string;
  message: string;
  shot_ordinal: number | null;
  deliberate: boolean;
  deliberate_note: string | null;
}

/** backend `SceneTimeline` — the four lanes that back the timeline editor. */
interface SceneTimelineWire {
  scene_ordinal: number;
  duration_ms: number;
  markers: { scene_ordinal: number; start_ms: number; slugline: string | null }[];
  dialogue: {
    line_ordinal: number;
    start_ms: number;
    duration_ms: number;
    character: string | null;
    emotion: string | null;
    text: string;
  }[];
  ambience: { start_ms: number; duration_ms: number; tag: string }[];
  sfx: { at_ms: number; name: string }[];
  visual: {
    start_ms: number;
    duration_ms: number;
    shot_ordinal: number;
    size: string;
    subjects: string[];
  }[];
  // Render knobs + staleness, written by POST .../timeline/edits. `TimelineData`
  // has nowhere to put them yet, so they are declared but not mapped — the
  // AI-assist strip still mutates the store locally (lib/timelineStore.ts).
  settings?: { pacing: number; ambience_duck: number };
  stale?: boolean;
  stale_reasons?: string[];
}

// --------------------------------------------------------------------------- //
// Scene / project addressing
// --------------------------------------------------------------------------- //

/** The demo routes hard-code the id "demo". Point that at a real project id
 * without touching the pages by setting NEXT_PUBLIC_DEMO_PROJECT_ID. */
const DEMO_PROJECT_ID = process.env.NEXT_PUBLIC_DEMO_PROJECT_ID ?? "";

const resolveProjectId = (ref: string): string =>
  (ref === "demo" || ref === "") && DEMO_PROJECT_ID ? DEMO_PROJECT_ID : ref;

interface SceneRef {
  projectId: string;
  ordinal: number;
}

/**
 * Resolve a UI scene/project id to the (project, scene ordinal) pair every
 * scene endpoint needs. Accepted forms: "proj_1/4", "proj_1:4", or a bare
 * "proj_1".
 *
 * A bare id means scene 1: `normalize.py` numbers scenes 1-based and reserves
 * ordinal 0 for the pre-slugline preamble, so scene 1 is always the first real
 * scene — which is the only one the demo pages open.
 */
function parseSceneRef(ref: string): SceneRef {
  const match = /^(.*?)[/:](\d+)$/.exec(ref);
  if (match) {
    return { projectId: resolveProjectId(match[1]), ordinal: Number(match[2]) };
  }
  return { projectId: resolveProjectId(ref), ordinal: 1 };
}

// --------------------------------------------------------------------------- //
// Vocabulary narrowing
//
// The frontend and backend shot vocabularies have DIVERGED. Sizes, axis sides
// and camera heights match; movement and eyeline do not:
//
//   movement  backend: static|pan|tilt|dolly|handheld|crane
//             frontend adds: track, zoom
//   eyeline   backend: left|right|to_camera|none         (screen direction)
//             frontend: on_axis|off_axis|to_camera|none  (axis relation)
//   lens_mm   backend: required int 8..300; frontend: number | null
//
// Inbound, a value with no frontend equivalent flattens to the neutral member
// rather than being guessed at. Outbound (`toWireShotList`) refuses to send a
// value the backend schema cannot hold, so the caller sees the gap instead of
// a 422. Closing this properly is a contract change on both sides.
// --------------------------------------------------------------------------- //

const SHOT_SIZE_SET = new Set<string>(SHOT_SIZES);
const EMOTION_SET = new Set<string>(EMOTIONS);
const AXIS_SIDE_SET = new Set(["a", "b", "neutral"]);
const CAMERA_HEIGHT_SET = new Set(["low", "eye", "high", "overhead"]);
const GRAMMAR_PROFILE_SET = new Set(["classical", "handheld", "symmetrical", "anime"]);

const WIRE_MOVEMENTS = new Set(["static", "pan", "tilt", "dolly", "handheld", "crane"]);
const WIRE_EYELINES = new Set(["left", "right", "to_camera", "none"]);
const WIRE_LENS_MIN = 8;
const WIRE_LENS_MAX = 300;
const WIRE_INTENT_MAX = 200;

const toShotSize = (v: string): ShotSize =>
  SHOT_SIZE_SET.has(v) ? (v as ShotSize) : "ms";

/** Backend `emotion` is nullable (neutral delivery) and its label set can grow
 * ahead of the frontend's; both cases read as "neutral" in the UI. */
const toEmotion = (v: string | null): Emotion | "neutral" =>
  v !== null && EMOTION_SET.has(v) ? (v as Emotion) : "neutral";

/** "left"/"right" carry no axis relation, so they flatten to "none". */
const toEyeline = (v: string): Eyeline =>
  v === "to_camera" || v === "none" || v === "on_axis" || v === "off_axis"
    ? (v as Eyeline)
    : "none";

function toShotSpec(wire: ShotSpecWire): ShotSpec {
  return {
    ordinal: wire.ordinal,
    size: toShotSize(wire.size),
    subjects: [...wire.subjects],
    axis_side: AXIS_SIDE_SET.has(wire.axis_side)
      ? (wire.axis_side as ShotSpec["axis_side"])
      : "neutral",
    lens_mm: wire.lens_mm,
    camera_height: CAMERA_HEIGHT_SET.has(wire.camera_height)
      ? (wire.camera_height as ShotSpec["camera_height"])
      : "eye",
    movement: WIRE_MOVEMENTS.has(wire.movement)
      ? (wire.movement as ShotSpec["movement"])
      : "static",
    eyeline: toEyeline(wire.eyeline),
    covers_lines: [...wire.covers_lines],
    intent: wire.intent,
  };
}

const toFinding = (wire: FindingWire): Finding => ({
  id: wire.id,
  rule_code: wire.rule_code,
  severity:
    wire.severity === "error" || wire.severity === "warn" ? wire.severity : "info",
  message: wire.message,
  shot_ordinal: wire.shot_ordinal,
  deliberate: wire.deliberate,
  deliberate_note: wire.deliberate_note,
});

/** Build the POST body for /shotlist, refusing anything the backend schema
 * cannot represent (see the vocabulary note above). */
function toWireShotList(
  sceneOrdinal: number,
  actionAxis: string,
  shots: ShotSpec[],
): ShotListWire {
  const problems: string[] = [];
  for (const s of shots) {
    if (!WIRE_MOVEMENTS.has(s.movement)) {
      problems.push(
        `shot ${s.ordinal}: movement "${s.movement}" (API accepts ${[...WIRE_MOVEMENTS].join("|")})`,
      );
    }
    if (!WIRE_EYELINES.has(s.eyeline)) {
      problems.push(
        `shot ${s.ordinal}: eyeline "${s.eyeline}" (API accepts ${[...WIRE_EYELINES].join("|")})`,
      );
    }
    if (s.lens_mm === null) {
      problems.push(`shot ${s.ordinal}: lens_mm is required by the API`);
    } else if (s.lens_mm < WIRE_LENS_MIN || s.lens_mm > WIRE_LENS_MAX) {
      problems.push(
        `shot ${s.ordinal}: lens_mm ${s.lens_mm} outside ${WIRE_LENS_MIN}-${WIRE_LENS_MAX}`,
      );
    }
    if (s.intent.length > WIRE_INTENT_MAX) {
      problems.push(`shot ${s.ordinal}: intent longer than ${WIRE_INTENT_MAX} chars`);
    }
  }
  if (problems.length) {
    throw new ContractMismatchError(
      `Shot list cannot be sent — the API schema does not accept: ${problems.join("; ")}`,
    );
  }

  return {
    scene_ordinal: sceneOrdinal,
    action_axis: actionAxis,
    shots: shots.map((s) => ({
      ordinal: s.ordinal,
      size: s.size,
      subjects: [...s.subjects],
      axis_side: s.axis_side,
      lens_mm: s.lens_mm as number, // null is rejected above
      camera_height: s.camera_height,
      movement: s.movement,
      eyeline: s.eyeline,
      covers_lines: [...s.covers_lines],
      intent: s.intent,
    })),
  };
}

// --------------------------------------------------------------------------- //
// Story-graph derivations
// --------------------------------------------------------------------------- //

/** Port of `_collect_signals` in backend/app/judge/voices.py: bucket dialogue
 * and narration counts plus the delivery-emotion distribution per speaker.
 * Characters declared on the graph are represented even with zero lines. */
function signalsFromGraph(graph: StoryGraphWire): CharacterSignal[] {
  const byName = new Map<string, CharacterSignal>();
  const bucket = (name: string): CharacterSignal => {
    let sig = byName.get(name);
    if (!sig) {
      sig = { name, dialogue_lines: 0, narration_lines: 0, emotions: {} };
      byName.set(name, sig);
    }
    return sig;
  };

  for (const c of graph.characters) bucket(c.canonical_name);

  for (const scene of graph.scenes) {
    for (const line of scene.lines) {
      if (line.character_name === null) continue;
      const sig = bucket(line.character_name);
      if (line.kind === "narration") sig.narration_lines += 1;
      else if (line.kind === "dialogue") sig.dialogue_lines += 1;
      else continue;
      if (line.emotion) {
        sig.emotions[line.emotion] = (sig.emotions[line.emotion] ?? 0) + 1;
      }
    }
  }
  return [...byName.values()];
}

/** Distinct dialogue speakers in a scene, in first-appearance order — the
 * subjects a shot in that scene can point at. */
function sceneSubjects(scene: NormalizedSceneWire): string[] {
  const seen: string[] = [];
  for (const line of scene.lines) {
    if (line.kind !== "dialogue" || !line.character_name) continue;
    if (!seen.includes(line.character_name)) seen.push(line.character_name);
  }
  return seen;
}

const sceneTitle = (scene: NormalizedSceneWire): string =>
  scene.slugline ?? scene.location ?? `Scene ${scene.ordinal}`;

// --------------------------------------------------------------------------- //
// Timeline mapping: backend `SceneTimeline` -> the UI's `TimelineData`
// --------------------------------------------------------------------------- //

/** Clip ids are the UI's selection keys, so they must be stable across reloads.
 * The wire carries no ids, so they are derived from each lane's natural key
 * (line ordinal / shot ordinal), with the array index as a tiebreak where the
 * lane has none. */
function toTimeline(
  projectId: string,
  wire: SceneTimelineWire,
  shots: ShotListWire | null,
): TimelineData {
  const intentByOrdinal = new Map(
    (shots?.shots ?? []).map((s) => [s.ordinal, s.intent]),
  );

  const visual: VisualClip[] = wire.visual.map((v) => ({
    id: `vis-${v.shot_ordinal}`,
    startMs: v.start_ms,
    durationMs: v.duration_ms,
    shotOrdinal: v.shot_ordinal,
    size: toShotSize(v.size),
    // The timeline lane carries no intent text; it comes from the shot list.
    label:
      intentByOrdinal.get(v.shot_ordinal) ??
      `${v.size.toUpperCase()}${v.subjects.length ? ` — ${v.subjects.join(", ")}` : ""}`,
    subjects: [...v.subjects],
  }));

  const dialogue: DialogueClip[] = wire.dialogue.map((d) => ({
    id: `dlg-${d.line_ordinal}`,
    startMs: d.start_ms,
    durationMs: d.duration_ms,
    // A null character is the narrator / an action line, matching the backend's
    // own voice map (None -> the narrator voice, app/api/routers/scenes.py).
    character: d.character ?? "NARRATOR",
    emotion: toEmotion(d.emotion),
    text: d.text,
  }));

  const ambience: AmbienceBlock[] = wire.ambience.map((a, i) => ({
    id: `amb-${i}-${a.tag.replace(/\s+/g, "-")}`,
    startMs: a.start_ms,
    durationMs: a.duration_ms,
    tag: a.tag,
  }));

  const sfx: SfxMarker[] = wire.sfx.map((s, i) => ({
    id: `sfx-${i}-${s.at_ms}`,
    atMs: s.at_ms,
    name: s.name,
  }));

  const scenes: SceneMarker[] = wire.markers.map((m) => ({
    startMs: m.start_ms,
    label: m.slugline ?? `Scene ${m.scene_ordinal}`,
  }));

  return {
    projectId,
    sceneOrdinal: wire.scene_ordinal,
    sceneTitle: wire.markers[0]?.slugline ?? `Scene ${wire.scene_ordinal}`,
    durationMs: wire.duration_ms,
    scenes,
    lanes: { visual, dialogue, ambience, sfx },
  };
}

// --------------------------------------------------------------------------- //
// Shot video renders
//
// GET .../render/video/{scene}/{shot} is the one endpoint in the API that does
// not always answer JSON: it returns raw `video/mp4` bytes when the clip is
// held (or read back out of the bucket), and a JSON provider-URL card when it
// is not. `request()` in apiClient always decodes JSON, so this path issues its
// own fetch — using apiClient's base URL and token so auth stays in one place.
// --------------------------------------------------------------------------- //

/** JSON fallback body of GET .../render/video/{scene}/{shot}. */
interface VideoRefWire {
  output_urls: string[];
  provider: string;
  model: string;
  duration_ms: number;
}

interface VideoRenderOutWire {
  scene_ordinal: number;
  shot_ordinal: number;
  duration_ms: number;
  cost_cents: number;
  provider: string;
  model: string;
  source: string;
  output_urls: string[];
  has_video: boolean;
}

const toVideoRenderResult = (w: VideoRenderOutWire): VideoRenderResult => ({
  scene_ordinal: w.scene_ordinal,
  shot_ordinal: w.shot_ordinal,
  duration_ms: w.duration_ms,
  cost_cents: w.cost_cents,
  provider: w.provider,
  model: w.model,
  source: w.source === "image" ? "image" : "text",
  output_urls: [...w.output_urls],
  has_video: w.has_video,
});

/** A provider URL a browser can actually put in `<video src>`. `gs://` cannot
 * be fetched by a browser at all, so it is kept as a reference, never a src. */
const isPlayableUrl = (url: string): boolean => /^https?:\/\//i.test(url);

// --------------------------------------------------------------------------- //
// Casting seed
// --------------------------------------------------------------------------- //

/** Deterministic character -> voice pairing for the studio to open on.
 *
 * GAP: there is no casting resource on the API (no GET/PUT
 * /api/v1/projects/{id}/casting), so nothing persists a casting yet. Rather
 * than fabricate one that looks authoritative, every speaking character is
 * paired round-robin with the voice pool; the user's edits are the source of
 * truth from there. */
function seedCasting(
  characters: CharacterSignal[],
  voices: Voice[],
): Record<string, Voice> {
  if (!voices.length) return {};
  const speaking = characters
    .filter((c) => c.dialogue_lines + c.narration_lines > 0)
    .sort((a, b) => a.name.localeCompare(b.name));
  return Object.fromEntries(
    speaking.map((c, i) => [c.name, voices[i % voices.length]]),
  );
}

// --------------------------------------------------------------------------- //

export class HttpApi implements StoryEngineApi {
  /** Every score this client returns came out of FastAPI's `app/judge`. The
   * local port in `lib/judge.ts` is never consulted here — if the server fails,
   * the caller sees the failure rather than a silently substituted number. */
  readonly judgeEngine: JudgeEngine = "backend";

  /** `SceneShotList` requires an `action_axis` that `SceneData` does not carry.
   * `getScene` already reads it from GET /shots, so cache it per scene instead
   * of paying a second round trip on every validate. */
  private readonly actionAxis = new Map<string, string>();

  async getScene(sceneId: string): Promise<SceneData> {
    const { projectId, ordinal } = parseSceneRef(sceneId);
    const [project, graph, shotlist] = await Promise.all([
      request<ProjectOut>(`/projects/${encodeURIComponent(projectId)}`),
      request<StoryGraphWire>(`/projects/${encodeURIComponent(projectId)}/graph`),
      requestOptional<ShotListWire>(
        `/projects/${encodeURIComponent(projectId)}/scenes/${ordinal}/shots`,
      ),
    ]);

    const scene = graph.scenes.find((s) => s.ordinal === ordinal);
    if (!scene) throw new Error(`Scene ${ordinal} not found in project ${projectId}`);
    if (shotlist) this.actionAxis.set(`${projectId}/${ordinal}`, shotlist.action_axis);

    return {
      id: sceneId,
      title: sceneTitle(scene),
      grammar_profile: GRAMMAR_PROFILE_SET.has(project.grammar_profile)
        ? (project.grammar_profile as GrammarProfile)
        : "classical",
      subjects: sceneSubjects(scene),
      shots: (shotlist?.shots ?? []).map(toShotSpec),
      // GAP: findings are only returned by POST .../shotlist — there is no
      // GET /api/v1/projects/{id}/scenes/{ordinal}/findings to read the stored
      // ones back. Re-POSTing here would wipe the user's `deliberate` marks
      // (the router calls replace_findings), so the scene opens with no server
      // findings and the store's optimistic client validation carries the UI
      // until `validateScene` runs.
      findings: [],
    };
  }

  async validateScene(sceneId: string, shots: ShotSpec[]): Promise<Finding[]> {
    const { projectId, ordinal } = parseSceneRef(sceneId);
    const key = `${projectId}/${ordinal}`;
    let actionAxis = this.actionAxis.get(key);
    if (actionAxis === undefined) {
      const existing = await requestOptional<ShotListWire>(
        `/projects/${encodeURIComponent(projectId)}/scenes/${ordinal}/shots`,
      );
      actionAxis = existing?.action_axis ?? "";
      this.actionAxis.set(key, actionAxis);
    }

    const findings = await request<FindingWire[]>(
      `/projects/${encodeURIComponent(projectId)}/scenes/${ordinal}/shotlist`,
      { method: "POST", body: toWireShotList(ordinal, actionAxis, shots) },
    );
    return findings.map(toFinding);
  }

  async setFindingDeliberate(
    sceneId: string,
    findingId: string,
    deliberate: boolean,
    note: string | null,
  ): Promise<void> {
    const { projectId, ordinal } = parseSceneRef(sceneId);
    await request<FindingWire>(
      `/projects/${encodeURIComponent(projectId)}/scenes/${ordinal}/findings/${encodeURIComponent(findingId)}`,
      { method: "PATCH", body: { deliberate, deliberate_note: note } },
    );
  }

  // --- Casting studio ---------------------------------------------------- //

  async getCasting(projectId: string): Promise<CastingData> {
    const pid = resolveProjectId(projectId);
    const [project, graph] = await Promise.all([
      request<ProjectOut>(`/projects/${encodeURIComponent(pid)}`),
      request<StoryGraphWire>(`/projects/${encodeURIComponent(pid)}/graph`),
    ]);

    // Characters + signals are composed client-side from the story graph — the
    // same walk the backend judge does (see signalsFromGraph).
    const characters = signalsFromGraph(graph);

    // GAP: the TTS adapters expose `list_voices()` but no router publishes it —
    // there is no GET /api/v1/voices (or /projects/{id}/voices). Until one
    // exists the pool is the mock catalog, which is shaped exactly like the
    // adapter `Voice` model, so the judge endpoints accept it unchanged.
    const voices = MOCK_VOICE_POOL.map((v) => ({ ...v, tags: [...v.tags] }));

    return {
      projectId: pid,
      title: project.title,
      characters,
      voices,
      casting: seedCasting(characters, voices),
      // GAP: no stored casting variants (no /projects/{id}/casting/candidates).
      // The leaderboard starts empty; the user snapshots candidates locally.
      candidates: [],
      // The pool above is fixture data even on this live path. Flagged so the
      // studio can say so instead of passing stub voice names off as a catalog.
      voicesAreStub: true,
    };
  }

  async judgeVoices(
    projectId: string,
    req: VoiceFitRequest,
  ): Promise<VoiceFitResult> {
    return request<VoiceFitResult>(
      `/projects/${encodeURIComponent(resolveProjectId(projectId))}/judge/voices`,
      { method: "POST", body: req },
    );
  }

  async rankVoices(
    projectId: string,
    req: VoiceRankRequest,
  ): Promise<RankingResult<VoiceFitResult>> {
    return request<RankingResult<VoiceFitResult>>(
      `/projects/${encodeURIComponent(resolveProjectId(projectId))}/judge/rank/voices`,
      { method: "POST", body: req },
    );
  }

  async judgeAnimatic(projectId: string): Promise<AnimaticJudgment> {
    return request<AnimaticJudgment>(
      `/projects/${encodeURIComponent(resolveProjectId(projectId))}/judge/animatic`,
      { method: "POST" },
    );
  }

  // --- Timeline editor --------------------------------------------------- //

  /** GET .../scenes/{ordinal}/timeline, joined with the scene's shot list for
   * the visual lane's intent text. The timeline is built from a stored audio
   * render, so it 404s ("No audio rendered yet") until POST .../render/audio
   * has run for that scene — that render spends TTS credits, so this method
   * deliberately does not trigger it. */
  async getTimeline(projectId: string): Promise<TimelineData> {
    const { projectId: pid, ordinal } = parseSceneRef(projectId);
    const [timeline, shots] = await Promise.all([
      request<SceneTimelineWire>(
        `/projects/${encodeURIComponent(pid)}/scenes/${ordinal}/timeline`,
      ),
      requestOptional<ShotListWire>(
        `/projects/${encodeURIComponent(pid)}/scenes/${ordinal}/shots`,
      ),
    ]);
    return toTimeline(pid, timeline, shots);
  }

  /**
   * GET .../render/video/{scene}/{shot}.
   *
   * A 404 is the *expected* answer for almost every shot — video renders cost
   * provider credits, so one has usually never been made — and resolves to
   * `null` rather than throwing. Anything else (401, 502 from a bucket read,
   * a network failure) throws, because those are real problems the editor must
   * show rather than paper over as "no video".
   */
  async getShotVideo(
    projectId: string,
    sceneOrdinal: number,
    shotOrdinal: number,
  ): Promise<ShotVideo | null> {
    const pid = resolveProjectId(projectId);
    const path = `/projects/${encodeURIComponent(pid)}/render/video/${sceneOrdinal}/${shotOrdinal}`;

    const headers: Record<string, string> = { Accept: "video/mp4, application/json" };
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const res = await fetch(`${API_BASE_URL}${path}`, {
      headers,
      credentials: "same-origin",
    });

    if (res.status === 404) return null;
    if (res.status === 401) {
      clearToken();
      throw new ApiError(401, "Not authenticated — sign in again", path);
    }
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      const detail =
        body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string"
          ? (body as { detail: string }).detail
          : `${res.status} ${res.statusText}`;
      throw new ApiError(res.status, detail, path);
    }

    const contentType = res.headers.get("content-type") ?? "";

    // Provider-reference card: the render exists but the bytes live in a bucket
    // this deployment cannot read through. Report it honestly — no fake player.
    if (contentType.includes("application/json")) {
      const ref = (await res.json()) as VideoRefWire;
      const urls = Array.isArray(ref.output_urls) ? ref.output_urls : [];
      const playable = urls.find(isPlayableUrl) ?? null;
      return {
        sceneOrdinal,
        shotOrdinal,
        src: playable,
        srcIsObjectUrl: false,
        providerUrls: urls.filter((u) => u !== playable),
        durationMs: typeof ref.duration_ms === "number" ? ref.duration_ms : null,
        provider: ref.provider ?? null,
        model: ref.model ?? null,
      };
    }

    // The ordinary success: mp4 bytes. Wrapped in an object URL the caller owns
    // and must revoke (see `revokeShotVideo` in lib/timelineStore.ts).
    const blob = await res.blob();
    return {
      sceneOrdinal,
      shotOrdinal,
      src: URL.createObjectURL(blob),
      srcIsObjectUrl: true,
      providerUrls: [],
      durationMs: null, // the element reports the true duration on loadedmetadata
      provider: null,
      model: null,
    };
  }

  /** POST .../render/video. Spends credits; 403 when rights are not attested
   * and 402 when the cost governor refuses. Both arrive as `ApiError` with the
   * server's own wording, which is exactly what the editor shows. */
  async renderShotVideo(
    projectId: string,
    req: VideoRenderRequest,
  ): Promise<VideoRenderResult> {
    const wire = await request<VideoRenderOutWire>(
      `/projects/${encodeURIComponent(resolveProjectId(projectId))}/render/video`,
      { method: "POST", body: req },
    );
    return toVideoRenderResult(wire);
  }
}
