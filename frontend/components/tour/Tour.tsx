"use client";

// A spotlight tour. One element is lit; the rest of the page is dimmed; a card
// beside it says what the element is for. Next moves on, and when the next
// step lives on another page the tour navigates there and resumes.
//
// The dimming is an SVG mask — a full-screen dark rectangle with the target's
// box cut out — rather than four positioned divs, because a mask follows a
// rounded cutout and a glow cleanly and resizes with the target. The whole
// overlay is pointer-transparent so the spotlit control stays clickable; only
// the card takes the pointer.
//
// No library. The behaviour is small, and a dependency that draws overlays in
// its own theme would have fought the one this app already has.

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { FOCUS_RING } from "@/components/casting/theme";
import { TOUR_PROGRESS_KEY, TOUR_SEEN_KEY, TOUR_STEPS, type TourStep } from "@/lib/tour";

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

const PAD = 8;
const CARD_W = 340;
const GAP = 14;

function readProgress(): number | null {
  try {
    const raw = window.sessionStorage.getItem(TOUR_PROGRESS_KEY);
    if (raw === null) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n >= 0 && n < TOUR_STEPS.length ? n : null;
  } catch {
    return null;
  }
}

function writeProgress(index: number | null): void {
  try {
    if (index === null) window.sessionStorage.removeItem(TOUR_PROGRESS_KEY);
    else window.sessionStorage.setItem(TOUR_PROGRESS_KEY, String(index));
  } catch {
    // Private mode: the tour still works for this page, it just will not
    // survive a navigation.
  }
}

/** Start the tour from the first step. Exported for the nav button. */
export function startTour(router: { push: (href: string) => void }, pathname: string): void {
  writeProgress(0);
  try {
    window.localStorage.setItem(TOUR_SEEN_KEY, "1");
  } catch {
    // ignore
  }
  const first = TOUR_STEPS[0];
  if (pathname !== first.route) router.push(first.route);
  else window.dispatchEvent(new Event("story-engine:tour"));
}

