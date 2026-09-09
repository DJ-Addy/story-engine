// Project-level calls the guided pipeline and the upload page need, and the
// two raw-bytes fetches (the scene mix, a shot's storyboard frame).
//
// These live beside `StoryEngineApi` rather than inside it because none of
// them has a mock. The interface exists so the workspace can run on fixtures;
// creating a project, ingesting a manuscript, deciding a casting and rendering
// are live-only by nature — a fixture that pretended otherwise would be the
// fabrication this app refuses elsewhere — so the pages that call these check
// `API_MODE` and say "needs a live backend" instead of reaching a mock.
//
// Every path resolves the "demo" reference the same way `httpApi` does, so a
// page can be opened with no id at all and land on the seeded project.

import { API_BASE_URL, getToken, request, requestOptional } from "@/lib/apiClient";
import { isDemoRef, resolveDemoProjectId } from "@/lib/demoApi";

export async function resolveProject(ref: string): Promise<string> {
  if (!isDemoRef(ref)) return ref;
  return (await resolveDemoProjectId()) ?? ref;
}

const p = (id: string) => `/projects/${encodeURIComponent(id)}`;

// --- projects + ingest ------------------------------------------------------ //

export interface ProjectOut {
  id: string;
  title: string;
  rights_attested: boolean;
  cost_cap_cents: number;
  cost_spent_cents: number;
}

export function createProject(input: {
  title: string;
  rights_attested: boolean;
}): Promise<ProjectOut> {
  return request<ProjectOut>("/projects", { method: "POST", body: input });
}

export async function getProject(ref: string): Promise<ProjectOut> {
  return request<ProjectOut>(p(await resolveProject(ref)));
}

export interface ScriptUploadOut {
  script_id: string;
  scene_count: number;
  character_count: number;
}

/** POST .../script — .fountain / .txt (Fountain) / .fdx. */
export async function uploadScript(projectId: string, file: File): Promise<ScriptUploadOut> {
  const form = new FormData();
  form.append("file", file, file.name);
  return request<ScriptUploadOut>(`${p(projectId)}/script`, { method: "POST", form });
}

export interface NovelIngestOut extends ScriptUploadOut {
  quotes: number;
  attributed: number;
  needs_review: number;
  characters: string[];
}

/** POST .../novel — prose. The converter attributes quotes and emits Fountain,
 * so the result re-enters the same pipeline a screenplay does. */
export async function uploadNovel(
  projectId: string,
  file: File,
  opts: { title?: string; author?: string } = {},
): Promise<NovelIngestOut> {
  const form = new FormData();
  form.append("file", file, file.name);
  if (opts.title) form.append("title", opts.title);
  if (opts.author) form.append("author", opts.author);
  return request<NovelIngestOut>(`${p(projectId)}/novel`, { method: "POST", form });
}

// --- story graph ------------------------------------------------------------- //

export interface GraphSummary {
  sceneCount: number;
  characters: { name: string; lineCount: number }[];
}

interface GraphWire {
  scenes: { ordinal: number }[];
  characters: { canonical_name: string; line_count: number }[];
}

/** null when nothing has been ingested yet (404). */
export async function getGraphSummary(projectId: string): Promise<GraphSummary | null> {
  const g = await requestOptional<GraphWire>(`${p(projectId)}/graph`);
  if (!g) return null;
  return {
    // Ordinal 0 is the pre-slugline preamble, not a scene anyone renders.
    sceneCount: g.scenes.filter((s) => s.ordinal >= 1).length,
    characters: g.characters.map((c) => ({ name: c.canonical_name, lineCount: c.line_count })),
  };
}

// --- casting ----------------------------------------------------------------- //

export interface CastingEntry {
  character: string | null;
  is_narrator: boolean;
  voice_id: string;
  voice_name: string;
  tone: string | null;
  confidence: number;
  rationale: string;
}

export interface CastingProposal {
  entries: CastingEntry[];
  rationale: string;
}

/** The casting the renderer will use, or null when none has been decided. */
export async function getCasting(projectId: string): Promise<CastingProposal | null> {
  // The route answers 200 with a JSON null rather than 404: "no casting yet"
  // is a normal state, not a missing resource.
  const out = await request<CastingProposal | null>(`${p(projectId)}/judge/casting`);
  return out ?? null;
}

/** Ask the judge to read the script and decide voice + tone for every part.
 * Persists by default, so the next audio render obeys it. */
export function decideCasting(projectId: string): Promise<CastingProposal> {
  return request<CastingProposal>(`${p(projectId)}/judge/casting`, {
    method: "POST",
    body: {},
  });
}

// --- shots + agent ----------------------------------------------------------- //

interface ShotListWire {
  shots: { ordinal: number; intent: string; size: string }[];
}

export async function getShots(
  projectId: string,
  sceneOrdinal: number,
): Promise<{ ordinal: number; intent: string; size: string }[] | null> {
  const s = await requestOptional<ShotListWire>(`${p(projectId)}/scenes/${sceneOrdinal}/shots`);
  return s ? s.shots : null;
}

