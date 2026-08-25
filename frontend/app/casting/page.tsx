"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type CastingData } from "@/lib/api";
import { useCastingStore } from "@/lib/castingStore";
import SceneHero from "@/components/casting/SceneHero";
import CharacterRoster from "@/components/casting/CharacterRoster";
import VoiceFitPanel from "@/components/casting/VoiceFitPanel";
import CandidateLeaderboard from "@/components/casting/CandidateLeaderboard";
import AnimaticJudgePanel from "@/components/casting/AnimaticJudgePanel";
import { FOCUS_RING } from "@/components/casting/theme";

export default function CastingStudioPage() {
  const load = useCastingStore((s) => s.load);
  const [casting, setCasting] = useState<CastingData | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getCasting("demo").then((data) => {
      if (cancelled) return;
      setCasting(data);
      load({
        projectId: data.projectId,
        title: data.title,
        characters: data.characters,
        voices: data.voices,
        casting: data.casting,
        candidates: data.candidates,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

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
            <AnimaticJudgePanel />
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
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <section className="lg:col-span-4" aria-label="Character roster">
            <CharacterRoster />
          </section>
          <section className="lg:col-span-8" aria-label="Voice-fit judge">
            <VoiceFitPanel />
          </section>
        </div>
        <section className="mt-4" aria-label="Candidates and leaderboard">
          <CandidateLeaderboard />
        </section>
      </main>
    </div>
  );
}
