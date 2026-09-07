// The demo project, resolved at RUNTIME.
//
// The workspace, the casting studio and the analytics page all open on the
// reference "demo" — one string that stands for "whatever project this
// deployment wants a first-time visitor to land in". Turning that string into a
// real project id used to be a build-time substitution:
//
//     const DEMO_PROJECT_ID = process.env.NEXT_PUBLIC_DEMO_PROJECT_ID ?? "";
//
// which cannot work on Cloud Run. NEXT_PUBLIC_* is INLINED INTO THE BUNDLE when
// `next build` runs (next/dist/docs/01-app/02-guides/environment-variables.md);
// Cloud Run supplies its environment at container start, long after the bundle
// is frozen. So a deployed build sent the literal string "demo" to
// /projects/demo and got a 401 or a 404 — the first thing anyone clicking
// "Open the workspace" saw.
//
// The fix is to ask the server. GET /api/v1/demo is unauthenticated and answers
// with the demo project this instance actually has; POST /api/v1/demo/session
// seeds one on demand and hands back a token for it. This module is the only
// place in the frontend that knows those two URLs.
//
// Two rules hold everything here together:
//
//   1. Discovery is memoized for the page's lifetime, and CONCURRENT callers
//      share one in-flight request. getScene, getTimeline and listScenes all
//      resolve the same reference within a millisecond of each other on first
//      paint; three probes for one answer would be a self-inflicted herd.
//   2. Nothing here throws at the caller for failing to discover. A probe that
//      cannot answer falls back to the old behaviour — the reference is used
//      verbatim — so the request that follows produces the real, reportable
//      failure instead of this module inventing one.

import { request, setToken } from "@/lib/apiClient";

// --------------------------------------------------------------------------- //
// Wire shapes
// --------------------------------------------------------------------------- //

/** GET /api/v1/demo — unauthenticated; describes the demo, if there is one. */
export interface DemoStatus {
  /** The deployment is willing to run a demo at all. */
  enabled: boolean;
  /** A demo project exists right now (implies a usable project_id). */
  seeded: boolean;
  project_id: string | null;
  title: string | null;
  scene_count: number;
}

/** POST /api/v1/demo/session — unauthenticated; seeds on demand. Answers 403
 * when the deployment has the demo turned off. */
export interface DemoSession {
  access_token: string;
  token_type: string;
  project_id: string;
}

// --------------------------------------------------------------------------- //
// The reference
// --------------------------------------------------------------------------- //

/** The reference the landing CTAs, the workspace and the studio open on. */
export const DEMO_REF = "demo";

/** True for the reference that means "this deployment's demo project". An empty
 * reference means the same thing: it is what an unset ?scene= collapses to. */
export const isDemoRef = (ref: string): boolean => {
  const trimmed = ref.trim();
  return trimmed === DEMO_REF || trimmed === "";
};

/**
 * The explicit override, still honoured and still winning over discovery.
 *
 * It is read as a full literal so Next inlines it — a dynamic lookup is not
 * substituted at all. That inlining is exactly why it cannot be the only
 * mechanism, but it stays the right one for a build made *for* a known project
 * (a local `next build`, a preview pinned to a fixture project), and honouring
 * it first means no existing deployment changes behaviour.
 */
export const DEMO_PROJECT_ID_OVERRIDE = (
  process.env.NEXT_PUBLIC_DEMO_PROJECT_ID ?? ""
).trim();

// --------------------------------------------------------------------------- //
// Discovery
// --------------------------------------------------------------------------- //

/** The backend is a separate deployable and this contract is new, so the
 * response is read defensively rather than trusted field for field. A `seeded`
 * flag with no id is not seeded: the id is the entire point of the answer. */
function toStatus(wire: unknown): DemoStatus {
  const w = (wire ?? {}) as Partial<Record<keyof DemoStatus, unknown>>;
  const id = typeof w.project_id === "string" && w.project_id ? w.project_id : null;
  return {
    enabled: w.enabled === true,
    seeded: w.seeded === true && id !== null,
    project_id: id,
    title: typeof w.title === "string" && w.title ? w.title : null,
    scene_count: typeof w.scene_count === "number" ? w.scene_count : 0,
  };
}

/** The shared in-flight probe: every caller during the first round trip gets
 * this same promise rather than issuing its own request. */
let inFlight: Promise<DemoStatus> | null = null;

/** A settled answer worth keeping. Only a *seeded* status is cached: "enabled
 * but empty" is a state the visitor is about to change by pressing the button
 * on the cold-start panel, and caching it would make the retry that follows
 * read a stale "there is no project". */
let cachedStatus: DemoStatus | null = null;

/** The project a demo session was just minted for. Written by
 * `startDemoSession`, so the retry that follows it resolves immediately rather
 * than waiting on another probe. */
let sessionProjectId: string | null = null;

/**
 * GET /api/v1/demo, memoized. Rejects like any other request — callers that
 * only want a project id should use `resolveDemoProjectId`, which cannot.
 */
export function loadDemoStatus(): Promise<DemoStatus> {
  if (cachedStatus) return Promise.resolve(cachedStatus);
  if (inFlight) return inFlight;

  inFlight = request<unknown>("/demo")
    .then((wire) => {
      const status = toStatus(wire);
      if (status.seeded) cachedStatus = status;
      return status;
    })
    .finally(() => {
      // Cleared either way: a failed probe has to stay retryable, and a
      // successful one is served from `cachedStatus` without coming back here.
      inFlight = null;
    });

  return inFlight;
}

/**
 * The demo project's real id, or null when this deployment has none.
 *
 * Never throws. A discovery failure is not this caller's to report — its own
 * request is about to fail with something far more specific.
 */
export async function resolveDemoProjectId(): Promise<string | null> {
  if (DEMO_PROJECT_ID_OVERRIDE) return DEMO_PROJECT_ID_OVERRIDE;
  if (sessionProjectId) return sessionProjectId;
  try {
    const status = await loadDemoStatus();
    return status.seeded ? status.project_id : null;
  } catch {
    return null;
  }
}

/**
 * POST /api/v1/demo/session: seed the demo project if it is not there yet, and
 * sign this browser into it.
 *
 * The token goes through `setToken` — the same storage every other request
 * reads from (lib/apiClient.ts). This is not a second auth mechanism; it is the
 * existing one, handed a token the server minted without a password.
 *
 * Throws ApiError 403 when the deployment has the demo disabled, which the
 * cold-start panel reports as "there is no demo here" rather than as a fault.
 */
export async function startDemoSession(): Promise<DemoSession> {
  const wire = await request<DemoSession>("/demo/session", { method: "POST" });

  if (typeof wire?.access_token !== "string" || !wire.access_token) {
    throw new Error("The demo session endpoint returned no access token.");
  }
  if (typeof wire?.project_id !== "string" || !wire.project_id) {
    throw new Error("The demo session endpoint returned no project id.");
  }

  setToken(wire.access_token);
  sessionProjectId = wire.project_id;
  // The session response carries no title or scene count, and inventing them
  // would be exactly the fabrication this app refuses. The cached status is
  // dropped instead, so the next reader asks the server for the real thing.
  cachedStatus = null;

  return {
    access_token: wire.access_token,
    token_type: typeof wire.token_type === "string" ? wire.token_type : "bearer",
    project_id: wire.project_id,
  };
}

/** Forget everything discovered. Used by the sign-in path: a token for a real
 * account must not keep resolving "demo" to the sample project some earlier
 * demo session seeded under a different user. */
export function forgetDemoSession(): void {
  sessionProjectId = null;
  cachedStatus = null;
}
