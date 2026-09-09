import { Suspense } from "react";
import type { Metadata } from "next";
import Pipeline from "@/components/pipeline/Pipeline";

export const metadata: Metadata = {
  title: "Pipeline · Story Engine",
  description:
    "Manuscript to story graph to cast, shots, audio, boards and video — one "
    + "step at a time, with the state of each step read live from the API.",
};

export default function PipelinePage() {
  // Reads ?project= and ?scene= with useSearchParams, so it renders on the
  // client below a Suspense boundary rather than blocking the prerender.
  return (
    <Suspense fallback={<div className="min-h-screen bg-[var(--cast-bg)]" aria-busy />}>
      <Pipeline />
    </Suspense>
  );
}
