import Link from "next/link";
import SmoothScroll from "@/components/landing/SmoothScroll";
import Hero from "@/components/landing/Hero";
import PipelineStory from "@/components/landing/PipelineStory";
import FeatureGrid from "@/components/landing/FeatureGrid";
import NumbersStrip from "@/components/landing/NumbersStrip";
import FooterCta from "@/components/landing/FooterCta";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950";

export default function Home() {
  return (
    <SmoothScroll>
      <div className="flex min-h-screen flex-1 flex-col bg-zinc-950 text-zinc-200">
        <header className="absolute inset-x-0 top-0 z-20">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
            <div className="flex items-center gap-3">
              <span className="text-sm font-semibold tracking-wide text-zinc-100">
                Story Engine
              </span>
              <span className="rounded border border-zinc-700/70 bg-zinc-950/40 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                v0.1
              </span>
            </div>
            <nav aria-label="Primary">
              <ul className="flex items-center gap-6 text-xs text-zinc-300">
                <li>
                  <a
                    href="#pipeline"
                    className={`rounded-sm transition-colors hover:text-zinc-50 ${FOCUS_RING}`}
                  >
                    Pipeline
                  </a>
                </li>
                <li>
                  <Link
                    href="/casting"
                    className={`rounded-sm transition-colors hover:text-zinc-50 ${FOCUS_RING}`}
                  >
                    Casting
                  </Link>
                </li>
                <li>
                  <Link
                    href="/timeline"
                    className={`rounded-sm transition-colors hover:text-zinc-50 ${FOCUS_RING}`}
                  >
                    Timeline
                  </Link>
                </li>
                <li>
                  <Link
                    href="/scenes/demo"
                    className={`rounded-sm transition-colors hover:text-zinc-50 ${FOCUS_RING}`}
                  >
                    Demo
                  </Link>
                </li>
              </ul>
            </nav>
          </div>
        </header>
        <main className="flex-1">
          <Hero />
          <PipelineStory />
          <FeatureGrid />
          <NumbersStrip />
        </main>
        <FooterCta />
      </div>
    </SmoothScroll>
  );
}
