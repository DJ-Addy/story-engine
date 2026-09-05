"use client";

// The stack under the shot list: everything that describes the CURRENT
// selection or the scene around it, tabbed so the workspace fits on a laptop
// without any of it being cut. Each tab renders the existing panel unchanged —
// the inspector, the continuity validator, the axis map, the AI edits — so this
// is a container, not a rewrite.

import { useSceneStore } from "@/lib/store";
import AxisDiagram from "@/components/AxisDiagram";
import ContinuityPanel from "@/components/ContinuityPanel";
import Inspector from "@/components/timeline/Inspector";
import AiAssistStrip from "@/components/timeline/AiAssistStrip";
import { FOCUS_RING } from "@/components/casting/theme";

export type SidePanelTab = "inspector" | "continuity" | "axis" | "ai";

const TABS: { id: SidePanelTab; label: string }[] = [
  { id: "inspector", label: "Inspector" },
  { id: "continuity", label: "Continuity" },
  { id: "axis", label: "Axis" },
  { id: "ai", label: "AI assist" },
];

export default function SidePanel({
  tab,
  onTab,
}: {
  tab: SidePanelTab;
  onTab: (next: SidePanelTab) => void;
}) {
  const unresolved = useSceneStore(
    (s) => s.findings.filter((f) => !f.deliberate).length,
  );

  return (
    <div className="flex min-h-0 flex-col gap-1.5">
      <div role="tablist" aria-label="Scene panels" className="flex shrink-0 gap-1">
        {TABS.map((t) => {
          const active = t.id === tab;
          return (
            <button
              key={t.id}
              role="tab"
              id={`ws-tab-${t.id}`}
              aria-selected={active}
              aria-controls={`ws-panel-${t.id}`}
              onClick={() => onTab(t.id)}
              className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
                active
                  ? "border-[var(--hairline-strong)] bg-white/[0.07] font-medium text-zinc-100"
                  : "border-transparent text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-200"
              } ${FOCUS_RING}`}
            >
              {t.label}
              {t.id === "continuity" && unresolved > 0 && (
                <span className="rounded-full bg-amber-500/15 px-1.5 font-mono text-[9px] text-amber-300">
                  {unresolved}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`ws-panel-${tab}`}
        aria-labelledby={`ws-tab-${tab}`}
        className="min-h-0 flex-1"
      >
        {tab === "inspector" && <Inspector />}
        {tab === "continuity" && <ContinuityPanel />}
        {tab === "axis" && (
          <div className="cast-scroll h-full overflow-y-auto">
            <AxisDiagram />
          </div>
        )}
        {tab === "ai" && (
          <div className="cast-scroll h-full overflow-y-auto">
            <AiAssistStrip />
          </div>
        )}
      </div>
    </div>
  );
}
