"use client";

/**
 * The workspace assistant: a chat that proposes edits to the story graph.
 *
 * Contract with the workspace shell — the shell renders this in a fixed 336px
 * right column at full height and passes only the scene it is looking at. The
 * panel owns its own scroll; the shell never scrolls on its behalf.
 *
 * What this is, and what it deliberately is not:
 *
 * The assistant edits the **story graph**, not the render. Every answer comes
 * back as a PROPOSAL — named in the six ops the API already accepts (reassign a
 * line, set an emotion, change pacing, duck ambience, insert a shot, repoint
 * coverage) — which you read before it lands. Apply sends that batch to the
 * existing `POST .../timeline/edits`, all-or-nothing; Discard throws it away.
 * An applied edit marks the rendered audio stale rather than silently
 * re-rendering and spending, and the card says so.
 *
 * Three rules this file keeps:
 *
 * 1. **Nothing here interprets the vocabulary.** Each op arrives with the
 *    server's own wording ("Reassign line 12 from TOM to MARA") and is sent back
 *    byte-identical. A TypeScript re-implementation of what `set_shot_coverage`
 *    means would be a second definition free to drift from the first.
 * 2. **Undo is exact or it is absent.** The server computes the inverse batch
 *    from the values that were actually there before the edit, so Undo replays
 *    real state. Where the vocabulary has no inverse (inserting a shot cannot be
 *    un-inserted) the card says that instead of offering a button that lies.
 * 3. **In mock mode the assistant says it is not configured.** It does not
 *    invent a reply. A judge must never mistake a fixture for a working agent,
 *    which is the same rule that got `MockApi` pulled out of the Docker image.
 *
 * The transcript is the applied-batch history: an applied card keeps its place
 * in the list, labelled and undoable, so "what has this thing done to my scene"
 * is answered by scrolling rather than by a second widget that could disagree.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, API_MODE } from "@/lib/api";
import type { AssistProposal, AssistTurn } from "@/lib/types";
import { describeFailure, FOCUS_RING, type Failure } from "@/components/casting/theme";

export interface AssistantPanelProps {
  /** Project the scene belongs to. */
  projectId: string;
  /** Scene ordinal currently open in the workspace. */
  sceneOrdinal: number;
  /**
   * Called after edits are applied to the story graph, so the shell can
   * refetch the timeline and scene. Applying is the panel's job; refetching
   * is the shell's.
   */
  onEditsApplied?: () => void;
}

/** What happened to a proposal. `stale` means the scene moved under it — the
 * batch was validated against a state that is no longer current, so applying it
 * now may be refused. It stays applicable; the card just stops pretending. */
type Outcome = "pending" | "applied" | "discarded" | "undone" | "stale";

interface Entry {
  id: string;
  role: "user" | "assistant" | "error";
  /** The prose half. Empty for error entries, which carry `failure` instead. */
  text: string;
  proposal?: AssistProposal;
  outcome?: Outcome;
  /** A failed request, shown in the server's own words. */
  failure?: Failure;
  /** A failed Apply/Undo on this card specifically. */
  actionFailure?: Failure;
  /** Set after Apply, when the stored render no longer matches the IR. */
  staleReasons?: string[];
}

/** Turns sent back with each message. The assistant is stateless, so continuity
 * is the panel's to carry; the backend caps history at 20, and a shorter window
 * keeps the prompt (and therefore the charge) small. */
const HISTORY_TURNS = 12;

const MAX_MESSAGE_CHARS = 4000;

const EXAMPLES = [
  "Mara sounds too cheerful in the doorway beat",
  "Tighten the pacing through the argument",
  "Add a reaction shot on Tom's last line",
];

let seq = 0;
const nextId = (): string => `e${(seq += 1)}`;

// --------------------------------------------------------------------------- //

function SparkIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#fbbf24"
      strokeWidth="1.6"
      strokeLinecap="round"
      aria-hidden
    >
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
    </svg>
  );
}

function PanelChrome({
  sceneOrdinal,
  appliedCount,
  children,
}: {
  sceneOrdinal: number;
  appliedCount?: number;
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col border-l border-[var(--hairline)] bg-[rgba(10,10,11,0.6)]">
      <div className="flex h-[42px] flex-none items-center gap-2.5 border-b border-[var(--hairline)] px-4">
        <SparkIcon />
        <span className="text-[12.5px] text-zinc-200">Assistant</span>
        <div className="grow" />
        {appliedCount ? (
          <span className="rounded-full bg-emerald-500/12 px-1.5 py-px font-mono text-[9px] text-emerald-300">
            {appliedCount} applied
          </span>
        ) : null}
        <span className="font-mono text-[10px] text-zinc-600">
          scene {sceneOrdinal}
        </span>
      </div>
      {children}
    </div>
  );
}

