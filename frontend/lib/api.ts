// Repository layer. All data access goes through this interface so the mock
// can be swapped for the FastAPI client without touching UI code.
//
// Which implementation `api` points at is decided once, here — see the
// "Mock vs live" block at the bottom of this file. The rule in one line:
// **a production build talks to the real API unless someone deliberately opts
// out**, and whichever side is answering is stamped onto the UI.

import { HttpApi } from "@/lib/httpApi";
import type {
  AnimaticJudgment,
  AssistProposal,
  AssistRequest,
  EditsApplied,
  Finding,
  GrammarProfile,
  JudgeEngine,
  RankingResult,
  ShotSpec,
  ShotVideo,
  TimelineData,
  TimelineEditOp,
  VideoRenderRequest,
  VideoRenderResult,
  Voice,
  VoiceFitRequest,
  VoiceFitResult,
  VoiceRankRequest,
} from "@/lib/types";
import type { CharacterSignal } from "@/lib/judge";
import { judgeCasting } from "@/lib/judge";
import type { MockSceneFixture } from "@/lib/mock";
import {
  castingFromIds,
  MOCK_ANIMATIC_JUDGMENT,
  MOCK_CANDIDATE_CASTINGS,
  MOCK_CASTING_TITLE,
  MOCK_CHARACTER_SIGNALS,
  MOCK_DEFAULT_CASTING,
  MOCK_PROJECT_ID,
  MOCK_SCENE_FIXTURES,
  MOCK_SCENE_ID,
  MOCK_VOICE_POOL,
} from "@/lib/mock";
import { splitSceneRef } from "@/lib/sceneRef";

export interface SceneData {
  id: string;
  title: string;
  grammar_profile: GrammarProfile;
  subjects: string[];
  shots: ShotSpec[];
  findings: Finding[];
}

/**
 * One row of the workspace's scene rail.
 *
 * The rail is what makes the workspace a workspace rather than a single-scene
 * page, so it needs the project's scenes BEFORE any one of them is opened. The
 * count is lines rather than shots on purpose: the story graph knows how many
 * lines a scene has in the one request that lists the scenes, whereas shot
 * counts would cost a shot-list fetch per scene to state honestly.
 */
export interface SceneSummary {
  ordinal: number;
  title: string;
  lineCount: number;
}

/** Everything the Casting Studio needs to open, in one payload (mirrors how
 * `getScene` returns a composed `SceneData`). Characters and their signals come
 * from the project's story graph; voices come from the TTS adapter catalog. */
export interface CastingData {
  projectId: string;
  title: string;
  characters: CharacterSignal[];
  voices: Voice[];
  /** The casting the studio opens with (character -> assigned voice). */
  casting: Record<string, Voice>;
  /** Pre-built casting variants so the leaderboard is demoable on arrival. */
  candidates: { label: string; casting: Record<string, Voice> }[];
  /**
   * True when `voices` is a local stub rather than a provider catalog. The API
   * publishes no voice-catalog endpoint yet (see the GAP in `httpApi.ts`), so
   * even against a live backend the pool is fixture data. The judge scores are
   * real; the *names being scored* are not, and the UI has to say so.
   */
  voicesAreStub: boolean;
}

export interface StoryEngineApi {
  /** Which judge produced this implementation's scores. Never blended: one
   * engine answers a request end to end, and the UI stamps every score. */
  readonly judgeEngine: JudgeEngine;

  /** The scenes of the project a reference points at, for the scene rail. The
   * reference is the same opaque string `getScene` takes; its scene half is
   * ignored, because the answer is a property of the project. */
  listScenes(sceneRef: string): Promise<SceneSummary[]>;
  getScene(sceneId: string): Promise<SceneData>;
  /** Persist shots and return the server's authoritative findings. */
  validateScene(sceneId: string, shots: ShotSpec[]): Promise<Finding[]>;
  setFindingDeliberate(
    sceneId: string,
    findingId: string,
    deliberate: boolean,
    note: string | null,
  ): Promise<void>;

  // --- Casting studio ---------------------------------------------------- //
  /** Load the character roster, voice pool, and starting casting for a project. */
  getCasting(projectId: string): Promise<CastingData>;
  /** Score how well each character's assigned voice fits.
   * Real: POST /api/v1/projects/{projectId}/judge/voices */
  judgeVoices(projectId: string, req: VoiceFitRequest): Promise<VoiceFitResult>;
  /** Rank several casting variants best-first.
   * Real: POST /api/v1/projects/{projectId}/judge/rank/voices */
  rankVoices(
    projectId: string,
    req: VoiceRankRequest,
  ): Promise<RankingResult<VoiceFitResult>>;
  /** Score the previz animatic quality.
   * Real: POST /api/v1/projects/{projectId}/judge/animatic */
  judgeAnimatic(projectId: string): Promise<AnimaticJudgment>;

