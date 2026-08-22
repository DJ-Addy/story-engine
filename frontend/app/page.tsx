import Link from "next/link";

export default function Home() {
  return (
    <div className="flex flex-col flex-1 min-h-screen bg-zinc-950 text-zinc-200">
      <header className="border-b border-zinc-800 px-6 py-3 flex items-center gap-3">
        <h1 className="text-sm font-semibold tracking-wide">Story Engine</h1>
        <span className="font-mono text-[10px] px-1.5 py-0.5 rounded border border-zinc-800 text-zinc-500">
          v0.1 · mock data
        </span>
      </header>
      <main className="flex-1 px-6 py-8 max-w-2xl">
        <p className="text-sm text-zinc-400 mb-6">
          Screenplay-to-audiobook and previz pipeline. Shot lists are validated
          by the deterministic continuity checker; edit coverage below.
        </p>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-2">
          Scenes
        </h2>
        <Link
          href="/scenes/demo"
          className="block border border-zinc-800 rounded-md bg-zinc-900/50 px-4 py-3 hover:border-zinc-600 transition-colors"
        >
          <div className="flex items-center justify-between">
            <span className="font-mono text-xs text-zinc-200">
              INT. THE GILDED TANKARD — NIGHT
            </span>
            <span className="font-mono text-[10px] text-zinc-500">
              10 shots · classical
            </span>
          </div>
          <p className="mt-1 text-[11px] text-zinc-500">
            Two-character dialogue · demo scene workspace
          </p>
        </Link>
      </main>
    </div>
  );
}