/** The failure copy, in the server's words. The `detail` is never rewritten:
 * a 503 from this API names the environment variables to set, which is the
 * single most useful sentence a misconfigured deployment can show. */
function FailureNote({ failure }: { failure: Failure }) {
  return (
    <div className="rounded-lg border border-rose-500/30 bg-rose-500/[0.07] px-2.5 py-2">
      <p className="text-[11.5px] font-medium text-rose-200">{failure.headline}</p>
      <p className="mt-1 break-words font-mono text-[10.5px] leading-relaxed text-rose-100/80">
        {failure.detail}
      </p>
      {failure.hint ? (
        <p className="mt-1.5 text-[10.5px] leading-relaxed text-zinc-400">
          {failure.hint}
        </p>
      ) : null}
    </div>
  );
}

function OutcomeTag({ outcome }: { outcome: Outcome }) {
  const style: Record<Outcome, string> = {
    pending: "text-zinc-500",
    applied: "text-emerald-300",
    discarded: "text-zinc-500",
    undone: "text-zinc-400",
    stale: "text-amber-300",
  };
  const label: Record<Outcome, string> = {
    pending: "proposed",
    applied: "applied",
    discarded: "discarded",
    undone: "undone",
    stale: "out of date",
  };
  return (
    <span className={`font-mono text-[9px] uppercase tracking-wide ${style[outcome]}`}>
      {label[outcome]}
    </span>
  );
}

// --------------------------------------------------------------------------- //