  // --- Timeline editor --------------------------------------------------- //
  /** Load the scrubbable timeline (audio + visual lanes) for a project.
   * Real: GET /api/v1/projects/{projectId}/scenes/{ordinal}/timeline. */
  getTimeline(projectId: string): Promise<TimelineData>;

  /** Fetch the stored video clip for one shot, or `null` when none exists.
   * `null` is the ordinary case — a Veo render costs credits, so most shots
   * have never been rendered. Only a genuine failure throws.
   * Real: GET /api/v1/projects/{id}/render/video/{scene}/{shot} */
  getShotVideo(
    projectId: string,
    sceneOrdinal: number,
    shotOrdinal: number,
  ): Promise<ShotVideo | null>;

  /** Render one shot to video. Spends provider credits and can be refused by
   * the rights gate (403) or the cost governor (402).
   * Real: POST /api/v1/projects/{id}/render/video */
  renderShotVideo(
    projectId: string,
    req: VideoRenderRequest,
  ): Promise<VideoRenderResult>;

  // --- Edit assistant ---------------------------------------------------- //
  /** Ask the assistant about one scene. Answers with prose plus a batch of ops
   * already validated against that scene, so Apply is a batch that will land.
   * Spends provider credits: 402 when the cost governor refuses and 503 when no
   * LLM is configured — the 503's detail names the variables to set.
   * Real: POST /api/v1/projects/{id}/scenes/{ordinal}/assist */
  assist(
    projectId: string,
    sceneOrdinal: number,
    req: AssistRequest,
  ): Promise<AssistProposal>;

  /** Apply a batch of ops to the scene's IR — all-or-nothing, so a 422 means
   * nothing changed. This is the same endpoint the timeline editor uses; the
   * assistant proposes, this applies.
   * Real: POST /api/v1/projects/{id}/scenes/{ordinal}/timeline/edits */
  applyTimelineEdits(
    projectId: string,
    sceneOrdinal: number,
    edits: TimelineEditOp[],
  ): Promise<EditsApplied>;
}

class MockApi implements StoryEngineApi {
  readonly judgeEngine: JudgeEngine = "local-heuristic";

  /** The fixture behind a scene reference, or a refusal in the same shape the
   * live API gives: a scene that was never authored is not silently replaced
   * with the first one, because the rail would then lie about what it opened. */
  private fixture(sceneRef: string): MockSceneFixture {
    const { ordinal } = splitSceneRef(sceneRef || MOCK_SCENE_ID);
    const found = MOCK_SCENE_FIXTURES.find((f) => f.ordinal === ordinal);
    if (!found) {
      throw new Error(
        `Scene ${ordinal} is not authored in the fixture project. ` +
          `Mock mode ships scenes ${MOCK_SCENE_FIXTURES.map((f) => f.ordinal).join(", ")}.`,
      );
    }
    return found;
  }

  async listScenes(): Promise<SceneSummary[]> {
    return MOCK_SCENE_FIXTURES.map((f) => ({
      ordinal: f.ordinal,
      title: f.title,
      lineCount: f.timeline.lanes.dialogue.length,
    }));
  }

  async getScene(sceneId: string): Promise<SceneData> {
    const f = this.fixture(sceneId);
    return {
      id: sceneId || MOCK_SCENE_ID,
      title: f.title,
      grammar_profile: f.grammarProfile,
      subjects: [...f.subjects],
      shots: f.shots.map((s) => ({ ...s, subjects: [...s.subjects], covers_lines: [...s.covers_lines] })),
      findings: f.findings.map((x) => ({ ...x })),
    };
  }

  async validateScene(): Promise<Finding[]> {
    // The real implementation POSTs shots and returns server findings.
    // The mock keeps the optimistic client findings authoritative.
    return [];
  }

  async setFindingDeliberate(): Promise<void> {
    // No-op in the mock; the store updates its own state optimistically.
  }

