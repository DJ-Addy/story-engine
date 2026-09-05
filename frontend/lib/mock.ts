// Mock scene: "The Gilded Tankard" — a two-character tavern dialogue.
// Includes one deliberate axis cross (shot 6) and one lens jump (shot 8)
// so the continuity panel has real findings to show.

import type {
  AnimaticJudgment,
  DialogueClip,
  Emotion,
  Finding,
  GrammarProfile,
  ShotSpec,
  TimelineData,
  VisualClip,
  Voice,
} from "@/lib/types";
import type { CharacterSignal } from "@/lib/judge";

export const MOCK_SCENE_ID = "demo";
export const MOCK_SCENE_TITLE = "INT. THE GILDED TANKARD — NIGHT";
export const MOCK_GRAMMAR_PROFILE: GrammarProfile = "classical";
export const MOCK_SUBJECTS = ["Mara", "Voss"];

export const MOCK_SHOTS: ShotSpec[] = [
  {
    ordinal: 1,
    size: "ws",
    subjects: ["Mara", "Voss"],
    axis_side: "neutral",
    lens_mm: 24,
    camera_height: "eye",
    movement: "dolly",
    eyeline: "none",
    covers_lines: [1, 2],
    intent: "Establish tavern geography; Mara enters, Voss waits at corner table",
  },
  {
    ordinal: 2,
    size: "ms",
    subjects: ["Mara", "Voss"],
    axis_side: "a",
    lens_mm: 35,
    camera_height: "eye",
    movement: "static",
    eyeline: "none",
    covers_lines: [3, 4, 5],
    intent: "Two-shot over the table; establish the axis as Mara sits",
  },
  {
    ordinal: 3,
    size: "mcu",
    subjects: ["Mara"],
    axis_side: "a",
    lens_mm: 50,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [6, 7],
    intent: "Mara probes about the missing ledger; guarded delivery",
  },
  {
    ordinal: 4,
    size: "mcu",
    subjects: ["Voss"],
    axis_side: "a",
    lens_mm: 50,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [8, 9],
    intent: "Voss deflects; matching single to keep coverage symmetrical",
  },
  {
    ordinal: 5,
    size: "insert",
    subjects: ["Voss"],
    axis_side: "neutral",
    lens_mm: 85,
    camera_height: "high",
    movement: "static",
    eyeline: "none",
    covers_lines: [10],
    intent: "Insert: Voss slides a brass key across the table",
  },
  {
    ordinal: 6,
    size: "cu",
    subjects: ["Mara"],
    axis_side: "b",
    lens_mm: 65,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [11, 12],
    intent: "Deliberate axis cross as the power dynamic flips to Mara",
  },
  {
    ordinal: 7,
    size: "cu",
    subjects: ["Voss"],
    axis_side: "b",
    lens_mm: 65,
    camera_height: "low",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [13, 14],
    intent: "Voss cornered; low angle sells his shrinking leverage",
  },
  {
    ordinal: 8,
    size: "cu",
    subjects: ["Mara"],
    axis_side: "b",
    lens_mm: 135,
    camera_height: "eye",
    movement: "static",
    eyeline: "off_axis",
    covers_lines: [15],
    intent: "Long-lens compression jump on Mara's ultimatum line",
  },
  {
    ordinal: 9,
    size: "pov",
    subjects: ["Mara"],
    axis_side: "neutral",
    lens_mm: 40,
    camera_height: "eye",
    movement: "handheld",
    eyeline: "to_camera",
    covers_lines: [16],
    intent: "Mara's POV: Voss's hand hesitating over the key",
  },
  {
    ordinal: 10,
    size: "mws",
    subjects: ["Mara", "Voss"],
    axis_side: "b",
    lens_mm: 32,
    camera_height: "eye",
    movement: "track",
    eyeline: "none",
    covers_lines: [17, 18],
    intent: "Resolve wide on the new axis side; Mara takes the key and exits",
  },
];

// --------------------------------------------------------------------------- //
// Casting Studio fixtures
// --------------------------------------------------------------------------- //
// The Gilded Tankard, cast as an immersive multi-voice audiobook. The character
// signals below stand in for what the backend derives by walking the story IR
// (line counts + the delivery-emotion distribution per speaker); the voice pool
// is seeded from the curated ElevenLabs catalog (backend/app/adapters/
// elevenlabs.py) plus a few more classic voices so suggestions have range.

export const MOCK_PROJECT_ID = "demo";
export const MOCK_CASTING_TITLE = "THE GILDED TANKARD — audiobook cast";

