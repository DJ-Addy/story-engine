// Client-side mirror of the backend voice-fit judge (backend/app/judge/voices.py).
//
// The backend judge is a deterministic heuristic over the story IR and the
// assigned voices' tags — no network, no credits. Porting it here lets the
// MockApi score any casting the user assembles, so the Casting Studio is fully
// interactive with NO backend running (the same reason `lib/continuity.ts`
// mirrors the continuity validator for optimistic shot feedback). When the real
// FastAPI client is wired in, these functions become dead code — the server
// returns the authoritative `VoiceFitResult` — but the shapes stay identical.

import type {
  CharacterVoiceFit,
  Voice,
  VoiceFinding,
  VoiceFitResult,
  VoiceSuggestion,
} from "@/lib/types";

/**
 * A character's signals derived from the IR: how much they speak, how much they
 * narrate, and the distribution of delivery emotions across their lines. The
 * backend computes this by walking every scene/line (`_CharacterSignals`); the
 * mock ships it as a fixture (see `lib/mock.ts`).
 */
export interface CharacterSignal {
  name: string;
  dialogue_lines: number;
  narration_lines: number;
  /** Canonical emotion label -> count across the character's lines. */
  emotions: Record<string, number>;
}

// --- emotion -> need axes (mirrors backend/app/judge/voices.py) ------------ //
const HIGH_AROUSAL = new Set(["angry", "shouting", "excited", "afraid", "surprised", "urgent"]);
const LOW_AROUSAL = new Set(["calm", "sad", "serious", "whispering"]);
// The remainder ({"happy", "sarcastic"}) sits mid-arousal.
const WARM = new Set(["happy", "calm", "excited"]);
const COLD = new Set(["angry", "shouting", "serious", "sarcastic"]);

// --- voice tag -> axis lookups --------------------------------------------- //
const TAG_AROUSAL: Record<string, number> = {
  expressive: 0.85,
  energetic: 0.85,
  strong: 0.8,
  styles: 0.75,
  young: 0.6,
  conversational: 0.5,
  neutral: 0.5,
  deep: 0.45,
  narrator: 0.45,
  narration: 0.45,
  warm: 0.4,
  soft: 0.25,
  calm: 0.2,
  gentle: 0.2,
};
const TAG_WARMTH: Record<string, number> = {
  warm: 0.9,
  gentle: 0.85,
  soft: 0.8,
  calm: 0.75,
  young: 0.6,
  conversational: 0.6,
  narrator: 0.55,
  narration: 0.55,
  deep: 0.5,
  neutral: 0.5,
  styles: 0.5,
  energetic: 0.5,
  expressive: 0.45,
  strong: 0.35,
};
const NARRATOR_TAGS = new Set(["narrator", "narration"]);

const DEFAULT_AXIS = 0.5;

// Findings fire when a need and the voice's axis diverge past these gates.
const AROUSAL_HIGH_GATE = 0.6;
const AROUSAL_LOW_GATE = 0.4;
const VOICE_SOFT_GATE = 0.45;
const VOICE_LOUD_GATE = 0.7;
const WARMTH_MISMATCH_GATE = 0.5;
const NARRATION_GATE = 0.5; // share of a character's lines that are narration

// A suggestion is only offered if it beats the current fit by this margin.
const SUGGESTION_MARGIN = 0.08;
const MAX_SUGGESTIONS = 3;

const clamp = (v: number) => Math.max(0, Math.min(1, v));
const round3 = (v: number) => Math.round(clamp(v) * 1000) / 1000;
const pct = (v: number) => Math.round(v * 100);

// --------------------------------------------------------------------------- //
// IR-derived character signals
// --------------------------------------------------------------------------- //
export function totalLines(sig: CharacterSignal): number {
  return sig.dialogue_lines + sig.narration_lines;
}

function emotionTotal(sig: CharacterSignal): number {
  return Object.values(sig.emotions).reduce((a, b) => a + b, 0);
}

function sumEmotions(sig: CharacterSignal, labels: Set<string>): number {
  let total = 0;
  for (const [label, count] of Object.entries(sig.emotions)) {
    if (labels.has(label)) total += count;
  }
  return total;
}

export function dominantEmotions(sig: CharacterSignal, top = 3): string[] {
  return Object.entries(sig.emotions)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, top)
    .map(([label]) => label);
}

export function narrationShare(sig: CharacterSignal): number {
  const total = totalLines(sig);
  return total ? sig.narration_lines / total : 0;
}

