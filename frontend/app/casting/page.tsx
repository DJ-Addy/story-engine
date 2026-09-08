"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { api, type CastingData } from "@/lib/api";
import { useCastingStore } from "@/lib/castingStore";
import SceneHero from "@/components/casting/SceneHero";
import CharacterRoster from "@/components/casting/CharacterRoster";
import VoiceFitPanel from "@/components/casting/VoiceFitPanel";
import CandidateLeaderboard from "@/components/casting/CandidateLeaderboard";
import AnimaticJudgePanel from "@/components/casting/AnimaticJudgePanel";
import { FailurePanel } from "@/components/casting/JudgeStatus";
import ApiModeBadge from "@/components/ApiModeBadge";
import AppNav from "@/components/AppNav";

type LoadState = "loading" | "ready" | "failed";

/**
 * The project the studio opens against.
 *
 * `?project=` mirrors the dashboard's parameter, so one link style addresses
 * both pages. "demo" stays the default, which keeps every existing link and the
 * cold-start path behaving exactly as before — `lib/httpApi.ts` resolves that
 * reference to the seeded project at request time, and passes a real id
 * straight through.
 *
 * Read through `useSyncExternalStore` rather than a mount effect, for the same
 * reason the dashboard does: the server render cannot see the URL, so resolving
 * it in an effect would either flash the wrong project or cascade a synchronous
 * setState that React 19 correctly flags.
 */
const DEFAULT_PROJECT_REF = "demo";

function urlProjectRef(): string {
  return (
    new URLSearchParams(window.location.search).get("project") ||
    DEFAULT_PROJECT_REF
  );
}

const serverProjectRef = () => DEFAULT_PROJECT_REF;

/** The URL does not change under us: the studio has no in-page project switcher. */
const NEVER_CHANGES = () => () => {};

export default function CastingStudioPage() {
  const projectRef = useSyncExternalStore(
    NEVER_CHANGES,
    urlProjectRef,
    serverProjectRef,
  );
  const load = useCastingStore((s) => s.load);
  const [casting, setCasting] = useState<CastingData | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<unknown>(null);

  // Bumping this re-runs the load effect (the Retry affordance).
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .getCasting(projectRef)
      .then((data) => {
        if (cancelled) return;
        setCasting(data);
        load({
          projectId: data.projectId,
          title: data.title,
          characters: data.characters,
          voices: data.voices,
          casting: data.casting,
          candidates: data.candidates,
          voicesAreStub: data.voicesAreStub,
        });
        setState("ready");
      })
      .catch((err: unknown) => {
        // The studio opens against a project that may not exist, may need auth,
        // or may not be reachable at all. Say which, rather than spinning.
        if (cancelled) return;
        setCasting(null);
        setError(err);
        setState("failed");
      });

    return () => {
      cancelled = true;
    };
  }, [load, attempt, projectRef]);

  // The transition back to "loading" happens here, in the event handler, not
  // inside the effect — a synchronous setState in an effect body cascades.
  const retry = useCallback(() => {
    setState("loading");
    setError(null);
    setAttempt((n) => n + 1);
  }, []);

  return (
    <div className="min-h-screen bg-[var(--cast-bg)] text-zinc-200">
      <AppNav>
        {casting && (
          <span className="hidden max-w-[22rem] truncate rounded border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 lg:inline">
            {casting.title}
          </span>
        )}
        <ApiModeBadge />
        <AnimaticJudgePanel />
      </AppNav>

      <SceneHero />

      <main className="mx-auto w-full max-w-7xl px-5 py-8 sm:px-8 md:py-10">
        {state === "failed" ? (
          <FailurePanel
            error={error}
            onRetry={retry}
            retryLabel="Reload casting"
          />
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              <section className="lg:col-span-4" aria-label="Character roster">
                <CharacterRoster loading={state === "loading"} />
              </section>
              <section className="lg:col-span-8" aria-label="Voice-fit judge">
                <VoiceFitPanel />
              </section>
            </div>
            <section className="mt-4" aria-label="Candidates and leaderboard">
              <CandidateLeaderboard />
            </section>
          </>
        )}
      </main>
    </div>
  );
}
