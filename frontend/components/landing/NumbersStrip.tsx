"use client";

import { useRef } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger, useGSAP);

type Stat = {
  prefix?: string;
  value: number;
  suffix?: string;
  label: string;
};

const STATS: Stat[] = [
  { value: 99, suffix: "%", label: "cue attribution on screenplays" },
  { value: 6, label: "continuity rules, deterministic" },
  { value: 367, label: "tests green" },
  { prefix: "$", value: 70, label: "cost ceiling per feature" },
];

export default function NumbersStrip() {
  const sectionRef = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        const nums = gsap.utils.toArray<HTMLElement>(
          "[data-count]",
          sectionRef.current
        );
        nums.forEach((el) => {
          const target = Number(el.dataset.count);
          const proxy = { v: 0 };
          gsap.to(proxy, {
            v: target,
            duration: 1.6,
            ease: "power2.out",
            scrollTrigger: {
              trigger: sectionRef.current,
              start: "top 80%",
              once: true,
            },
            onUpdate: () => {
              el.textContent = String(Math.round(proxy.v));
            },
          });
        });
      });
    },
    { scope: sectionRef }
  );

  return (
    <section
      ref={sectionRef}
      className="border-t border-zinc-800 bg-zinc-900/30"
      aria-label="By the numbers"
    >
      <div className="mx-auto grid max-w-6xl grid-cols-2 gap-x-6 gap-y-12 px-6 py-20 md:grid-cols-4">
        {STATS.map((s) => (
          <div key={s.label}>
            <p className="font-mono text-4xl font-semibold tracking-tight text-zinc-50 md:text-5xl">
              {s.prefix && <span className="text-amber-400">{s.prefix}</span>}
              <span data-count={s.value}>{s.value}</span>
              {s.suffix && <span className="text-amber-400">{s.suffix}</span>}
            </p>
            <p className="mt-2 text-xs leading-relaxed text-zinc-500">
              {s.label}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
