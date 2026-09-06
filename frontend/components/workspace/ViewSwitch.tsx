"use client";

// The one switch that matters: read the scene as a script, or cut it as
// picture.
//
// It is a tablist and not a pair of links because both views ARE the same page.
// The top bar, the scene rail and the assistant never move when it is thrown —
// only the middle column is swapped — and a link would promise a navigation
// that never happens. Arrow keys move between the tabs, which is why the strip
// claims them back from the global transport with `data-local-arrow-keys`.

import { FOCUS_RING } from "@/components/casting/theme";

export type WorkspaceView = "script" | "edit";

const VIEWS: { id: WorkspaceView; label: string }[] = [
  { id: "script", label: "Script" },
  { id: "edit", label: "Edit" },
];

/** The id of the tab that labels the view panel; the panel points back at it. */
export const viewTabId = (view: WorkspaceView): string => `ws-view-tab-${view}`;

/** The single panel both tabs control — there is only ever one middle column. */
export const VIEW_PANEL_ID = "ws-view-panel";

export default function ViewSwitch({
  value,
  onChange,
}: {
  value: WorkspaceView;
  onChange: (next: WorkspaceView) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Workspace view"
      data-local-arrow-keys="true"
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const i = VIEWS.findIndex((v) => v.id === value);
        const next = VIEWS[(i + (e.key === "ArrowRight" ? 1 : VIEWS.length - 1)) % VIEWS.length];
        onChange(next.id);
      }}
      className="flex shrink-0 items-center gap-[3px] rounded-[10px] border border-[var(--hairline)] bg-[var(--surface-1)] p-[3px]"
    >
      {VIEWS.map((v) => {
        const on = v.id === value;
        return (
          <button
            key={v.id}
            role="tab"
            id={viewTabId(v.id)}
            type="button"
            aria-selected={on}
            aria-controls={VIEW_PANEL_ID}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(v.id)}
            className={`rounded-lg px-3.5 py-1.5 text-[12.5px] leading-none transition-colors ${
              on
                ? "bg-amber-400/[0.11] font-medium text-amber-300"
                : "text-zinc-400 hover:text-zinc-100"
            } ${FOCUS_RING}`}
          >
            {v.label}
          </button>
        );
      })}
    </div>
  );
}
