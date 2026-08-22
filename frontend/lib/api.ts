// Repository layer. All data access goes through this interface so the mock
// can be swapped for the FastAPI client without touching UI code.

import type { Finding, GrammarProfile, ShotSpec } from "@/lib/types";
import {
  MOCK_GRAMMAR_PROFILE,
  MOCK_SCENE_ID,
  MOCK_SCENE_TITLE,
  MOCK_SERVER_FINDINGS,
  MOCK_SHOTS,
  MOCK_SUBJECTS,
} from "@/lib/mock";

export interface SceneData {
  id: string;
  title: string;
  grammar_profile: GrammarProfile;
  subjects: string[];
  shots: ShotSpec[];
  findings: Finding[];
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
}

export const api: StoryEngineApi = new MockApi();