export default function Tour() {
  const pathname = usePathname();
  const router = useRouter();
  const params = useSearchParams();
  const [index, setIndex] = useState<number | null>(null);
  const [box, setBox] = useState<Box | null>(null);
  const [waited, setWaited] = useState(0);

  const step: TourStep | null = index === null ? null : (TOUR_STEPS[index] ?? null);

  // Resume from storage, start from ?tour=1, or auto-start once for a first
  // visit to the pipeline. Listens for the nav button's event too.
  useEffect(() => {
    const sync = () => {
      const fromUrl = params.get("tour") === "1";
      if (fromUrl) {
        writeProgress(0);
        setIndex(0);
        return;
      }
      const stored = readProgress();
      if (stored !== null) {
        setIndex(stored);
        return;
      }
      if (pathname === "/pipeline") {
        let seen = true;
        try {
          seen = window.localStorage.getItem(TOUR_SEEN_KEY) === "1";
        } catch {
          seen = true;
        }
        if (!seen) {
          try {
            window.localStorage.setItem(TOUR_SEEN_KEY, "1");
          } catch {
            // ignore
          }
          writeProgress(0);
          setIndex(0);
          return;
        }
      }
      setIndex(null);
    };
    sync();
    window.addEventListener("story-engine:tour", sync);
    return () => window.removeEventListener("story-engine:tour", sync);
  }, [pathname, params]);

  // A step on another page: go there. The stored progress resumes it.
  useEffect(() => {
    if (!step) return;
    if (step.route !== pathname) router.push(step.route);
  }, [step, pathname, router]);

  // Track the target's box. Targets appear after data loads, so poll briefly
  // and follow resizes/scrolls; give up on the spotlight (not the card) after
  // a while so a step whose control never renders still explains itself.
  useLayoutEffect(() => {
    if (!step || step.route !== pathname) {
      setBox(null);
      return;
    }
    let raf = 0;
    let tries = 0;
    let scrolled = false;
    const measure = () => {
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
      if (el) {
        if (!scrolled) {
          el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
          scrolled = true;
        }
        const r = el.getBoundingClientRect();
        setBox({ top: r.top, left: r.left, width: r.width, height: r.height });
        setWaited(0);
      } else {
        tries += 1;
        setBox(null);
        setWaited(tries);
      }
      raf = window.requestAnimationFrame(measure);
    };
    raf = window.requestAnimationFrame(measure);
    return () => window.cancelAnimationFrame(raf);
  }, [step, pathname]);

  const finish = useCallback(() => {
    writeProgress(null);
    setIndex(null);
  }, []);

  const go = useCallback(
    (next: number) => {
      if (next < 0) return;
      if (next >= TOUR_STEPS.length) {
        finish();
        return;
      }
      writeProgress(next);
      setIndex(next);
    },
    [finish],
  );

  // Keyboard: Esc leaves, arrows move.
  useEffect(() => {
    if (index === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
      else if (e.key === "ArrowRight" || e.key === "Enter") go(index + 1);
      else if (e.key === "ArrowLeft") go(index - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, go, finish]);

  // Card position: beside the target per placement, clamped into the viewport;
  // centred when there is no target to sit beside.
  const cardStyle = useMemo<React.CSSProperties>(() => {
    if (typeof window === "undefined") return {};
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (!box) {
      return { top: "50%", left: "50%", transform: "translate(-50%, -50%)" };
    }
    const placement = step?.placement ?? "bottom";
    let top = box.top + box.height + PAD + GAP;
    let left = box.left + box.width / 2 - CARD_W / 2;
    if (placement === "top") top = box.top - PAD - GAP - 180;
    if (placement === "left") {
      top = box.top;
      left = box.left - PAD - GAP - CARD_W;
    }
    if (placement === "right") {
      top = box.top;
      left = box.left + box.width + PAD + GAP;
    }
    left = Math.max(12, Math.min(left, vw - CARD_W - 12));
    top = Math.max(12, Math.min(top, vh - 200));
    return { top, left, width: CARD_W };
  }, [box, step]);

  if (!step || step.route !== pathname) return null;

  const total = TOUR_STEPS.length;
  const n = (index ?? 0) + 1;
  const hole = box
    ? {
        x: box.left - PAD,
        y: box.top - PAD,
        w: box.width + PAD * 2,
        h: box.height + PAD * 2,
      }
    : null;

  return (
    <div className="pointer-events-none fixed inset-0 z-[100]" aria-live="polite">
      {/* Dimmer with the target cut out. */}
      <svg className="absolute inset-0 h-full w-full" aria-hidden>
        <defs>
          <mask id="tour-mask">
            <rect x="0" y="0" width="100%" height="100%" fill="white" />
            {hole && (
              <rect x={hole.x} y={hole.y} width={hole.w} height={hole.h} rx="10" fill="black" />
            )}
          </mask>
        </defs>
        <rect
          x="0"
          y="0"
          width="100%"
          height="100%"
          fill="rgba(3, 5, 9, 0.72)"
          mask="url(#tour-mask)"
        />
        {hole && (
          <rect
            x={hole.x}
            y={hole.y}
            width={hole.w}
            height={hole.h}
            rx="10"
            fill="none"
            stroke="rgb(251 191 36)"
            strokeWidth="2"
            style={{ filter: "drop-shadow(0 0 14px rgba(251,191,36,0.75))" }}
          />
        )}
      </svg>

      {/* The card. */}
      <div
        role="dialog"
        aria-label={`Tour step ${n} of ${total}: ${step.title}`}
        className="pointer-events-auto absolute rounded-lg border border-amber-500/40 bg-[var(--cast-bg)] p-4 shadow-[0_20px_60px_-20px_rgba(0,0,0,0.9)]"
        style={cardStyle}
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-amber-300/90">
            tour · {n} / {total}
          </span>
          {!box && waited > 20 && (
            <span className="font-mono text-[10px] text-zinc-600">(control not on screen yet)</span>
          )}
        </div>
        <h3 className="mt-1.5 text-sm font-semibold text-zinc-50">{step.title}</h3>
        <p className="mt-1.5 text-xs leading-relaxed text-zinc-400">{step.body}</p>
        <div className="mt-3 flex items-center gap-2">
          <button
            onClick={() => go((index ?? 0) - 1)}
            disabled={(index ?? 0) === 0}
            className={`rounded-md border border-[var(--hairline-strong)] px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-zinc-300 hover:text-zinc-50 disabled:opacity-30 ${FOCUS_RING}`}
          >
            back
          </button>
          <button
            onClick={() => go((index ?? 0) + 1)}
            className={`rounded-md bg-amber-400 px-3 py-1 font-mono text-[10px] uppercase tracking-wider text-zinc-950 hover:bg-amber-300 ${FOCUS_RING}`}
          >
            {n === total ? "finish" : "next"}
          </button>
          <button
            onClick={finish}
            className={`ml-auto font-mono text-[10px] uppercase tracking-wider text-zinc-500 hover:text-zinc-200 ${FOCUS_RING}`}
          >
            skip tour
          </button>
        </div>
      </div>
    </div>
  );
}
