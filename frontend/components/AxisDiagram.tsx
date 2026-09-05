"use client";

import { useMemo } from "react";
import { useSceneStore } from "@/lib/store";
import type { ShotSpec } from "@/lib/types";

const W = 320;
const H = 230;
const CX = W / 2;
const CY = H / 2;
const SUBJECT_GAP = 110;

/**
 * Deterministic top-down camera placement. The action axis runs horizontally
 * between the two subjects. In SVG coordinates (y grows downward):
 * side 'a' cameras sit below the axis line, 'b' above, neutral on the
 * perpendicular through the midpoint. Angle/radius vary only with ordinal so
 * the layout is stable across renders.
 */
function cameraPosition(shot: ShotSpec): { x: number; y: number } {
  const radius = 72 + (shot.ordinal % 4) * 11;
  let thetaDeg: number;
  if (shot.axis_side === "a") {
    thetaDeg = 30 + ((shot.ordinal * 43) % 120); // 30–150: below the line
  } else if (shot.axis_side === "b") {
    thetaDeg = 210 + ((shot.ordinal * 43) % 120); // 210–330: above the line
  } else {
    thetaDeg = shot.ordinal % 2 === 0 ? 90 : 270; // on the perpendicular
  }
  const theta = (thetaDeg * Math.PI) / 180;
  return {
    x: CX + radius * Math.cos(theta),
    y: CY + radius * Math.sin(theta),
  };
}

export default function AxisDiagram() {
  const shots = useSceneStore((s) => s.shots);
  const selectedOrdinal = useSceneStore((s) => s.selectedOrdinal);
  const hoveredOrdinal = useSceneStore((s) => s.hoveredOrdinal);
  const selectShot = useSceneStore((s) => s.selectShot);
  const hoverShot = useSceneStore((s) => s.hoverShot);

  const subjects = useMemo(() => {
    const seen: string[] = [];
    for (const shot of shots) {
      for (const subj of shot.subjects) {
        if (!seen.includes(subj)) seen.push(subj);
      }
    }
    return seen.slice(0, 2);
  }, [shots]);

  const subjectA = { x: CX - SUBJECT_GAP / 2, y: CY, name: subjects[0] ?? "?" };
  const subjectB = { x: CX + SUBJECT_GAP / 2, y: CY, name: subjects[1] ?? "?" };

  return (
    <div className="cast-panel flex flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--hairline)] px-3 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
          Axis map
        </h2>
        <span className="text-[10px] font-mono text-zinc-600">
          top-down · a below / b above
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Top-down axis diagram of camera positions"
      >
        {/* action axis line, extended past both subjects */}
        <line
          x1={subjectA.x - 40}
          y1={CY}
          x2={subjectB.x + 40}
          y2={CY}
          stroke="#3f3f46"
          strokeWidth="1"
          strokeDasharray="5 4"
        />
        {/* perpendicular reference through the midpoint */}
        <line
          x1={CX}
          y1={CY - 24}
          x2={CX}
          y2={CY + 24}
          stroke="#27272a"
          strokeWidth="1"
        />

        {/* camera dots */}
        {shots.map((shot) => {
          const pos = cameraPosition(shot);
          const active =
            shot.ordinal === selectedOrdinal || shot.ordinal === hoveredOrdinal;
          return (
            <g
              key={shot.ordinal}
              className="cursor-pointer"
              onClick={() => selectShot(shot.ordinal)}
              onMouseEnter={() => hoverShot(shot.ordinal)}
              onMouseLeave={() => hoverShot(null)}
            >
              <circle
                cx={pos.x}
                cy={pos.y}
                r={active ? 8 : 6}
                fill={active ? "#0ea5e9" : "#18181b"}
                stroke={active ? "#7dd3fc" : "#52525b"}
                strokeWidth={active ? 1.5 : 1}
              />
              <text
                x={pos.x}
                y={pos.y + 3}
                textAnchor="middle"
                fontSize="8"
                fontFamily="var(--font-geist-mono), monospace"
                fill={active ? "#f0f9ff" : "#a1a1aa"}
              >
                {shot.ordinal}
              </text>
            </g>
          );
        })}

        {/* subjects drawn last so they stay on top */}
        {[subjectA, subjectB].map((s) => (
          <g key={s.name + s.x}>
            <circle cx={s.x} cy={s.y} r={11} fill="#27272a" stroke="#71717a" strokeWidth="1.5" />
            <text
              x={s.x}
              y={s.y + 3.5}
              textAnchor="middle"
              fontSize="10"
              fontWeight="600"
              fill="#e4e4e7"
            >
              {s.name.charAt(0).toUpperCase()}
            </text>
            <text
              x={s.x}
              y={s.y + 26}
              textAnchor="middle"
              fontSize="8"
              fill="#71717a"
            >
              {s.name}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
