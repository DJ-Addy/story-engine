"use client";

import Link from "next/link";
import { useRef } from "react";
import { motion, useReducedMotion, type Variants } from "motion/react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger, useGSAP);

const WORDS: { text: string; accent?: string }[] = [
  { text: "Every" },
  { text: "line." },
  { text: "Every" },
  { text: "voice.", accent: "text-amber-400" },
  { text: "Every" },
  { text: "frame.", accent: "text-sky-400" },
];

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.12, delayChildren: 0.25 } },
};

const word: Variants = {
  hidden: { opacity: 0, y: 28 },
  show: { opacity: 1, y: 0, transition: { duration: 0.55, ease: "easeOut" } },
};

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950";

export default function Hero() {
  const sectionRef = useRef<HTMLElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const cueRef = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        // Slow parallax drift + gentle push-in on the backdrop as the hero scrolls away.
        gsap.to(backdropRef.current, {
          yPercent: 14,
          scale: 1.08,
          ease: "none",
          scrollTrigger: {
            trigger: sectionRef.current,
            start: "top top",
            end: "bottom top",
            scrub: true,
          },
        });
        gsap.to(cueRef.current, {
          autoAlpha: 0,
          ease: "none",
          scrollTrigger: {
            trigger: sectionRef.current,
            start: "top top",
            end: "+=240",
            scrub: true,
          },
        });
      });
    },
    { scope: sectionRef }
  );

  return (
    <section
      ref={sectionRef}
      className="relative flex min-h-dvh flex-col justify-end overflow-hidden"
      aria-label="Story Engine introduction"
    >
      <div ref={backdropRef} className="absolute inset-0 will-change-transform">
        {/* Decorative SVG (not next/image — Next blocks SVG unless dangerouslyAllowSVG). */}
        <img
          src="/hero-story-graph.svg"
          alt="A projector beam illuminating a constellation of story-graph nodes flowing into a strip of film"
          className="absolute inset-0 h-full w-full object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-zinc-950 via-zinc-950/55 to-zinc-950/30" />
        <div className="absolute inset-x-0 bottom-0 h-2/5 bg-gradient-to-t from-zinc-950 to-transparent" />
      </div>

      <div className="relative z-10 mx-auto w-full max-w-6xl px-6 pb-28 pt-40">
        <motion.h1
          variants={container}
          initial={reduce ? "show" : "hidden"}
          animate="show"
          className="max-w-3xl text-5xl font-semibold tracking-tight text-zinc-50 md:text-7xl"
        >
          {WORDS.map((w, i) => (
            <motion.span
              key={i}
              variants={word}
              className={`mr-[0.28em] inline-block ${w.accent ?? ""}`}
            >
              {w.text}
            </motion.span>
          ))}
        </motion.h1>

        <motion.p
          initial={reduce ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: reduce ? 0 : 1.1, ease: "easeOut" }}
          className="mt-6 max-w-2xl text-base leading-relaxed text-zinc-300 md:text-lg"
        >
          Story Engine reads your screenplay or novel and builds a story graph
          — who speaks every line, what they wear and carry, where the camera
          sits.
        </motion.p>

        <motion.div
          initial={reduce ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: reduce ? 0 : 1.3, ease: "easeOut" }}
          className="mt-10 flex flex-wrap items-center gap-4"
        >
          <Link
            href="/scenes/demo"
            className={`rounded-md bg-amber-400 px-5 py-2.5 text-sm font-medium text-zinc-950 transition-colors hover:bg-amber-300 ${FOCUS_RING}`}
          >
            Open the demo workspace
          </Link>
          <a
            href="#pipeline"
            className={`rounded-md border border-zinc-700 px-5 py-2.5 text-sm font-medium text-zinc-200 transition-colors hover:border-zinc-500 hover:text-zinc-50 ${FOCUS_RING}`}
          >
            Read the pipeline
          </a>
        </motion.div>
      </div>

      <div
        ref={cueRef}
        aria-hidden="true"
        className="absolute bottom-6 left-1/2 z-10 flex -translate-x-1/2 flex-col items-center gap-2"
      >
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-500">
          Scroll
        </span>
        <span className="h-8 w-px bg-gradient-to-b from-zinc-500 to-transparent" />
      </div>
    </section>
  );
}