/** Available-voices pool. Ids/names/tags mirror the ElevenLabs adapter shape. */
export const MOCK_VOICE_POOL: Voice[] = [
  { id: "21m00Tcm4TlvDq8ikWAM", name: "Rachel", tags: ["female", "narrator", "calm", "american"] },
  { id: "pNInz6obpgDQGcFmaJgB", name: "Adam", tags: ["male", "narrator", "deep", "american"] },
  { id: "TxGEqnHWrfWFTfGW9XjX", name: "Josh", tags: ["male", "narrator", "deep", "american"] },
  { id: "ErXwobaYiN019PkySvjV", name: "Antoni", tags: ["male", "conversational", "warm", "american"] },
  { id: "EXAVITQu4vr4xnSDxMaL", name: "Bella", tags: ["female", "conversational", "soft", "american"] },
  { id: "MF3mGyEYCl7XYWbV9V6O", name: "Elli", tags: ["female", "young", "soft", "american"] },
  { id: "XB0fDUnXU5powFXDhCwa", name: "Charlotte", tags: ["female", "gentle", "calm", "american"] },
  { id: "AZnzlk1XvdvUeBnXmlld", name: "Domi", tags: ["female", "expressive", "strong", "american"] },
  { id: "VR6AewLTigWG4xSOukaG", name: "Arnold", tags: ["male", "strong", "energetic", "american"] },
  { id: "yoZ06aMxZJJ28mfd3POQ", name: "Sam", tags: ["male", "conversational", "young", "american"] },
];

const VOICE_BY_ID = new Map(MOCK_VOICE_POOL.map((v) => [v.id, v]));

/** Resolve a `character -> voice_id` map into the `character -> Voice` casting
 * shape the judge/rank requests expect. */
export function castingFromIds(ids: Record<string, string>): Record<string, Voice> {
  const out: Record<string, Voice> = {};
  for (const [character, voiceId] of Object.entries(ids)) {
    const voice = VOICE_BY_ID.get(voiceId);
    if (voice) out[character] = voice;
  }
  return out;
}

/** Per-character IR signals for the casting scene. */
export const MOCK_CHARACTER_SIGNALS: CharacterSignal[] = [
  {
    name: "NARRATOR",
    dialogue_lines: 0,
    narration_lines: 30,
    emotions: { calm: 12, serious: 8, sad: 2 },
  },
  {
    name: "MARA",
    dialogue_lines: 24,
    narration_lines: 0,
    emotions: { serious: 8, urgent: 6, angry: 4, calm: 3, sad: 3 },
  },
  {
    name: "VOSS",
    dialogue_lines: 20,
    narration_lines: 0,
    emotions: { angry: 8, shouting: 6, sarcastic: 3, serious: 3 },
  },
  {
    name: "LYRA",
    dialogue_lines: 14,
    narration_lines: 0,
    emotions: { sad: 5, afraid: 4, whispering: 3, calm: 2 },
  },
  {
    name: "BARKEEP",
    dialogue_lines: 6,
    narration_lines: 0,
    emotions: { happy: 3, calm: 2, serious: 1 },
  },
];

/** The casting the studio opens with — deliberately imperfect so the judge has
 * concrete findings to surface (soft voice on the shouty antagonist, a
 * non-narrator voice on the narrator, a hot voice on the timid character). */
export const MOCK_DEFAULT_CASTING: Record<string, string> = {
  NARRATOR: "AZnzlk1XvdvUeBnXmlld", // Domi — expressive, not a narrator
  MARA: "pNInz6obpgDQGcFmaJgB", // Adam — deep narrator (good fit)
  VOSS: "EXAVITQu4vr4xnSDxMaL", // Bella — soft (too soft for a shouty role)
  LYRA: "VR6AewLTigWG4xSOukaG", // Arnold — energetic (too hot for a timid role)
  BARKEEP: "ErXwobaYiN019PkySvjV", // Antoni — warm (good fit)
};

/** A couple of pre-built casting variants so the leaderboard can be ranked on
 * arrival; the user can snapshot more from the editor. */
export const MOCK_CANDIDATE_CASTINGS: { label: string; ids: Record<string, string> }[] = [
  { label: "First pass", ids: MOCK_DEFAULT_CASTING },
  {
    label: "Narrator-forward",
    ids: {
      NARRATOR: "21m00Tcm4TlvDq8ikWAM", // Rachel — calm narrator
      MARA: "pNInz6obpgDQGcFmaJgB", // Adam
      VOSS: "AZnzlk1XvdvUeBnXmlld", // Domi — strong/expressive
      LYRA: "MF3mGyEYCl7XYWbV9V6O", // Elli — young/soft
      BARKEEP: "ErXwobaYiN019PkySvjV", // Antoni
    },
  },
];

