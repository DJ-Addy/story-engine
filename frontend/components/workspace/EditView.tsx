"use client";

// The picture is the page.
//
// The rejected layout gave the monitor about a third of a column and spent the
// rest on a shot table and a four-tab panel, which is backwards for a surface
// whose whole subject is what the scene LOOKS like. Here the monitor takes
// whatever height is left after two fixed rows: the filmstrip that says which
// shot, and the dock that says where in time.
//
// Nothing here scrolls the page. The monitor is sized from the height it can
// spare (`.ws-monitor-fit`), the filmstrip scrolls sideways inside itself, and
// the dock is a fixed two lanes until it is asked for more.

import { useState } from "react";
import VideoMonitor from "@/components/timeline/VideoMonitor";
import Filmstrip from "@/components/workspace/Filmstrip";
import LaneDock from "@/components/workspace/LaneDock";

export default function EditView({
  onActivateAudio,
  loading,
  failure,
  onRetry,
}: {
  onActivateAudio: () => void;
  loading: boolean;
  failure: unknown | null;
  onRetry: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 py-3.5">
      <section
        aria-label="Program monitor"
        className="min-h-0 flex-1 overflow-hidden"
      >
        {/* Width derived from the height this row can spare, so the 16:9 stage
            is never squashed. See .ws-monitor-fit in globals.css. */}
        <div className="ws-monitor-fit">
          <VideoMonitor />
        </div>
      </section>

      <Filmstrip />

      <LaneDock
        onActivateAudio={onActivateAudio}
        loading={loading}
        failure={failure}
        onRetry={onRetry}
        expanded={expanded}
        onExpandedChange={setExpanded}
      />
    </div>
  );
}
