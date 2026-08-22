"use client";

import { useRef, type ReactNode } from "react";
import { useReducedMotion } from "motion/react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger, useGSAP);

/* ------------------------------------------------------------------ */
/* Beat data                                                           */
/* ------------------------------------------------------------------ */

type Beat = {
  id: string;
  label: string;
  title: string;
  body: string;
  visual: ReactNode;
};

function FountainCard() {
  return (
    <div className="relative rounded-lg border border-zinc-800 bg-zinc-900/60 p-5 font-mono text-xs leading-relaxed">
      <p className="text-zinc-500">INT. THE GILDED TANKARD — NIGHT</p>
      <p className="mt-4 text-center text-amber-400">MARA</p>
      <p className="text-center text-zinc-200">You said the ledger was clean.</p>
      <p className="mt-3 text-center text-amber-400">DOYLE</p>
      <p className="text-center italic text-zinc-500">(sliding it across)</p>
      <p className="text-center text-zinc-200">
        I said it balanced. Different thing.
      </p>
      <span className="absolute right-3 top-3 rounded border border-sky-400/30 bg-sky-400/10 px-1.5 py-0.5 text-[10px] text-sky-400">
        cue → MARA · conf 0.98
      </span>
    </div>
  );
}

function MiniGraph() {
  const label = { fontSize: 10 } as const;
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
      <svg
        viewBox="0 0 320 180"
        role="img"
        aria-label="A miniature story graph: a scene node linked to two characters, a prop, and a camera position"
        className="w-full font-mono"
      >
        <g stroke="#3f3f46" strokeWidth="1">
          <line x1="160" y1="90" x2="64" y2="42" />
          <line x1="160" y1="90" x2="256" y2="42" />
          <line x1="160" y1="90" x2="64" y2="140" />
          <line x1="160" y1="90" x2="256" y2="140" />
        </g>
        <circle cx="160" cy="90" r="7" fill="#18181b" stroke="#e4e4e7" strokeWidth="1.5" />
        <circle cx="64" cy="42" r="5" fill="#fbbf24" />
        <circle cx="256" cy="42" r="5" fill="#fbbf24" />
        <circle cx="64" cy="140" r="5" fill="#52525b" />
        <circle cx="256" cy="140" r="5" fill="#38bdf8" />
        <text x="160" y="112" textAnchor="middle" fill="#a1a1aa" style={label}>
          SCENE 12
        </text>
        <text x="64" y="30" textAnchor="middle" fill="#fbbf24" style={label}>
          MARA
        </text>
        <text x="256" y="30" textAnchor="middle" fill="#fbbf24" style={label}>
          DOYLE
        </text>
        <text x="64" y="158" textAnchor="middle" fill="#a1a1aa" style={label}>
          LEDGER
        </text>
        <text x="256" y="158" textAnchor="middle" fill="#38bdf8" style={label}>
          CAM A · 50mm
        </text>
        <text x="100" y="60" textAnchor="middle" fill="#71717a" style={{ fontSize: 8 }}>
          speaks · 14 cues
        </text>
      </svg>
    </div>
  );
}

const BAR_HEIGHTS = [
  0.35, 0.6, 0.45, 0.85, 0.55, 0.95, 0.7, 0.4, 0.8, 0.5, 0.9, 0.65, 0.3, 0.75,
  0.55, 0.85, 0.45, 0.6, 0.35, 0.7, 0.5, 0.4, 0.65, 0.3,
];