  // --- Casting studio ---------------------------------------------------- //
  async getCasting(projectId: string): Promise<CastingData> {
    // Real: GET the project's story graph (for characters/signals) and the TTS
    // adapter's voice catalog. Here it's all served from fixtures.
    return {
      projectId: projectId || MOCK_PROJECT_ID,
      title: MOCK_CASTING_TITLE,
      characters: MOCK_CHARACTER_SIGNALS.map((c) => ({
        ...c,
        emotions: { ...c.emotions },
      })),
      voices: MOCK_VOICE_POOL.map((v) => ({ ...v, tags: [...v.tags] })),
      casting: castingFromIds(MOCK_DEFAULT_CASTING),
      candidates: MOCK_CANDIDATE_CASTINGS.map((c) => ({
        label: c.label,
        casting: castingFromIds(c.ids),
      })),
      voicesAreStub: true,
    };
  }

  async judgeVoices(
    _projectId: string,
    req: VoiceFitRequest,
  ): Promise<VoiceFitResult> {
    // Real: POST {casting, available_voices} to .../judge/voices and return the
    // server's VoiceFitResult. The mock runs the same deterministic heuristic
    // client-side (see lib/judge.ts) so scores respond to every reassignment.
    //
    // The backend declares `casting: dict[str, Voice] = Field(min_length=1)`,
    // so an empty casting is a 422 there. Fail the same way here rather than
    // letting the mock succeed where the real API cannot.
    if (Object.keys(req.casting).length === 0) {
      throw new Error("Cast at least one character before judging.");
    }
    return judgeCasting(
      MOCK_CHARACTER_SIGNALS,
      req.casting,
      req.available_voices ?? MOCK_VOICE_POOL,
    );
  }

  async rankVoices(
    _projectId: string,
    req: VoiceRankRequest,
  ): Promise<RankingResult<VoiceFitResult>> {
    // Real: POST {candidates, available_voices} to .../judge/rank/voices. The
    // mock judges each candidate and assembles the same best-first leaderboard
    // the backend does: sort by overall score desc, ties broken by label asc.
    if (req.candidates.length === 0) {
      throw new Error("Snapshot at least one take before ranking.");
    }
    const pool = req.available_voices ?? MOCK_VOICE_POOL;
    const scored = req.candidates.map((c) => ({
      label: c.label,
      result: judgeCasting(MOCK_CHARACTER_SIGNALS, c.casting, pool),
    }));
    scored.sort(
      (a, b) =>
        b.result.overall_score - a.result.overall_score ||
        a.label.localeCompare(b.label),
    );
    const entries = scored.map((s, i) => ({
      label: s.label,
      rank: i + 1,
      overall_score: s.result.overall_score,
      result: s.result,
    }));
    return { winner: entries.length ? entries[0].label : null, entries };
  }

  async judgeAnimatic(): Promise<AnimaticJudgment> {
    // Real: POST .../judge/animatic (reads the project's stored shot lists).
    return MOCK_ANIMATIC_JUDGMENT;
  }

  // --- Timeline editor --------------------------------------------------- //
  async getTimeline(projectId: string): Promise<TimelineData> {
    // Real: GET .../scenes/{ordinal}/timeline joined with the scene's shot list.
    // Here it's served from a fixture, deep-cloned so the timeline store's edits
    // never touch the fixture itself.
    const t = this.fixture(projectId).timeline;
    return {
      projectId: projectId || t.projectId,
      sceneOrdinal: t.sceneOrdinal,
      timingSource: t.timingSource,
      sceneTitle: t.sceneTitle,
      durationMs: t.durationMs,
      scenes: t.scenes.map((s) => ({ ...s })),
      lanes: {
        visual: t.lanes.visual.map((c) => ({ ...c, subjects: [...c.subjects] })),
        dialogue: t.lanes.dialogue.map((c) => ({ ...c })),
        ambience: t.lanes.ambience.map((b) => ({ ...b })),
        sfx: t.lanes.sfx.map((m) => ({ ...m })),
      },
    };
  }

  /** No fixture ships a video file, and inventing a playable clip would be the
   * exact fabrication this app is trying to avoid. So the mock reports the
   * truth: nothing is rendered. That is also the state the editor is designed
   * around, so the empty case is what local development exercises. */
  async getShotVideo(): Promise<ShotVideo | null> {
    return null;
  }

  /** Rendering needs a provider, and the mock has none. Refuse clearly instead
   * of pretending a clip exists — this drives the editor's real error state. */
  async renderShotVideo(
    _projectId: string,
    req: VideoRenderRequest,
  ): Promise<VideoRenderResult> {
    await new Promise((r) => setTimeout(r, 900));
    throw new Error(
      `Mock mode has no video provider, so shot ${req.shot_ordinal} cannot be ` +
        "rendered. Point the app at a live backend (NEXT_PUBLIC_USE_MOCK_API=false) " +
        "to render with Veo.",
    );
  }

