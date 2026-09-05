import { Suspense } from "react";
import type { Metadata } from "next";
import Workspace from "@/components/workspace/Workspace";

export const metadata: Metadata = {
  title: "Workspace · Story Engine",
  description:
    "Shot list, program monitor and timeline for one scene, on one screen.",
};

export default function WorkspacePage() {
  // The workspace reads ?scene= with useSearchParams, so it renders on the
  // client below a Suspense boundary rather than blocking the prerender.
  return (
    <Suspense fallback={<div className="tl-shell h-dvh" aria-busy />}>
      <Workspace />
    </Suspense>
  );
}