export default function AssistantPanel({
  projectId,
  sceneOrdinal,
  onEditsApplied,
}: AssistantPanelProps) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const [busyEntry, setBusyEntry] = useState<string | null>(null);

  // The conversation belongs to one scene. Reset during render (the documented
  // pattern for reacting to a changed prop) rather than in an effect, which
  // would show the previous scene's transcript for a frame first.
  const sceneKey = `${projectId}#${sceneOrdinal}`;
  const [lastKey, setLastKey] = useState(sceneKey);
  if (sceneKey !== lastKey) {
    setLastKey(sceneKey);
    setEntries([]);
    setDraft("");
    setThinking(false);
    setBusyEntry(null);
  }

  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries, thinking]);

  const patch = useCallback((id: string, next: Partial<Entry>) => {
    setEntries((prev) =>
      prev.map((e) => (e.id === id ? { ...e, ...next } : e)),
    );
  }, []);

  const send = useCallback(
    async (message: string) => {
      const text = message.trim();
      if (!text || thinking) return;

      const history: AssistTurn[] = entries
        .filter((e): e is Entry & { role: "user" | "assistant" } =>
          e.role !== "error" && e.text.length > 0,
        )
        .slice(-HISTORY_TURNS)
        .map((e) => ({ role: e.role, content: e.text }));

      setEntries((prev) => [
        ...prev,
        { id: nextId(), role: "user", text },
      ]);
      setDraft("");
      setThinking(true);
      try {
        const proposal = await api.assist(projectId, sceneOrdinal, {
          message: text,
          history,
        });
        setEntries((prev) => [
          ...prev,
          {
            id: nextId(),
            role: "assistant",
            text: proposal.reply,
            proposal,
            outcome: proposal.edits.length ? "pending" : undefined,
          },
        ]);
      } catch (err: unknown) {
        setEntries((prev) => [
          ...prev,
          { id: nextId(), role: "error", text: "", failure: describeFailure(err) },
        ]);
      } finally {
        setThinking(false);
      }
    },
    [entries, projectId, sceneOrdinal, thinking],
  );

  /** Apply, or undo, one card's batch — both go through the same endpoint, so
   * they are the same call with a different list of ops. */
  const runBatch = useCallback(
    async (entry: Entry, kind: "apply" | "undo") => {
      const proposal = entry.proposal;
      if (!proposal || busyEntry) return;
      const edits = kind === "apply" ? proposal.edits.map((e) => e.edit) : proposal.undo;
      if (!edits.length) return;

      setBusyEntry(entry.id);
      patch(entry.id, { actionFailure: undefined });
      try {
        const applied = await api.applyTimelineEdits(projectId, sceneOrdinal, edits);
        patch(entry.id, {
          outcome: kind === "apply" ? "applied" : "undone",
          staleReasons: applied.stale ? applied.staleReasons : undefined,
        });
        // Every other pending proposal was screened against a scene that has
        // now moved, so it stops claiming to be current.
        setEntries((prev) =>
          prev.map((e) =>
            e.id !== entry.id && e.outcome === "pending"
              ? { ...e, outcome: "stale" }
              : e,
          ),
        );
        onEditsApplied?.();
      } catch (err: unknown) {
        patch(entry.id, { actionFailure: describeFailure(err) });
      } finally {
        setBusyEntry(null);
      }
    },
    [busyEntry, onEditsApplied, patch, projectId, sceneOrdinal],
  );

  // --- Mock mode ----------------------------------------------------------- //
  // No model, so no answer. Saying "not configured" is the whole point: a canned
  // reply here would be indistinguishable from a working agent.
  if (API_MODE === "mock") {
    return (
      <PanelChrome sceneOrdinal={sceneOrdinal}>
        <div className="flex grow flex-col items-center justify-center gap-2 px-6 text-center">
          <p className="text-[12.5px] leading-relaxed text-zinc-500">
            The assistant is not configured on this deployment.
          </p>
          <p className="text-[11px] leading-relaxed text-zinc-600">
            It proposes real edits from a live model, so it is never faked here.
            Point the app at a backend with{" "}
            <code className="font-mono text-[10px] text-zinc-500">
              GOOGLE_CLOUD_PROJECT
            </code>{" "}
            set to switch it on.
          </p>
        </div>
      </PanelChrome>
    );
  }

  const appliedCount = entries.filter((e) => e.outcome === "applied").length;

  return (
    <PanelChrome sceneOrdinal={sceneOrdinal} appliedCount={appliedCount}>
      <div
        ref={listRef}
        role="log"
        aria-live="polite"
        aria-label="Assistant conversation"
        className="flex min-h-0 grow flex-col gap-2.5 overflow-y-auto px-3 py-3"
      >
        {entries.length === 0 && !thinking ? (
          <div className="my-auto px-1 text-center">
            <p className="text-[12px] leading-relaxed text-zinc-400">
              Ask for a change to this scene.
            </p>
            <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-600">
              Every answer comes back as a proposal in the edit vocabulary. You
              read it, then apply or discard it — nothing lands on its own.
            </p>
            <div className="mt-3 flex flex-col gap-1.5">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setDraft(example)}
                  className={`rounded-md border border-[var(--hairline)] bg-white/[0.02] px-2.5 py-1.5 text-left text-[11px] text-zinc-500 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-300 ${FOCUS_RING}`}
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {entries.map((entry) => {
          if (entry.role === "error" && entry.failure) {
            return <FailureNote key={entry.id} failure={entry.failure} />;
          }
          if (entry.role === "user") {
            return (
              <p
                key={entry.id}
                className="self-end max-w-[92%] whitespace-pre-wrap break-words rounded-lg rounded-br-sm border border-amber-500/20 bg-amber-500/[0.07] px-2.5 py-1.5 text-[12px] leading-relaxed text-amber-50"
              >
                {entry.text}
              </p>
            );
          }

          const proposal = entry.proposal;
          const busy = busyEntry === entry.id;
          const outcome = entry.outcome ?? "pending";
          const canUndo =
            outcome === "applied" && proposal !== undefined && proposal.undo.length > 0;

          return (
            <div key={entry.id} className="flex flex-col gap-2">
              <p className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-zinc-300">
                {entry.text}
              </p>

              {proposal && proposal.edits.length > 0 ? (
                <div
                  className={`rounded-lg border px-2.5 py-2 ${
                    outcome === "applied"
                      ? "border-emerald-500/25 bg-emerald-500/[0.05]"
                      : outcome === "discarded"
                        ? "border-[var(--hairline)] bg-white/[0.01] opacity-60"
                        : "border-amber-500/25 bg-amber-500/[0.04]"
                  }`}
                >
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
                      Proposal
                    </span>
                    <div className="grow" />
                    <OutcomeTag outcome={outcome} />
                  </div>

                  <ol className="flex flex-col gap-1">
                    {proposal.edits.map((proposed, i) => (
                      <li
                        key={`${entry.id}-${i}`}
                        className="flex gap-1.5 text-[11.5px] leading-relaxed text-zinc-200"
                      >
                        <span
                          className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-amber-400/70"
                          aria-hidden
                        />
                        <span className="min-w-0 break-words">{proposed.summary}</span>
                      </li>
                    ))}
                  </ol>

                  {outcome === "stale" ? (
                    <p className="mt-2 text-[10.5px] leading-relaxed text-amber-300/90">
                      The scene changed after this was proposed, so applying it
                      may be refused. Ask again for a batch checked against the
                      scene as it stands.
                    </p>
                  ) : null}

                  {entry.staleReasons ? (
                    <p className="mt-2 text-[10.5px] leading-relaxed text-zinc-400">
                      The rendered audio no longer matches the story graph
                      {entry.staleReasons.length
                        ? `: ${entry.staleReasons.join("; ")}`
                        : ""}
                      . Re-render the scene to hear it.
                    </p>
                  ) : null}

                  {entry.actionFailure ? (
                    <div className="mt-2">
                      <FailureNote failure={entry.actionFailure} />
                    </div>
                  ) : null}

                  <div className="mt-2 flex items-center gap-1.5">
                    {outcome === "pending" || outcome === "stale" ? (
                      <>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runBatch(entry, "apply")}
                          className={`rounded-md border border-amber-400/40 bg-amber-500/15 px-2 py-1 text-[11px] font-medium text-amber-100 transition-colors hover:bg-amber-500/25 disabled:opacity-50 ${FOCUS_RING}`}
                        >
                          {busy ? "Applying…" : "Apply"}
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => patch(entry.id, { outcome: "discarded" })}
                          className={`rounded-md border border-[var(--hairline)] px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-200 disabled:opacity-50 ${FOCUS_RING}`}
                        >
                          Discard
                        </button>
                      </>
                    ) : null}

                    {canUndo ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void runBatch(entry, "undo")}
                        title={proposal.undo_summary.join("\n")}
                        className={`rounded-md border border-[var(--hairline)] px-2 py-1 text-[11px] text-zinc-400 transition-colors hover:border-[var(--hairline-strong)] hover:text-zinc-200 disabled:opacity-50 ${FOCUS_RING}`}
                      >
                        {busy ? "Undoing…" : "Undo"}
                      </button>
                    ) : null}

                    {outcome === "applied" && !canUndo && proposal.undo_blocked_by ? (
                      <span className="text-[10.5px] leading-tight text-zinc-500">
                        {proposal.undo_blocked_by}
                      </span>
                    ) : null}

                    <div className="grow" />
                    <span
                      className="shrink-0 font-mono text-[9px] text-zinc-600"
                      title="The cost governor's pre-flight estimate, which is what the project was charged — not a measurement of what the provider billed."
                    >
                      est. {proposal.estimated_cost_cents}¢
                    </span>
                  </div>
                </div>
              ) : null}

              {proposal && proposal.undo_blocked_by && outcome === "pending" ? (
                <p className="text-[10.5px] leading-relaxed text-zinc-500">
                  Not undoable once applied — {proposal.undo_blocked_by}.
                </p>
              ) : null}

              {proposal && proposal.dropped.length > 0 ? (
                <details className="group">
                  <summary
                    className={`cursor-pointer list-none text-[10.5px] text-zinc-600 hover:text-zinc-400 ${FOCUS_RING}`}
                  >
                    {proposal.dropped.length} suggestion
                    {proposal.dropped.length === 1 ? "" : "s"} dropped before you
                    saw {proposal.dropped.length === 1 ? "it" : "them"}
                  </summary>
                  <ul className="mt-1 flex flex-col gap-1 border-l border-[var(--hairline)] pl-2">
                    {proposal.dropped.map((reason, i) => (
                      <li
                        key={`${entry.id}-drop-${i}`}
                        className="break-words font-mono text-[10px] leading-relaxed text-zinc-600"
                      >
                        {reason}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              {proposal && proposal.model ? (
                <span className="font-mono text-[9px] text-zinc-700">
                  {proposal.provider} · {proposal.model}
                </span>
              ) : null}
            </div>
          );
        })}

        {thinking ? (
          <div className="flex items-center gap-2 text-[11.5px] text-zinc-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" aria-hidden />
            Reading the scene…
          </div>
        ) : null}
      </div>

      <form
        className="flex-none border-t border-[var(--hairline)] p-2.5"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(draft);
            }
          }}
          rows={2}
          maxLength={MAX_MESSAGE_CHARS}
          disabled={thinking}
          placeholder="Ask for an edit…"
          aria-label="Message the assistant"
          className={`w-full resize-none rounded-md border border-[var(--hairline)] bg-black/30 px-2.5 py-2 text-[12px] leading-relaxed text-zinc-200 placeholder:text-zinc-600 disabled:opacity-50 ${FOCUS_RING}`}
        />
        <div className="mt-1.5 flex items-center gap-2">
          <span className="text-[10px] text-zinc-600">
            Proposes edits; never applies them.
          </span>
          <div className="grow" />
          <button
            type="submit"
            disabled={thinking || draft.trim().length === 0}
            className={`rounded-md border border-amber-400/40 bg-amber-500/15 px-2.5 py-1 text-[11px] font-medium text-amber-100 transition-colors hover:bg-amber-500/25 disabled:opacity-40 ${FOCUS_RING}`}
          >
            Send
          </button>
        </div>
      </form>
    </PanelChrome>
  );
}
