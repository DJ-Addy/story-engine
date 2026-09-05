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
  Finding,
  GrammarProfile,
  JudgeEngine,
  RankingResult,
  ShotSpec,
  ShotVideo,
  TimelineData,
  VideoRenderRequest,
  VideoRenderResult,
  Voice,
  VoiceFitRequest,
  VoiceFitResult,
  VoiceRankRequest,
} from "@/lib/types";
import type { CharacterSignal } from "@/lib/judge";
import { judgeCasting } from "@/lib/judge";
import {
  castingFromIds,
  MOCK_ANIMATIC_JUDGMENT,
  MOCK_CANDIDATE_CASTINGS,
  MOCK_CASTING_TITLE,
  MOCK_CHARACTER_SIGNALS,
  MOCK_DEFAULT_CASTING,
  MOCK_GRAMMAR_PROFILE,
  MOCK_PROJECT_ID,
  MOCK_SCENE_ID,
  MOCK_SCENE_TITLE,
  MOCK_SERVER_FINDINGS,
  MOCK_SHOTS,
  MOCK_SUBJECTS,
  MOCK_TIMELINE,
  MOCK_VOICE_POOL,
} from "@/lib/mock";

export interface SceneData {
  id: string;
  title: string;
  grammar_profile: GrammarProfile;
  subjects: string[];
  shots: ShotSpec[];
  findings: Finding[];
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
}

class MockApi implements StoryEngineApi {
  readonly judgeEngine: JudgeEngine = "local-heuristic";

  async getScene(sceneId: string): Promise<SceneData> {
    return {
      id: sceneId || MOCK_SCENE_ID,
      title: MOCK_SCENE_TITLE,
      grammar_profile: MOCK_GRAMMAR_PROFILE,
      subjects: [...MOCK_SUBJECTS],
      shots: MOCK_SHOTS.map((s) => ({ ...s, subjects: [...s.subjects], covers_lines: [...s.covers_lines] })),
      findings: MOCK_SERVER_FINDINGS.map((f) => ({ ...f })),
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
    // never touch MOCK_TIMELINE.
    const t = MOCK_TIMELINE;
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
