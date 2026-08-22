"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type SceneData } from "@/lib/api";
import { useSceneStore } from "@/lib/store";
import ShotListEditor from "@/components/ShotListEditor";
import ContinuityPanel from "@/components/ContinuityPanel";
import AxisDiagram from "@/components/AxisDiagram";

export default function SceneWorkspacePage() {
  const loadScene = useSceneStore((s) => s.loadScene);
  const [scene, setScene] = useState<SceneData | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.getScene("demo").then((data) => {
      if (cancelled) return;
      setScene(data);
      loadScene(data.shots, data.findings);
    });
    return () => {
      cancelled = true;
    };
  }, [loadScene]);

  return (
    <div className="flex flex-col h-screen bg-zinc-950 text-zinc-200">
      <header className="shrink-0 border-b border-zinc-800 px-4 py-2 flex items-center gap-3">
        <Link href="/" className="text-xs text-zinc-500 hover:text-zinc-200">
          Story Engine
        </Link>
        <span className="text-zinc-700">/</span>
        <h1 className="font-mono text-xs text-zinc-200">
          {scene?.title ?? "Loading scene…"}
        </h1>
        {scene && (
          <span className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-zinc-800 text-zinc-500">
            profile: {scene.grammar_profile}
          </span>
        )}
      </header>
      <main className="flex-1 min-h-0 grid grid-cols-[1fr_340px] gap-2 p-2">
        <section className="min-h-0">
          <ShotListEditor />
        </section>
        <aside className="min-h-0 flex flex-col gap-2">
          <AxisDiagram />
          <div className="flex-1 min-h-0">
            <ContinuityPanel />
          </div>
        </aside>
      </main>
    </div>
  );
}
