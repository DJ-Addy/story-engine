import { Suspense } from "react";
import type { Metadata } from "next";
import Workspace from "@/components/workspace/Workspace";

export const metadata: Metadata = {
  title: "Workspace · Story Engine",
  description:
    "One scene, two views: the screenplay with its shots in the margin, and the "
    + "program monitor with the cut under it. The assistant edits the story graph.",
};

export default function WorkspacePage() {
  // The workspace reads ?scene= and ?view= with useSearchParams, so it renders
  // on the client below a Suspense boundary rather than blocking the prerender.
  return (
    <Suspense fallback={<div className="tl-shell h-dvh" aria-busy />}>
      <Workspace />
    </Suspense>
  );
}
