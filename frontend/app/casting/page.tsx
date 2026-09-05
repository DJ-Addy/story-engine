"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, type CastingData } from "@/lib/api";
import { useCastingStore } from "@/lib/castingStore";
import SceneHero from "@/components/casting/SceneHero";
import CharacterRoster from "@/components/casting/CharacterRoster";
import VoiceFitPanel from "@/components/casting/VoiceFitPanel";
import CandidateLeaderboard from "@/components/casting/CandidateLeaderboard";
import AnimaticJudgePanel from "@/components/casting/AnimaticJudgePanel";
import { FailurePanel } from "@/components/casting/JudgeStatus";
import ApiModeBadge from "@/components/ApiModeBadge";
import { FOCUS_RING } from "@/components/casting/theme";

type LoadState = "loading" | "ready" | "failed";

export default function CastingStudioPage() {
  const load = useCastingStore((s) => s.load);
  const [casting, setCasting] = useState<CastingData | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<unknown>(null);

  // Bumping this re-runs the load effect (the Retry affordance).
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api
      .getCasting("demo")
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
  }, [load, attempt]);

  // The transition back to "loading" happens here, in the event handler, not
  // inside the effect — a synchronous setState in an effect body cascades.
  const retry = useCallback(() => {
    setState("loading");
    setError(null);
    setAttempt((n) => n + 1);
  }, []);

  return (
    <div className="min-h-screen bg-[var(--cast-bg)] text-zinc-200">
      <header className="sticky top-0 z-40 border-b border-[var(--hairline)] bg-[var(--cast-bg)]/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-5 py-3 sm:px-8">
          <Link
            href="/"
            className={`rounded-sm text-xs font-medium text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
          >
            Story Engine
          </Link>
          <span className="text-zinc-700" aria-hidden>
            /
          </span>
          <h1 className="font-mono text-xs text-zinc-100">Casting Studio</h1>
          {casting && (
            <span className="hidden rounded border border-[var(--hairline)] px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 md:inline">
              {casting.title}
            </span>
          )}
          <div className="ml-auto flex items-center gap-3">
            <ApiModeBadge />
            <AnimaticJudgePanel />
            <Link
              href="/timeline"
              className={`rounded-sm text-xs text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
            >
              Timeline →
            </Link>
            <Link
              href="/scenes/demo"
              className={`rounded-sm text-xs text-zinc-400 transition-colors hover:text-zinc-100 ${FOCUS_RING}`}
            >
              Scene workspace →
            </Link>
          </div>
        </div>
      </header>

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
