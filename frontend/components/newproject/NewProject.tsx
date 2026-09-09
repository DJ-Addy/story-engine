"use client";

// Bring a manuscript in. A screenplay (.fountain / .fdx / .txt in Fountain) goes
// straight to the parser; prose (.txt) goes through the novel converter, which
// attributes the quotes and emits Fountain so it re-enters the same pipeline.
//
// The kind is an explicit switch rather than sniffed from the extension,
// because ".txt" is both: the API accepts it on either route and parses it
// differently. Guessing would silently turn a novel into a screenplay with no
// characters, and the failure would show up three steps later as an empty
// cast. A .fdx or .fountain file flips the switch for you; a .txt leaves it.

import { useRouter } from "next/navigation";
import { useState } from "react";
import AppNav from "@/components/AppNav";
import ApiModeBadge from "@/components/ApiModeBadge";
import { FOCUS_RING } from "@/components/casting/theme";
import { API_MODE } from "@/lib/api";
import { getToken } from "@/lib/apiClient";
import { startDemoSession } from "@/lib/demoApi";
import {
  createProject,
  uploadNovel,
  uploadScript,
  type NovelIngestOut,
  type ScriptUploadOut,
} from "@/lib/projectApi";

type Kind = "screenplay" | "novel";

const errorText = (err: unknown): string =>
  err instanceof Error ? err.message : String(err);

const LABEL = "block font-mono text-[10px] uppercase tracking-wider text-zinc-500";
const FIELD =
  "mt-1.5 w-full rounded-md border border-[var(--hairline)] bg-[var(--surface-3)] px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-amber-500/50 focus:outline-none";

export default function NewProject() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [kind, setKind] = useState<Kind>("novel");
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<
    { projectId: string; ingest: ScriptUploadOut | NovelIngestOut } | null
  >(null);

  const onPick = (f: File | null) => {
    setFile(f);
    if (!f) return;
    const ext = f.name.toLowerCase().split(".").pop() ?? "";
    if (ext === "fdx" || ext === "fountain") setKind("screenplay");
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || !title.trim() || !rights) return;
    setError(null);
    try {
      // A visitor with no account still gets a project: the demo user owns it,
      // the same way the seeded sample is owned. Signing in first makes it
      // yours instead.
      if (!getToken()) {
        setBusy("Starting a session…");
        await startDemoSession();
      }
      setBusy("Creating the project…");
      const project = await createProject({ title: title.trim(), rights_attested: true });
      setBusy(kind === "novel" ? "Converting prose to a screenplay…" : "Parsing the screenplay…");
      const ingest =
        kind === "novel"
          ? await uploadNovel(project.id, file, {
              title: title.trim(),
              author: author.trim() || undefined,
            })
          : await uploadScript(project.id, file);
      setResult({ projectId: project.id, ingest });
      setBusy(null);
      router.push(`/pipeline?project=${encodeURIComponent(project.id)}`);
    } catch (err) {
      setBusy(null);
      setError(errorText(err));
    }
  };

  return (
    <div className="min-h-screen bg-[var(--cast-bg)] text-zinc-200">
      <AppNav>
        <ApiModeBadge />
      </AppNav>

      <main className="mx-auto w-full max-w-3xl px-5 py-10 sm:px-8">
        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-amber-300/80">
          New project
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-50">
          Bring in a script or a book
        </h1>
        <p className="mt-2 max-w-xl text-sm leading-relaxed text-zinc-500">
          The manuscript becomes a story graph: who speaks, where, in what
          order. Everything after that — casting, shots, audio, boards, video —
          renders from that one graph.
        </p>

        {API_MODE === "mock" && (
          <div className="mt-6 rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-xs text-amber-200">
            This build is on mock data and has no backend to upload to. Point it
            at a live API to create projects.
          </div>
        )}

        <form onSubmit={submit} className="cast-panel mt-8 space-y-6 p-6">
          <div className="grid gap-5 sm:grid-cols-2">
            <label className="block">
              <span className={LABEL}>Title</span>
              <input
                className={FIELD}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="The Odyssey — Book XII"
                required
              />
            </label>
            <label className="block">
              <span className={LABEL}>Author (optional)</span>
              <input
                className={FIELD}
                value={author}
                onChange={(e) => setAuthor(e.target.value)}
                placeholder="Homer, tr. Samuel Butler"
              />
            </label>
          </div>

          <fieldset>
            <legend className={LABEL}>What is this file?</legend>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {(
                [
                  ["novel", "A book / prose", "Quotes are attributed to speakers and the text becomes a screenplay first."],
                  ["screenplay", "A screenplay", ".fountain, .fdx, or Fountain-formatted .txt. Parsed as written."],
                ] as const
              ).map(([value, name, hint]) => (
                <label
                  key={value}
                  className={`cursor-pointer rounded-md border px-4 py-3 transition-colors ${
                    kind === value
                      ? "border-amber-500/50 bg-amber-500/10"
                      : "border-[var(--hairline)] bg-[var(--surface-3)] hover:border-[var(--hairline-strong)]"
                  }`}
                >
                  <input
                    type="radio"
                    name="kind"
                    value={value}
                    checked={kind === value}
                    onChange={() => setKind(value)}
                    className="sr-only"
                  />
                  <span className="block text-sm font-medium text-zinc-100">{name}</span>
                  <span className="mt-1 block text-xs leading-relaxed text-zinc-500">{hint}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <label className="block">
            <span className={LABEL}>File</span>
            <input
              type="file"
              accept={kind === "novel" ? ".txt,text/plain" : ".fountain,.fdx,.txt"}
              onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              className="mt-1.5 block w-full text-sm text-zinc-300 file:mr-3 file:rounded-md file:border file:border-[var(--hairline-strong)] file:bg-[var(--surface-3)] file:px-3 file:py-1.5 file:font-mono file:text-[10px] file:uppercase file:tracking-wider file:text-zinc-200"
              required
            />
            {file && (
              <span className="mt-1.5 block font-mono text-[10px] text-zinc-500">
                {file.name} · {(file.size / 1024).toFixed(1)} KB
              </span>
            )}
          </label>

          <label className="flex items-start gap-3 rounded-md border border-[var(--hairline)] bg-[var(--surface-3)] px-4 py-3">
            <input
              type="checkbox"
              checked={rights}
              onChange={(e) => setRights(e.target.checked)}
              className="mt-0.5"
              required
            />
            <span className="text-xs leading-relaxed text-zinc-400">
              I hold the rights to this text, or it is in the public domain.
              Nothing renders — no voices, no video — without this on file.
            </span>
          </label>

          {error && (
            <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-xs text-rose-200">
              {error}
            </div>
          )}

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={busy !== null || !file || !title.trim() || !rights || API_MODE === "mock"}
              className={`rounded-md bg-amber-400 px-4 py-2 text-sm font-medium text-zinc-950 transition-colors hover:bg-amber-300 disabled:cursor-not-allowed disabled:opacity-40 ${FOCUS_RING}`}
            >
              {busy ?? "Create project and ingest"}
            </button>
            <span className="text-xs text-zinc-600">
              Ingest is free — no provider is called until you render.
            </span>
          </div>

          {result && (
            <p className="text-xs text-emerald-300">
              Ingested {result.ingest.scene_count} scene(s), {result.ingest.character_count}{" "}
              character(s). Opening the pipeline…
            </p>
          )}
        </form>
      </main>
    </div>
  );
}