export interface AgentRun {
  status: string;
  error: string | null;
  model: string;
  delegations: string[];
  tool_calls: string[];
  final_text: string | null;
}

/** POST .../agent/run — the ADK coordinator delegates to the specialists. The
 * shot-list step is what the pipeline needs from it; the rest is what a judge
 * gets to watch. */
export function runAgent(projectId: string, brief: string, maxScenes = 1): Promise<AgentRun> {
  return request<AgentRun>(`${p(projectId)}/agent/run`, {
    method: "POST",
    body: { brief, max_scenes: maxScenes },
  });
}

// --- audio ------------------------------------------------------------------- //

export interface TimelineStatus {
  timingSource: "rendered" | "estimated";
  durationMs: number;
  dialogueClips: number;
  sfxEvents: number;
}

export async function getTimelineStatus(
  projectId: string,
  sceneOrdinal: number,
): Promise<TimelineStatus | null> {
  const t = await requestOptional<{
    timing_source: "rendered" | "estimated";
    duration_ms: number;
    dialogue: unknown[];
    sfx: unknown[];
  }>(`${p(projectId)}/scenes/${sceneOrdinal}/timeline`);
  if (!t) return null;
  return {
    timingSource: t.timing_source,
    durationMs: t.duration_ms,
    dialogueClips: t.dialogue.length,
    sfxEvents: t.sfx.length,
  };
}

export interface AudioRenderOut {
  scene_ordinal: number;
  duration_ms: number;
  clip_count: number;
  ambience_tags: string[];
}

/** POST .../render/audio — spends TTS credits; minutes, not seconds, because
 * the renderer backs off around a per-minute quota. */
export function renderSceneAudio(projectId: string, sceneOrdinal: number): Promise<AudioRenderOut> {
  return request<AudioRenderOut>(`${p(projectId)}/scenes/${sceneOrdinal}/render/audio`, {
    method: "POST",
  });
}

/** Fetch raw bytes behind the bearer token and hand back an object URL the
 * caller owns (and must revoke). An <audio>/<img> src cannot carry a header,
 * which is why this is not simply a URL. 404 -> null. */
async function fetchObjectUrl(path: string, accept: string): Promise<string | null> {
  const headers: Record<string, string> = { Accept: accept };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_BASE_URL}${path}`, { headers, credentials: "same-origin" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

/** The rendered scene mix as an object URL, or null when nothing is rendered. */
export function getSceneAudio(projectId: string, sceneOrdinal: number): Promise<string | null> {
  return fetchObjectUrl(`${p(projectId)}/scenes/${sceneOrdinal}/audio`, "audio/wav");
}

// --- storyboards (animatic) ---------------------------------------------------- //

export interface BoardRenderOut {
  scene_ordinal: number;
  shot_ordinal: number;
  cost_cents: number;
  provider: string;
  model: string;
}

export interface SceneBoardsOut {
  scene_ordinal: number;
  rendered: BoardRenderOut[];
  skipped: number[];
  total_cost_cents: number;
  /** Shots the provider refused, with its reason. The rest were still drawn. */
  failed: { shot_ordinal: number; detail: string }[];
}

/** Which shots of a scene already have a frame. */
export async function getRenderedBoards(
  projectId: string,
  sceneOrdinal: number,
): Promise<{ rendered: number[]; total: number }> {
  const out = await requestOptional<{ rendered: number[]; total: number }>(
    `${p(projectId)}/scenes/${sceneOrdinal}/boards`,
  );
  return out ?? { rendered: [], total: 0 };
}

/** One shot's frame as an object URL, or null. */
export function getShotBoard(
  projectId: string,
  sceneOrdinal: number,
  shotOrdinal: number,
): Promise<string | null> {
  return fetchObjectUrl(
    `${p(projectId)}/scenes/${sceneOrdinal}/shots/${shotOrdinal}/board`,
    "image/png",
  );
}

export function renderShotBoard(
  projectId: string,
  sceneOrdinal: number,
  shotOrdinal: number,
): Promise<BoardRenderOut> {
  return request<BoardRenderOut>(
    `${p(projectId)}/scenes/${sceneOrdinal}/shots/${shotOrdinal}/board`,
    { method: "POST" },
  );
}

/** Every shot in the scene that has no frame yet (all of them with `force`). */
export function renderSceneBoards(
  projectId: string,
  sceneOrdinal: number,
  force = false,
): Promise<SceneBoardsOut> {
  return request<SceneBoardsOut>(`${p(projectId)}/scenes/${sceneOrdinal}/boards`, {
    method: "POST",
    body: { force },
  });
}

// --- analytics ----------------------------------------------------------------- //

export interface AnalyticsStatus {
  configured: boolean;
  reachable: boolean;
  detail: string | null;
  tables_present: string[];
}

export function getAnalyticsStatus(projectId: string): Promise<AnalyticsStatus> {
  return request<AnalyticsStatus>(`${p(projectId)}/analytics/status`);
}
