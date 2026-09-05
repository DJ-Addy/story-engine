"use client";

import { useMemo, useState } from "react";
import { useSceneStore } from "@/lib/store";
import type { Finding, Severity } from "@/lib/types";

const SEVERITY_ORDER: Severity[] = ["error", "warn", "info"];

const SEVERITY_STYLES: Record<Severity, { dot: string; text: string; badge: string }> = {
  error: { dot: "bg-red-500", text: "text-red-400", badge: "bg-red-950 text-red-300 border-red-900" },
  warn: { dot: "bg-amber-500", text: "text-amber-400", badge: "bg-amber-950 text-amber-300 border-amber-900" },
  info: { dot: "bg-sky-500", text: "text-sky-400", badge: "bg-sky-950 text-sky-300 border-sky-900" },
};

function FindingRow({ finding }: { finding: Finding }) {
  const selectShot = useSceneStore((s) => s.selectShot);
  const markDeliberate = useSceneStore((s) => s.markDeliberate);
  const style = SEVERITY_STYLES[finding.severity];

  return (
    <div
      className={`border-b border-[var(--hairline)] px-3 py-2 text-xs ${
        finding.deliberate ? "opacity-50" : ""
      }`}
    >
      <div className="flex items-start gap-2">
        <span className={`mt-1 h-1.5 w-1.5 rounded-full shrink-0 ${style.dot}`} />
        <div className="flex-1 min-w-0">
          <p className={`leading-snug ${finding.deliberate ? "text-zinc-500" : "text-zinc-300"}`}>
            {finding.message}
          </p>
          <div className="mt-1.5 flex items-center gap-2 flex-wrap">
            {finding.shot_ordinal !== null && (
              <button
                className="font-mono text-[11px] px-1.5 py-0.5 rounded border border-zinc-700 text-zinc-300 hover:border-sky-600 hover:text-sky-300"
                title="Jump to shot"
                onClick={() => selectShot(finding.shot_ordinal)}
              >
                shot {finding.shot_ordinal}
              </button>
            )}
            <label className="flex items-center gap-1.5 text-[11px] text-zinc-500 cursor-pointer select-none">
              <input
                type="checkbox"
                className="accent-sky-600"
                checked={finding.deliberate}
                onChange={(e) =>
                  markDeliberate(finding.id, e.target.checked, finding.deliberate_note)
                }
              />
              deliberate
            </label>
          </div>
          {finding.deliberate && (
            <input
              type="text"
              className="mt-1.5 w-full bg-zinc-900 border border-zinc-800 rounded-sm px-1.5 py-0.5 text-[11px] text-zinc-400 placeholder-zinc-600 outline-none focus:border-zinc-600"
              placeholder="Why is this deliberate?"
              value={finding.deliberate_note ?? ""}
              onChange={(e) => markDeliberate(finding.id, true, e.target.value)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export default function ContinuityPanel() {
  const findings = useSceneStore((s) => s.findings);
  const validatorMode = useSceneStore((s) => s.validatorMode);
  const setValidatorMode = useSceneStore((s) => s.setValidatorMode);
  const [openInSilent, setOpenInSilent] = useState(false);

  const activeCounts = useMemo(() => {
    const counts: Record<Severity, number> = { error: 0, warn: 0, info: 0 };
    for (const f of findings) {
      if (!f.deliberate) counts[f.severity]++;
    }
    return counts;
  }, [findings]);

  const totalActive = activeCounts.error + activeCounts.warn + activeCounts.info;

  const grouped = useMemo(() => {
    const map = new Map<string, Finding[]>();
    for (const f of findings) {
      const list = map.get(f.rule_code) ?? [];
      list.push(f);
      map.set(f.rule_code, list);
    }
    return [...map.entries()];
  }, [findings]);

  const modeToggle = (
    <div className="flex shrink-0 overflow-hidden rounded border border-[var(--hairline)] font-mono text-[10px]">
      {(["strict", "silent"] as const).map((mode) => (
        <button
          key={mode}
          className={`px-2 py-0.5 uppercase tracking-wide ${
            validatorMode === mode
              ? "bg-zinc-700 text-zinc-100"
              : "bg-zinc-900 text-zinc-500 hover:text-zinc-300"
          }`}
          onClick={() => {
            setValidatorMode(mode);
            setOpenInSilent(false);
          }}
        >
          {mode}
        </button>
      ))}
    </div>
  );

  if (validatorMode === "silent" && !openInSilent) {
    return (
      <div className="cast-panel flex flex-col gap-2 p-3">
        <div className="flex items-center justify-between">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
            Continuity
          </h2>
          {modeToggle}
        </div>
        <button
          className="flex items-center justify-center gap-2 rounded border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-300 hover:border-zinc-600"
          onClick={() => setOpenInSilent(true)}
          title="Show findings"
        >
          <span className="font-mono text-sm">{totalActive}</span>
          <span className="text-zinc-500">active finding{totalActive === 1 ? "" : "s"}</span>
        </button>
      </div>
    );
  }

  return (
    <div className="cast-panel flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--hairline)] px-3 py-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
            Continuity
          </h2>
          {SEVERITY_ORDER.map((sev) =>
            activeCounts[sev] > 0 ? (
              <span
                key={sev}
                className={`font-mono text-[10px] px-1.5 py-0.5 rounded border ${SEVERITY_STYLES[sev].badge}`}
              >
                {activeCounts[sev]} {sev}
              </span>
            ) : null,
          )}
          {totalActive === 0 && (
            <span className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-emerald-900 bg-emerald-950 text-emerald-300">
              clean
            </span>
          )}
        </div>
        {modeToggle}
      </div>
      <div className="cast-scroll min-h-0 flex-1 overflow-auto">
        {grouped.length === 0 && (
          <p className="px-3 py-4 text-xs text-zinc-600">
            No findings. The validator found no continuity issues.
          </p>
        )}
        {grouped.map(([ruleCode, ruleFindings]) => (
          <div key={ruleCode}>
            <div className="sticky top-0 border-b border-[var(--hairline)] bg-[#141417] px-3 py-1">
              <span className="font-mono text-[10px] uppercase tracking-wider text-zinc-500">
                {ruleCode}
              </span>
              <span className={`ml-2 font-mono text-[10px] ${SEVERITY_STYLES[ruleFindings[0].severity].text}`}>
                {ruleFindings.filter((f) => !f.deliberate).length}/{ruleFindings.length} active
              </span>
            </div>
            {ruleFindings.map((f) => (
              <FindingRow key={f.id} finding={f} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