/** Static animatic-quality judgment for the optional previz judge panel. */
export const MOCK_ANIMATIC_JUDGMENT: AnimaticJudgment = {
  overall_score: 0.72,
  rationale:
    "Judged 2 scene(s) with shot lists; overall animatic quality 0.72. Coverage and continuity are strong; shot-size variety in scene 1 is thin.",
  coverage_score: 0.81,
  continuity_score: 0.78,
  variety_score: 0.58,
  pacing_score: 0.7,
  scenes: [
    {
      scene_ordinal: 1,
      score: 0.68,
      coverage_score: 0.8,
      continuity_score: 0.74,
      variety_score: 0.5,
      pacing_score: 0.68,
      shot_count: 10,
      findings: [
        {
          code: "SIZE_MONOTONY",
          severity: "warn",
          message: "Shots 6-8 are all close-ups; vary shot size to keep the exchange from feeling flat.",
          scene_ordinal: 1,
          shot_ordinal: 7,
        },
        {
          code: "AXIS_CROSS",
          severity: "info",
          message: "Shot 6 crosses the action axis; marked deliberate upstream.",
          scene_ordinal: 1,
          shot_ordinal: 6,
        },
      ],
    },
    {
      scene_ordinal: 2,
      score: 0.76,
      coverage_score: 0.82,
      continuity_score: 0.82,
      variety_score: 0.66,
      pacing_score: 0.72,
      shot_count: 8,
      findings: [
        {
          code: "PACING_LONG_TAKE",
          severity: "info",
          message: "Shot 3 runs long; consider an insert to break up the dialogue.",
          scene_ordinal: 2,
          shot_ordinal: 3,
        },
      ],
    },
  ],
  findings: [
    {
      code: "VARIETY_LOW",
      severity: "warn",
      message: "Overall shot-size variety (0.58) is below the 0.65 target for the classical profile.",
      scene_ordinal: null,
      shot_ordinal: null,
    },
  ],
};

// What the backend validator returned for this scene. The AXIS_CROSS on shot 6
// has already been marked deliberate upstream; the LENS_JUMP has not.
export const MOCK_SERVER_FINDINGS: Finding[] = [
  {
    id: "srv-f-001",
    rule_code: "AXIS_CROSS",
    severity: "warn",
    message:
      "Shot 6 crosses the action axis (side 'b' after shot 4 on side 'a'). Screen direction will flip.",
    shot_ordinal: 6,
    deliberate: true,
    deliberate_note: "Intentional flip: power dynamic reverses on Mara's line 11.",
  },
  {
    id: "srv-f-002",
    rule_code: "LENS_JUMP",
    severity: "info",
    message:
      "Shot 8 jumps 70mm (65mm to 135mm) at the same size ('cu') as shot 7. Perspective will shift noticeably.",
    shot_ordinal: 8,
    deliberate: false,
    deliberate_note: null,
  },
];

// --------------------------------------------------------------------------- //
// Timeline editor fixture
// --------------------------------------------------------------------------- //
// The Gilded Tankard as a scrubbable multi-lane timeline. The VISUAL lane is
// derived 1:1 from MOCK_SHOTS (same ordinals/sizes/subjects/intent) so the
// timeline lines up with /scenes/demo and /casting; the DIALOGUE lane places
// each spoken line inside the span of the shot that covers it. Timings are
// authored below; when real media is wired the clip spans come from the
// rendered WAV + shot list (see api.getTimeline).

/** Authored [startMs, durationMs] for each shot ordinal on the visual track. */
const VISUAL_SPANS: Record<number, [number, number]> = {
  1: [0, 6000],
  2: [6000, 9000],
  3: [15000, 6500],
  4: [21500, 6500],
  5: [28000, 3500],
  6: [31500, 6000],
  7: [37500, 6000],
  8: [43500, 4500],
  9: [48000, 4000],
  10: [52000, 8000],
};

const TIMELINE_VISUAL: VisualClip[] = MOCK_SHOTS.map((s) => {
  const [startMs, durationMs] = VISUAL_SPANS[s.ordinal];
  return {
    id: `vis-${s.ordinal}`,
    startMs,
    durationMs,
    shotOrdinal: s.ordinal,
    size: s.size,
    label: s.intent,
    subjects: [...s.subjects],
  };
});

/** One dialogue line and the shot ordinal whose span it plays under. */
interface DialogueSpec {
  line: number;
  shot: number;
  character: string;
  emotion: Emotion;
  text: string;
}

