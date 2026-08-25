// Repository layer. All data access goes through this interface so the mock
// can be swapped for the FastAPI client without touching UI code.

import type {
  AnimaticJudgment,
  Finding,
  GrammarProfile,
  RankingResult,
  ShotSpec,
  TimelineData,
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
}

export interface StoryEngineApi {
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
   * Real: GET /api/v1/projects/{projectId}/render/audio for the rendered WAV,
   * plus the scene's shot list for the visual track. */
  getTimeline(projectId: string): Promise<TimelineData>;
}

class MockApi implements StoryEngineApi {
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
    };
  }

  async judgeVoices(
    _projectId: string,
    req: VoiceFitRequest,
  ): Promise<VoiceFitResult> {
    // Real: POST {casting, available_voices} to .../judge/voices and return the
    // server's VoiceFitResult. The mock runs the same deterministic heuristic
    // client-side (see lib/judge.ts) so scores respond to every reassignment.
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
    // Real: GET /api/v1/projects/{projectId}/render/audio for the rendered WAV,
    // plus the scene's shot list for the visual track. Here it's served from a
    // fixture, deep-cloned so the timeline store's edits never touch MOCK_TIMELINE.
    const t = MOCK_TIMELINE;
    return {
      projectId: projectId || t.projectId,
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
}

export const api: StoryEngineApi = new MockApi();
