import Link from "next/link";

const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950";

export default function FooterCta() {
  return (
    <>
      <section className="border-t border-zinc-800" aria-label="Get started">
        <div className="mx-auto max-w-6xl px-6 py-28 text-center">
          <p className="mx-auto max-w-2xl text-2xl font-semibold tracking-tight text-zinc-50 md:text-4xl">
            Your manuscript already knows how it wants to sound.
          </p>
          <Link
            href="/scenes/demo"
            className={`mt-10 inline-block rounded-md bg-amber-400 px-6 py-3 text-sm font-medium text-zinc-950 transition-colors hover:bg-amber-300 ${FOCUS_RING}`}
          >
            Open the demo workspace
          </Link>
        </div>
      </section>
      <footer className="border-t border-zinc-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-8">
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold tracking-wide text-zinc-200">
              Story Engine
            </span>
            <span className="rounded border border-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500">
              v0.1 · mock data
            </span>
          </div>
          <nav aria-label="Footer">
            <ul className="flex items-center gap-6 text-xs text-zinc-500">
              <li>
                <Link
                  href="/scenes/demo"
                  className={`rounded-sm transition-colors hover:text-zinc-200 ${FOCUS_RING}`}
                >
                  Demo
                </Link>
              </li>
              <li>
                <a
                  href="#pipeline"
                  className={`rounded-sm transition-colors hover:text-zinc-200 ${FOCUS_RING}`}
                >
                  Pipeline
                </a>
              </li>
            </ul>
          </nav>
        </div>
      </footer>
    </>
  );
}