const DIALOGUE_SPECS: DialogueSpec[] = [
  { line: 1, shot: 1, character: "NARRATOR", emotion: "calm", text: "Fog pooled at the tavern door as Mara stepped inside." },
  { line: 2, shot: 1, character: "MARA", emotion: "serious", text: "You're a hard man to find, Voss." },
  { line: 3, shot: 2, character: "VOSS", emotion: "sarcastic", text: "And yet here you are, ruining my evening." },
  { line: 4, shot: 2, character: "MARA", emotion: "serious", text: "The ledger. Where is it?" },
  { line: 5, shot: 2, character: "VOSS", emotion: "calm", text: "Straight to business — I always liked that about you." },
  { line: 6, shot: 3, character: "MARA", emotion: "urgent", text: "People are dead over what's in those pages." },
  { line: 7, shot: 3, character: "MARA", emotion: "angry", text: "Don't make me ask twice." },
  { line: 8, shot: 4, character: "VOSS", emotion: "sarcastic", text: "Threats? In my own tavern?" },
  { line: 9, shot: 4, character: "VOSS", emotion: "serious", text: "You wound me, Mara." },
  { line: 10, shot: 5, character: "VOSS", emotion: "whispering", text: "Take it — before I change my mind." },
  { line: 11, shot: 6, character: "MARA", emotion: "serious", text: "A key. To what?" },
  { line: 12, shot: 6, character: "MARA", emotion: "urgent", text: "Voss. To what?" },
  { line: 13, shot: 7, character: "VOSS", emotion: "afraid", text: "The cellar under the old mill." },
  { line: 14, shot: 7, character: "VOSS", emotion: "whispering", text: "But they'll be watching it." },
  { line: 15, shot: 8, character: "MARA", emotion: "shouting", text: "Then they'll see me coming." },
  { line: 16, shot: 9, character: "NARRATOR", emotion: "serious", text: "Voss's hand hovered over the brass, unwilling to let go." },
  { line: 17, shot: 10, character: "MARA", emotion: "calm", text: "Pleasure doing business." },
  { line: 18, shot: 10, character: "NARRATOR", emotion: "calm", text: "And then she was gone, swallowed by the fog." },
];

/** ~6% breathing gap after each line before the next in the same shot. */
const DIALOGUE_GAP = 0.06;

function buildDialogue(specs: DialogueSpec[]): DialogueClip[] {
  const byShot = new Map<number, DialogueSpec[]>();
  for (const spec of specs) {
    const list = byShot.get(spec.shot) ?? [];
    list.push(spec);
    byShot.set(spec.shot, list);
  }
  const clips: DialogueClip[] = [];
  for (const [shot, lines] of byShot) {
    const [start, duration] = VISUAL_SPANS[shot];
    const slot = duration / lines.length;
    lines.forEach((spec, i) => {
      clips.push({
        id: `dlg-${spec.line}`,
        startMs: Math.round(start + i * slot),
        durationMs: Math.round(slot * (1 - DIALOGUE_GAP)),
        character: spec.character,
        emotion: spec.emotion,
        text: spec.text,
      });
    });
  }
  return clips.sort((a, b) => a.startMs - b.startMs);
}

export const MOCK_TIMELINE: TimelineData = {
  projectId: MOCK_PROJECT_ID,
  // `normalize.py` numbers scenes 1-based (ordinal 0 is the pre-slugline
  // preamble), so the demo scene is 1 — the same scene `parseSceneRef` resolves
  // a bare project id to in httpApi.ts.
  sceneOrdinal: 1,
  sceneTitle: MOCK_SCENE_TITLE,
  durationMs: 60000,
  scenes: [
    { startMs: 0, label: "Entrance" },
    { startMs: 15000, label: "The Ask" },
    { startMs: 31500, label: "Power Flips" },
    { startMs: 48000, label: "Resolve" },
  ],
  lanes: {
    visual: TIMELINE_VISUAL,
    dialogue: buildDialogue(DIALOGUE_SPECS),
    ambience: [
      { id: "amb-room", tag: "tavern room tone", startMs: 0, durationMs: 60000 },
      { id: "amb-hearth", tag: "hearth fire", startMs: 0, durationMs: 46000 },
      { id: "amb-rain", tag: "rain, exterior", startMs: 43500, durationMs: 16500 },
    ],
    sfx: [
      { id: "sfx-door-in", atMs: 1200, name: "door creak" },
      { id: "sfx-tankard", atMs: 6200, name: "tankard set down" },
      { id: "sfx-key", atMs: 28800, name: "brass key slides" },
      { id: "sfx-chair", atMs: 31700, name: "chair scrape" },
      { id: "sfx-coin", atMs: 48600, name: "coin clink" },
      { id: "sfx-door-out", atMs: 53200, name: "door & night wind" },
    ],
  },
};