function AudioBoardsCard() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-5">
        <p className="font-mono text-[10px] uppercase tracking-widest text-zinc-500">
          Audiobook mix
        </p>
        <div className="mt-4 flex h-20 items-end gap-1" aria-hidden="true">
          {BAR_HEIGHTS.map((h, i) => (
            <span
              key={i}
              className="w-1.5 origin-bottom rounded-sm bg-amber-400/70 motion-safe:animate-[wave_1.15s_ease-in-out_infinite]"
              style={{ height: `${h * 100}%`, animationDelay: `${i * 0.07}s` }}
            />
          ))}
        </div>
        <p className="mt-3 font-mono text-[10px] text-zinc-500">
          ambience −18 LUFS · ducked under speech
        </p>
      </div>
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-5">
        <p className="font-mono text-[10px] uppercase tracking-widest text-zinc-500">
          Shot list
        </p>
        <ul className="mt-4 space-y-2 font-mono text-[11px]">
          {[
            ["12A", "WS", "establishing — tavern floor", "24mm"],
            ["12B", "MCU", "MARA — over the ledger", "50mm"],
            ["12C", "CU", "DOYLE — reaction", "85mm"],
          ].map(([n, size, desc, lens]) => (
            <li
              key={n}
              className="flex items-center gap-3 rounded border border-zinc-800 bg-zinc-950/60 px-3 py-2"
            >
              <span className="text-amber-400">{n}</span>
              <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-300">
                {size}
              </span>
              <span className="flex-1 truncate text-zinc-400">{desc}</span>
              <span className="text-sky-400">{lens}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function FilmstripCard() {
  const sprockets = Array.from({ length: 18 });
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="flex justify-between gap-1.5 px-1" aria-hidden="true">
        {sprockets.map((_, i) => (
          <span key={i} className="h-1.5 w-2.5 rounded-[2px] bg-zinc-700/70" />
        ))}
      </div>
      <div className="my-2 grid grid-cols-3 gap-2 md:grid-cols-5">
        {["12A", "12B", "12C", "12D", "12E"].map((n, i) => (
          <div
            key={n}
            className={`relative aspect-video overflow-hidden rounded-sm border border-zinc-800 bg-gradient-to-br from-zinc-800 to-zinc-950 ${
              i > 2 ? "hidden md:block" : ""
            }`}
          >
            <span className="absolute inset-0 bg-[radial-gradient(ellipse_at_30%_20%,rgba(251,191,36,0.14),transparent_60%)]" />
            <span className="absolute bottom-1 right-1.5 font-mono text-[9px] text-zinc-500">
              {n}
            </span>
          </div>
        ))}
      </div>
      <div className="flex justify-between gap-1.5 px-1" aria-hidden="true">
        {sprockets.map((_, i) => (
          <span key={i} className="h-1.5 w-2.5 rounded-[2px] bg-zinc-700/70" />
        ))}
      </div>
      <p className="mt-3 font-mono text-[10px] text-zinc-500">
        boards cut to the finished audio track · 24 fps reference
      </p>
    </div>
  );
}

const BEATS: Beat[] = [
  {
    id: "script",
    label: "Script",
    title: "Ingest the manuscript",
    body: "Fountain, Final Draft, or a novel. Every cue, parenthetical, and stage direction is parsed — each line of dialogue attributed to a speaker and scored for confidence.",
    visual: <FountainCard />,
  },
  {
    id: "graph",
    label: "Story Graph",
    title: "Resolve the graph",
    body: "Characters, wardrobe, props, locations, and camera positions become one graph: who speaks, what they carry, where the lens sits, scene by scene.",
    visual: <MiniGraph />,
  },
  {
    id: "render",
    label: "Audio | Boards",
    title: "Render both ways",
    body: "A multi-voice audiobook with environmental sound design — ambience ducks under speech. And a previz package: shot lists checked by a deterministic continuity validator.",
    visual: <AudioBoardsCard />,
  },
  {
    id: "animatic",
    label: "Animatic",
    title: "Cut the animatic",
    body: "Boards timed against the finished audio. Eyelines, axis, and coverage reviewed before anyone books a stage.",
    visual: <FilmstripCard />,
  },
];

/* ------------------------------------------------------------------ */
/* Node rail                                                           */
/* ------------------------------------------------------------------ */

const NODE_X = [40, 253, 466, 680];

function NodeRail({ staticRail }: { staticRail: boolean }) {
  return (
    <svg
      viewBox="0 0 720 92"
      className="mx-auto w-full max-w-3xl font-mono"
      role="img"
      aria-label="Pipeline stages: Script, Story Graph, Audio and Boards, Animatic"
    >
      {NODE_X.slice(0, -1).map((x, i) => (
        <path
          key={i}
          data-connector=""
          d={`M ${x + 18} 34 L ${NODE_X[i + 1] - 18} 34`}
          pathLength={1}
          strokeDasharray={1}
          strokeDashoffset={staticRail ? 0 : undefined}
          stroke="#fbbf24"
          strokeOpacity={0.6}
          strokeWidth={1.5}
          fill="none"
        />
      ))}
      {NODE_X.map((x, i) => (
        <g key={i}>
          <circle cx={x} cy={34} r={11} fill="#18181b" stroke="#3f3f46" strokeWidth={1.5} />
          <circle
            data-node-dot=""
            cx={x}
            cy={34}
            r={5}
            fill="#fbbf24"
            style={
              !staticRail && i > 0 ? { transform: "scale(0)", transformOrigin: `${x}px 34px` } : undefined
            }
          />
          <text
            data-node-label=""
            x={x}
            y={72}
            textAnchor="middle"
            fill="#a1a1aa"
            style={{ fontSize: 11, opacity: staticRail || i === 0 ? 1 : 0.4 }}
          >
            {BEATS[i].label}
          </text>
        </g>
      ))}
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/* Section                                                             */
/* ------------------------------------------------------------------ */

function BeatCard({ beat, index }: { beat: Beat; index: number }) {
  return (
    <div>
      <p className="font-mono text-[11px] uppercase tracking-widest text-zinc-500">
        {String(index + 1).padStart(2, "0")} · {beat.label}
      </p>
      <h3 className="mt-2 text-xl font-semibold text-zinc-100 md:text-2xl">
        {beat.title}
      </h3>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-400">
        {beat.body}
      </p>
      <div className="mt-5">{beat.visual}</div>
    </div>
  );
}

export default function PipelineStory() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const root = wrapRef.current!;
        const cards = gsap.utils.toArray<HTMLElement>("[data-beat-card]", root);
        const dots = gsap.utils.toArray<SVGCircleElement>("[data-node-dot]", root);
        const labels = gsap.utils.toArray<SVGTextElement>("[data-node-label]", root);
        const connectors = gsap.utils.toArray<SVGPathElement>("[data-connector]", root);
        if (cards.length === 0) return;

        gsap.set(cards.slice(1), { autoAlpha: 0, y: 28 });
        gsap.set(connectors, { strokeDashoffset: 1 });

        const tl = gsap.timeline({
          defaults: { ease: "none" },
          scrollTrigger: {
            trigger: root,
            pin: panelRef.current,
            start: "top top",
            end: "+=2800",
            scrub: 0.6,
            anticipatePin: 1,
          },
        });

        tl.to({}, { duration: 0.35 });
        for (let i = 1; i < cards.length; i++) {
          tl.to(connectors[i - 1], { strokeDashoffset: 0, duration: 0.8 })
            .to(cards[i - 1], { autoAlpha: 0, y: -28, duration: 0.35 }, "<0.45")
            .to(dots[i], { scale: 1, duration: 0.25, ease: "back.out(2)" })
            .to(labels[i], { opacity: 1, duration: 0.2 }, "<")
            .to(cards[i], { autoAlpha: 1, y: 0, duration: 0.5 }, "<");
          tl.to({}, { duration: i === cards.length - 1 ? 0.6 : 0.4 });
        }
      });
    },
    { scope: wrapRef, dependencies: [reduce] }
  );

  /* Reduced motion: no pinning, no scrubbing — everything laid out and visible. */
  if (reduce) {
    return (
      <section id="pipeline" className="border-t border-zinc-800" aria-label="The pipeline">
        <div className="mx-auto max-w-6xl px-6 py-24">
          <p className="font-mono text-[11px] uppercase tracking-widest text-amber-400">
            Pipeline
          </p>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight text-zinc-50 md:text-4xl">
            From manuscript to master
          </h2>
          <div className="mt-10">
            <NodeRail staticRail />
          </div>
          <div className="mt-12 grid gap-12 md:grid-cols-2">
            {BEATS.map((beat, i) => (
              <BeatCard key={beat.id} beat={beat} index={i} />
            ))}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section id="pipeline" className="border-t border-zinc-800" aria-label="The pipeline">
      <div ref={wrapRef}>
        <div
          ref={panelRef}
          className="flex h-dvh flex-col justify-center overflow-hidden"
        >
          <div className="mx-auto w-full max-w-6xl px-6">
            <p className="font-mono text-[11px] uppercase tracking-widest text-amber-400">
              Pipeline
            </p>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight text-zinc-50 md:text-4xl">
              From manuscript to master
            </h2>
            <div className="mt-8">
              <NodeRail staticRail={false} />
            </div>
            <div className="relative mt-6 h-[380px] md:h-[400px]">
              {BEATS.map((beat, i) => (
                <div
                  key={beat.id}
                  data-beat-card=""
                  className="absolute inset-0 will-change-transform"
                >
                  <BeatCard beat={beat} index={i} />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
