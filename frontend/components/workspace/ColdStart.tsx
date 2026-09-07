"use client";

// The first thing anyone sees, when there is nothing to see yet.
//
// Every landing CTA points at /workspace, and the workspace opens on the "demo"
// reference. On a freshly deployed instance that reference resolves to a
// project that does not exist yet, and the visitor holds no token — so the
// scene and the timeline both came back 401 (or 404), and the page they were
// promised rendered as an error strip in a rail they had no reason to look at.
//
// This is the answer to that: the shell keeps its frame, and the middle of it
// says what happened and offers the one press that fixes it. `Continue as demo`
// asks the server to seed the sample project and hand back a token for it
// (POST /api/v1/demo/session); the workspace then reloads through its existing
// retry. Signing in with a real account is offered underneath, because a demo
// session is not what someone with their own project came for.
//
// Nothing here fabricates a project. Until the server answers, the page says it
// has nothing — and when it can name the demo project, it names the one the
// server reported, never an invented title.

import { useEffect, useId, useState } from "react";
import { ApiError } from "@/lib/apiClient";
import { loadDemoStatus, startDemoSession } from "@/lib/demoApi";
import { describeFailure, FOCUS_RING } from "@/components/casting/theme";
import { SignInForm } from "@/components/auth/SignInForm";

/**
 * The two cold starts, which need different words:
 *
 *   unauthenticated  no token at all — nothing on this deployment will load.
 *   no-project       authenticated enough to ask, and the demo project is not
 *                    there (or belongs to someone else: a project you do not
 *                    own answers 404, backend/app/api/deps.py).
 */
export type ColdStartKind = "unauthenticated" | "no-project";

const isStatus = (err: unknown, status: number): boolean =>
  err instanceof ApiError && err.status === status;

/**
 * Decide whether the workspace is looking at a cold start rather than an
 * ordinary failure. Returns null when it is not — a network outage, a 500, a
 * scene that is missing from a project that loaded fine, and a timeline that
 * has simply never been rendered are all reported where they happen, by the
 * panels that already do it.
 *
 * The two triggers, and why only these two:
 *
 *   401 from EITHER half. There is no token, so nothing on this page can load
 *   and no amount of retrying will change that.
 *
 *   404 from the SCENE half, and only while the workspace is on the demo
 *   reference. The scene half is the one that reads GET /projects/{id}, so its
 *   404 means "no such project". The timeline half's 404 is the ordinary
 *   "no audio rendered yet" (see `getTimeline` in lib/httpApi.ts) and must
 *   never be mistaken for a missing project. Off the demo reference a 404 means
 *   the id someone typed into the scene rail was wrong, which the rail says
 *   itself — hijacking the whole shell for that would be worse.
 */
export function coldStartKind({
  sceneFailure,
  timelineFailure,
  onDemoRef,
}: {
  sceneFailure: unknown;
  timelineFailure: unknown;
  onDemoRef: boolean;
}): { kind: ColdStartKind; failure: unknown } | null {
  // The triggering failure travels with the verdict, so the panel quotes the
  // response it is actually talking about rather than whichever half happened
  // to fail first.
  if (isStatus(sceneFailure, 401)) {
    return { kind: "unauthenticated", failure: sceneFailure };
  }
  if (isStatus(timelineFailure, 401)) {
    return { kind: "unauthenticated", failure: timelineFailure };
  }
  if (onDemoRef && isStatus(sceneFailure, 404)) {
    return { kind: "no-project", failure: sceneFailure };
  }
  return null;
}

/** What GET /api/v1/demo said about this deployment, as far as the button cares. */
type Availability =
  | "checking"
  /** A demo exists, or the deployment is willing to seed one. */
  | "available"
  /** The demo is switched off here (or this build's API has no demo at all). */
  | "unavailable"
  /** The probe itself failed. Not proof of anything — offer the button and let
   * the real request answer, rather than hiding it on a hunch. */
  | "unknown";

const COPY: Record<ColdStartKind, { eyebrow: string; headline: string; body: string }> = {
  unauthenticated: {
    eyebrow: "No session",
    headline: "Nothing is open yet — this browser has no session.",
    body:
      "Every project endpoint on this deployment takes a bearer token, and none " +
      "is stored here, so the scene and its timeline both came back unauthorised. " +
      "Nothing below is being estimated or filled in: there is no project loaded.",
  },
  "no-project": {
    eyebrow: "No project",
    headline: "This deployment has no demo project open to you.",
    body:
      "The workspace opens on the demo reference, and the API answered 404. " +
      "Either nothing has been seeded into this instance yet, or the project " +
      "that was seeded belongs to another account — a project you do not own " +
      "answers 404 rather than admitting it exists.",
  },
};