function narrates(sig: CharacterSignal): boolean {
  return narrationShare(sig) >= NARRATION_GATE || sig.name.toUpperCase().includes("NARRATOR");
}

/** (arousal_need, warmth_need) from the emotion distribution. */
export function needAxes(sig: CharacterSignal): [number, number] {
  const total = emotionTotal(sig);
  if (total === 0) return [DEFAULT_AXIS, DEFAULT_AXIS];
  const high = sumEmotions(sig, HIGH_AROUSAL);
  const low = sumEmotions(sig, LOW_AROUSAL);
  const mid = total - high - low;
  const arousal = (high + 0.5 * mid) / total;
  const warm = sumEmotions(sig, WARM);
  const cold = sumEmotions(sig, COLD);
  const neutral = total - warm - cold;
  const warmth = (warm + 0.5 * neutral) / total;
  return [arousal, warmth];
}

function highArousalShare(sig: CharacterSignal): number {
  const total = emotionTotal(sig);
  return total ? sumEmotions(sig, HIGH_AROUSAL) / total : 0;
}

const emptySignal = (name: string): CharacterSignal => ({
  name,
  dialogue_lines: 0,
  narration_lines: 0,
  emotions: {},
});

// --------------------------------------------------------------------------- //
// Voice-tag interpretation
// --------------------------------------------------------------------------- //
function axis(tags: string[], table: Record<string, number>): number {
  const values = tags.map((t) => t.toLowerCase()).filter((t) => t in table).map((t) => table[t]);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : DEFAULT_AXIS;
}

/** Map a voice's tags to (arousal, warmth). Pure. */
export function voiceAxes(voice: Voice): [number, number] {
  return [axis(voice.tags, TAG_AROUSAL), axis(voice.tags, TAG_WARMTH)];
}

function isNarratorVoice(voice: Voice): boolean {
  return voice.tags.some((t) => NARRATOR_TAGS.has(t.toLowerCase()));
}

const tagList = (voice: Voice) => (voice.tags.length ? voice.tags.join(", ") : "no tags");

// --------------------------------------------------------------------------- //
// Scoring
// --------------------------------------------------------------------------- //
function rawFit(sig: CharacterSignal, voice: Voice): number {
  const [arousalNeed, warmthNeed] = needAxes(sig);
  const [vArousal, vWarmth] = voiceAxes(voice);
  const arousalFit = 1 - Math.abs(arousalNeed - vArousal);
  const warmthFit = 1 - Math.abs(warmthNeed - vWarmth);
  let score = 0.6 * arousalFit + 0.4 * warmthFit;
  if (narrates(sig) && !isNarratorVoice(voice)) score *= 0.6;
  return clamp(score);
}

function findingsFor(sig: CharacterSignal, voice: Voice): VoiceFinding[] {
  const findings: VoiceFinding[] = [];
  const [arousalNeed, warmthNeed] = needAxes(sig);
  const [vArousal, vWarmth] = voiceAxes(voice);

  if (arousalNeed >= AROUSAL_HIGH_GATE && vArousal <= VOICE_SOFT_GATE) {
    findings.push({
      code: "AROUSAL_TOO_SOFT",
      severity: "warn",
      message:
        `${sig.name}'s lines are ${pct(highArousalShare(sig))}% high-arousal ` +
        `(angry/shouting/afraid/urgent/...), but voice '${voice.name}' reads ` +
        `soft/low-energy (${tagList(voice)}). Consider a more expressive/energetic voice.`,
    });
  } else if (arousalNeed <= AROUSAL_LOW_GATE && vArousal >= VOICE_LOUD_GATE) {
    findings.push({
      code: "AROUSAL_TOO_HOT",
      severity: "warn",
      message:
        `${sig.name} is largely calm/subdued, but voice '${voice.name}' reads ` +
        `high-energy (${tagList(voice)}). Consider a calmer voice.`,
    });
  }

  if (Math.abs(warmthNeed - vWarmth) >= WARMTH_MISMATCH_GATE) {
    const hotter = warmthNeed > vWarmth ? "warmer" : "cooler";
    findings.push({
      code: "WARMTH_MISMATCH",
      severity: "info",
      message: `${sig.name}'s delivery reads ${hotter} than voice '${voice.name}' (warmth axis mismatch).`,
    });
  }

  if (narrates(sig) && !isNarratorVoice(voice)) {
    findings.push({
      code: "NARRATOR_MISMATCH",
      severity: "warn",
      message:
        `${sig.name} narrates (${pct(narrationShare(sig))}% of their lines) but ` +
        `voice '${voice.name}' isn't tagged for narration.`,
    });
  }
  return findings;
}