  // --- Edit assistant ---------------------------------------------------- //

  /**
   * The mock has no model, so it says so. It must NEVER answer with a canned
   * reply and a hand-written op batch: a viewer who cannot tell a fixture from
   * an agent would be looking at a fabricated demo of the one feature whose
   * whole claim is that a real model proposed a real edit. The panel checks
   * `API_MODE` and shows the unconfigured state rather than calling this, so
   * reaching here at all is a bug — hence a plain, loud refusal.
   */
  async assist(): Promise<AssistProposal> {
    throw new Error(
      "Mock mode has no LLM, so the edit assistant is unavailable. Point the " +
        "app at a live backend (NEXT_PUBLIC_USE_MOCK_API=false) with " +
        "GOOGLE_CLOUD_PROJECT and GOOGLE_APPLICATION_CREDENTIALS configured.",
    );
  }

  /** Nothing can be proposed in mock mode, so nothing can be applied; the
   * store's local AI-assist strip is the mock's editing affordance. */
  async applyTimelineEdits(): Promise<EditsApplied> {
    throw new Error(
      "Mock mode holds the timeline in the browser, so IR edits cannot be " +
        "applied. Point the app at a live backend to edit the story graph.",
    );
  }
}

// --------------------------------------------------------------------------- //
// Mock vs live
//
// The old default was "mock unless told otherwise", which meant a deployment
// that forgot one env var served fabricated numbers that looked authoritative.
// The default is now decided by the build itself:
//
//   NEXT_PUBLIC_USE_MOCK_API=true  -> mock   (explicit opt-in, any environment)
//   NEXT_PUBLIC_USE_MOCK_API=false -> live   (explicit opt-out, any environment)
//   unset, `next dev`              -> mock   (no backend needed to hack on UI)
//   unset, `next build`/`start`    -> LIVE   (a shipped build never invents data)
//
// Forgetting the variable can no longer ship fake data; only writing it can,
// and then `API_MODE_NOTICE` says so loudly on screen. Both reads below are
// full literals so Next inlines them at build time (dynamic lookups are not
// inlined — see next/dist/docs/01-app/02-guides/environment-variables.md).
// --------------------------------------------------------------------------- //

export type ApiMode = "mock" | "live";

const TRUTHY = new Set(["true", "1", "yes", "on"]);
const FALSY = new Set(["false", "0", "no", "off"]);

const RAW_MOCK_FLAG = (process.env.NEXT_PUBLIC_USE_MOCK_API ?? "").trim().toLowerCase();
const IS_PRODUCTION_BUILD = process.env.NODE_ENV === "production";

function resolveMode(): { mode: ApiMode; reason: string } {
  if (TRUTHY.has(RAW_MOCK_FLAG)) {
    return {
      mode: "mock",
      reason: "NEXT_PUBLIC_USE_MOCK_API is set to a true value.",
    };
  }
  if (FALSY.has(RAW_MOCK_FLAG)) {
    return {
      mode: "live",
      reason: "NEXT_PUBLIC_USE_MOCK_API is set to a false value.",
    };
  }
  if (RAW_MOCK_FLAG !== "") {
    // A typo ("mock", "yes please", …) must not silently mean "mock".
    return {
      mode: IS_PRODUCTION_BUILD ? "live" : "mock",
      reason: `NEXT_PUBLIC_USE_MOCK_API="${RAW_MOCK_FLAG}" is not a boolean; falling back to the ${
        IS_PRODUCTION_BUILD ? "production" : "development"
      } default.`,
    };
  }
  return IS_PRODUCTION_BUILD
    ? { mode: "live", reason: "Production build with no override: live API." }
    : { mode: "mock", reason: "Development build with no override: mock data." };
}

const RESOLVED = resolveMode();

/** Which implementation `api` is. Render it — do not let a viewer guess. */
export const API_MODE: ApiMode = RESOLVED.mode;

/** Why that mode was chosen; shown in the mode badge's tooltip. */
export const API_MODE_REASON: string = RESOLVED.reason;

/** Set when the running build is serving fabricated data — a deployed build
 * that was deliberately switched to the mock. The UI shows this at full volume. */
export const API_MODE_IS_UNSAFE_DEPLOY: boolean =
  API_MODE === "mock" && IS_PRODUCTION_BUILD;

/** Retained for callers that only need the boolean. */
export const USE_MOCK_API: boolean = API_MODE === "mock";

export const api: StoryEngineApi =
  API_MODE === "mock" ? new MockApi() : new HttpApi();