export default function ColdStartPanel({
  kind,
  /** The failure that triggered this, so the server's own words are kept. */
  failure,
  /** Re-run the workspace's load. The same `retry` the failure panels use. */
  onRecovered,
}: {
  kind: ColdStartKind;
  failure: unknown;
  onRecovered: () => void;
}) {
  const headingId = useId();
  const formId = useId();

  const [availability, setAvailability] = useState<Availability>("checking");
  const [demo, setDemo] = useState<{ title: string | null; scenes: number } | null>(
    null,
  );
  const [disabledDetail, setDisabledDetail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  // Focus follows a disclosure the visitor opened, never one that opened itself
  // because the probe came back "no demo here" while they were reading.
  const [revealedByUser, setRevealedByUser] = useState(false);

  // Ask what this deployment's demo is before offering it. The probe is
  // memoized in lib/demoApi: a seeded answer is already cached by the load that
  // just failed, and an unseeded one costs a single unauthenticated GET, which
  // is the whole reason this panel can name the project it is about to open.
  useEffect(() => {
    let cancelled = false;
    loadDemoStatus()
      .then((status) => {
        if (cancelled) return;
        setAvailability(status.enabled ? "available" : "unavailable");
        // With no demo to offer, the credentials are the only way in, so they
        // are opened rather than left behind a disclosure that has to be found.
        if (!status.enabled) setSigningIn(true);
        setDemo(
          status.seeded
            ? { title: status.title, scenes: status.scene_count }
            : null,
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // A 404 here is this build's API saying it has no demo endpoint at all,
        // which is an answer. Anything else is a probe that did not land.
        const absent = isStatus(err, 404);
        setAvailability(absent ? "unavailable" : "unknown");
        if (absent) setSigningIn(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const offerDemo = availability !== "unavailable";

  async function continueAsDemo() {
    setBusy(true);
    setProblem(null);
    try {
      await startDemoSession();
      onRecovered();
    } catch (err) {
      // 403 is the honest "this deployment runs no demo". It is a decision, not
      // a fault, so it changes what the panel offers rather than turning red.
      if (isStatus(err, 403)) {
        setAvailability("unavailable");
        setDisabledDetail(err instanceof ApiError ? err.message : null);
        setSigningIn(true);
      } else {
        setProblem(describeFailure(err).detail);
      }
    } finally {
      setBusy(false);
    }
  }

  const copy = COPY[kind];
  const serverSaid = describeFailure(failure).detail;

  return (
    <div className="cast-scroll flex min-h-0 flex-1 items-center justify-center overflow-y-auto px-5 py-10">
      <section
        role="alert"
        aria-labelledby={headingId}
        className="cast-panel w-full max-w-xl px-6 py-6 sm:px-7 sm:py-7"
      >
        <div className="flex items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-400" />
          <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-amber-300/90">
            {copy.eyebrow}
          </span>
        </div>

        <h1
          id={headingId}
          className="mt-3 text-[17px] font-medium leading-snug text-zinc-100"
        >
          {copy.headline}
        </h1>
        <p className="mt-2.5 max-w-prose text-[13px] leading-relaxed text-zinc-400">
          {copy.body}
        </p>

        {/* The server's own wording, kept verbatim — it is written for a human
            and says more than anything this panel could paraphrase. */}
        <p className="mt-3 break-words rounded-md border border-[var(--hairline)] bg-black/30 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-zinc-400">
          {serverSaid}
        </p>

        {offerDemo ? (
          <>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={continueAsDemo}
                disabled={busy}
                className={`rounded-lg bg-amber-400 px-3.5 py-2 text-[13px] font-semibold text-zinc-950 transition-colors hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`}
              >
                {busy ? "Opening the demo…" : "Continue as demo"}
              </button>
              <span className="font-mono text-[10px] text-zinc-600">
                POST /api/v1/demo/session
              </span>
            </div>

            <p className="mt-2.5 max-w-prose text-[12px] leading-relaxed text-zinc-500">
              That asks the server to seed its sample project if it is not there
              yet, signs this browser in with the token it returns, and reloads
              the scene. It writes nothing but that sample project, and it needs
              no account of yours.
            </p>

            {/* Only ever the server's own answer about its own demo — a title
                is shown because GET /api/v1/demo reported one, never because
                this panel had a nice name for an empty project. */}
            {demo && (
              <p className="mt-1.5 font-mono text-[11px] text-zinc-500">
                Seeded here:{" "}
                <span className="text-zinc-300">{demo.title ?? "untitled project"}</span>
                {demo.scenes > 0 && ` · ${demo.scenes} scenes`}
              </p>
            )}

            {problem && (
              <p className="mt-3 break-words font-mono text-[11px] leading-relaxed text-rose-300">
                {problem}
              </p>
            )}
          </>
        ) : (
          <div className="mt-5 rounded-lg border border-[var(--hairline)] bg-white/[0.02] px-3.5 py-3">
            <p className="max-w-prose text-[12.5px] leading-relaxed text-zinc-300">
              This deployment has no demo project, so there is nothing to open
              without an account. Sign in below with one that owns a project.
            </p>
            {disabledDetail && (
              <p className="mt-2 break-words font-mono text-[11px] text-zinc-500">
                {disabledDetail}
              </p>
            )}
          </div>
        )}

        <div className="mt-5 border-t border-[var(--hairline)] pt-4">
          <button
            type="button"
            onClick={() => {
              setSigningIn((s) => !s);
              setRevealedByUser(true);
            }}
            aria-expanded={signingIn}
            aria-controls={formId}
            className={`rounded-sm text-[12px] text-zinc-400 underline-offset-2 transition-colors hover:text-zinc-200 hover:underline ${FOCUS_RING}`}
          >
            {signingIn
              ? "Hide sign-in"
              : offerDemo
                ? "Or sign in to your own account"
                : "Sign in"}
          </button>

          <div id={formId} hidden={!signingIn}>
            {signingIn && (
              <>
                <SignInForm
                  idPrefix="ws-cold-start"
                  onSignedIn={onRecovered}
                  allowRegister
                  autoFocus={revealedByUser}
                  className="mt-3 max-w-sm"
                />
                <p className="mt-3 max-w-prose text-[11.5px] leading-relaxed text-zinc-600">
                  Signing in reloads this scene. Your own projects open from the
                  reference control at the foot of the Scenes rail — the demo
                  project belongs to the demo account and stays invisible to
                  everyone else.
                </p>
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