function suggestionsFor(
  sig: CharacterSignal,
  assigned: Voice,
  pool: Voice[],
  current: number,
): VoiceSuggestion[] {
  const ranked = pool
    .filter((v) => v.id !== assigned.id)
    .map((v) => [v, rawFit(sig, v)] as const)
    .sort((a, b) => b[1] - a[1] || a[0].id.localeCompare(b[0].id));

  const out: VoiceSuggestion[] = [];
  for (const [voice, fit] of ranked) {
    if (fit < current + SUGGESTION_MARGIN) break;
    out.push({ voice_id: voice.id, voice_name: voice.name, score: round3(fit) });
    if (out.length >= MAX_SUGGESTIONS) break;
  }
  return out;
}

/** The score band the backend uses in its rationale text. */
export function scoreBand(score: number): "strong" | "adequate" | "poor" {
  return score >= 0.75 ? "strong" : score >= 0.55 ? "adequate" : "poor";
}

function rationaleFor(sig: CharacterSignal, voice: Voice, score: number): string {
  const band = scoreBand(score);
  let speaks: string;
  if (totalLines(sig) === 0) {
    speaks = "has no attributed lines in the IR";
  } else {
    const emotions = dominantEmotions(sig);
    const mood = emotions.length ? `mostly ${emotions.join(", ")}` : "emotionally neutral";
    speaks = `speaks ${sig.dialogue_lines} line(s), ${mood}`;
  }
  return `${sig.name} ${speaks}; voice '${voice.name}' (${tagList(voice)}) is a ${band} fit (score ${score.toFixed(2)}).`;
}

function scoreCharacter(
  sig: CharacterSignal,
  voice: Voice,
  pool: Voice[],
  totalDialogue: number,
): CharacterVoiceFit {
  const raw = rawFit(sig, voice);
  const score = round3(raw);
  return {
    character: sig.name,
    voice_id: voice.id,
    voice_name: voice.name,
    score,
    rationale: rationaleFor(sig, voice, score),
    line_count: sig.dialogue_lines,
    speaks_share: totalDialogue ? round3(sig.dialogue_lines / totalDialogue) : 0,
    dominant_emotions: dominantEmotions(sig),
    findings: findingsFor(sig, voice),
    suggestions: suggestionsFor(sig, voice, pool, raw),
  };
}

/**
 * Deterministic voice-fit judge over a casting. Mirrors `_score_casting` in the
 * backend: prominent (more-spoken) characters dominate the overall score, every
 * cast character carries a floor weight of 1, and speaking characters with no
 * voice assigned are reported as `uncast_characters`.
 */
export function judgeCasting(
  signals: CharacterSignal[],
  casting: Record<string, Voice>,
  availableVoices?: Voice[] | null,
): VoiceFitResult {
  const byName = new Map(signals.map((s) => [s.name, s]));
  const totalDialogue = signals.reduce((sum, s) => sum + s.dialogue_lines, 0);

  // The suggestion pool is the explicit available pool, else the distinct voices
  // used in the casting.
  const pool =
    availableVoices != null
      ? [...availableVoices]
      : [...new Map(Object.values(casting).map((v) => [v.id, v])).values()];

  const fits: CharacterVoiceFit[] = [];
  let weightedSum = 0;
  let weightTotal = 0;
  for (const character of Object.keys(casting).sort()) {
    const voice = casting[character];
    const sig = byName.get(character) ?? emptySignal(character);
    const fit = scoreCharacter(sig, voice, pool, totalDialogue);
    fits.push(fit);
    const weight = Math.max(1, sig.dialogue_lines);
    weightedSum += fit.score * weight;
    weightTotal += weight;
  }

  const overall = weightTotal ? round3(weightedSum / weightTotal) : 0;
  const uncast = signals
    .filter((s) => totalLines(s) > 0 && !(s.name in casting))
    .map((s) => s.name)
    .sort();
  const rationale =
    `Judged ${fits.length} cast character(s); overall casting fit ${overall.toFixed(2)}. ` +
    (uncast.length
      ? `${uncast.length} speaking character(s) have no voice assigned.`
      : "Every speaking character is cast.");

  return { overall_score: overall, rationale, characters: fits, uncast_characters: uncast };
}
