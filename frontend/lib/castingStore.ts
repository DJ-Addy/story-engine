import { create } from "zustand";
import type {
  JudgeEngine,
  RankingResult,
  Voice,
  VoiceFitResult,
} from "@/lib/types";
import type { CharacterSignal } from "@/lib/judge";

/** A snapshot of a casting under a label — the unit the leaderboard ranks. */
export interface CandidateSnapshot {
  label: string;
  casting: Record<string, Voice>;
}

interface CastingState {
  projectId: string;
  title: string;
  characters: CharacterSignal[];
  voices: Voice[];
  /** True when `voices` is fixture data rather than a provider catalog. */
  voicesAreStub: boolean;

  /** The casting being edited: character name -> assigned voice. */
  casting: Record<string, Voice>;

  /** Last judge result, and whether the casting changed since it was produced. */
  fit: VoiceFitResult | null;
  fitStale: boolean;
  /** Which judge produced `fit` — stamped when the result lands, so the number
   * on screen is always attributable even if the client is swapped later. */
  fitEngine: JudgeEngine | null;

  candidates: CandidateSnapshot[];
  ranking: RankingResult<VoiceFitResult> | null;
  /** True when candidates changed since the last ranking (leaderboard is out of date). */
  rankingStale: boolean;
  /** Which judge produced `ranking`. */
  rankingEngine: JudgeEngine | null;

  load(data: {
    projectId: string;
    title: string;
    characters: CharacterSignal[];
    voices: Voice[];
    casting: Record<string, Voice>;
    candidates?: CandidateSnapshot[];
    voicesAreStub?: boolean;
  }): void;
  assignVoice(character: string, voice: Voice): void;
  setFit(fit: VoiceFitResult, engine: JudgeEngine): void;
  addCandidate(label: string): void;
  removeCandidate(label: string): void;
  loadCandidate(label: string): void;
  setRanking(ranking: RankingResult<VoiceFitResult>, engine: JudgeEngine): void;
}

const cloneCasting = (casting: Record<string, Voice>): Record<string, Voice> =>
  Object.fromEntries(Object.entries(casting).map(([c, v]) => [c, { ...v, tags: [...v.tags] }]));

export const useCastingStore = create<CastingState>((set, get) => ({
  projectId: "",
  title: "",
  characters: [],
  voices: [],
  voicesAreStub: false,
  casting: {},
  fit: null,
  fitStale: false,
  fitEngine: null,
  candidates: [],
  ranking: null,
  rankingStale: false,
  rankingEngine: null,

  load({
    projectId,
    title,
    characters,
    voices,
    casting,
    candidates = [],
    voicesAreStub = false,
  }) {
    set({
      projectId,
      title,
      characters,
      voices,
      voicesAreStub,
      casting,
      fit: null,
      fitStale: false,
      fitEngine: null,
      candidates,
      ranking: null,
      rankingStale: candidates.length >= 2,
      rankingEngine: null,
    });
  },

  assignVoice(character, voice) {
    const { casting, fit } = get();
    set({
      casting: { ...casting, [character]: voice },
      // Keep the last fit visible but flag it as out of date until re-judged.
      fitStale: fit !== null,
    });
  },

  setFit(fit, engine) {
    set({ fit, fitStale: false, fitEngine: engine });
  },

  addCandidate(label) {
    const { candidates, casting } = get();
    // Labels must be unique (the backend rejects duplicates); auto-suffix.
    let unique = label.trim() || "Candidate";
    let n = 2;
    const taken = new Set(candidates.map((c) => c.label));
    while (taken.has(unique)) unique = `${label.trim() || "Candidate"} (${n++})`;
    set({
      candidates: [...candidates, { label: unique, casting: cloneCasting(casting) }],
      rankingStale: true,
    });
  },

  removeCandidate(label) {
    const { candidates } = get();
    const next = candidates.filter((c) => c.label !== label);
    set({ candidates: next, rankingStale: true });
  },

  loadCandidate(label) {
    const { candidates, fit } = get();
    const snapshot = candidates.find((c) => c.label === label);
    if (!snapshot) return;
    set({ casting: cloneCasting(snapshot.casting), fitStale: fit !== null });
  },

  setRanking(ranking, engine) {
    set({ ranking, rankingStale: false, rankingEngine: engine });
  },
}));
