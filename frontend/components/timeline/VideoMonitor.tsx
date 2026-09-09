"use client";

// The program monitor: the picture for the shot currently under the playhead.
//
// THE EMPTY STATE IS THE PRIMARY STATE. A Veo render costs real credits, so
// almost every shot has never been rendered, and this editor is built around
// that: with no clip the monitor shows the shot's slate — ordinal, size,
// subjects, intent, timing — and the rest of the editor (lanes, scrubbing,
// audio playback, inspector) is completely unaffected. Four states are told
// apart explicitly and none of them is a broken player:
//
//   none      -> slate + "no video rendered for this shot" + a render affordance
//   rendering -> slate + a scanning bar + elapsed time
//   error     -> slate + the server's own wording (403 rights / 402 cap / 401)
//   ready     -> a muted <video>, or a provider-reference card when the bytes
//                live in a bucket this deployment cannot read through
//
// THE CLOCK IS NOT HERE. `useTransportClock` owns time; this component only
// *follows* it — it seeks and plays/pauses the element from the store and never
// writes currentMs back, so the video can never fight the audio timeline. It
// also subscribes imperatively (via store.subscribe) rather than with a hook,
// so a moving playhead does not re-render the monitor 60 times a second.

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { api, API_MODE } from "@/lib/api";
import { getShotBoard, renderShotBoard } from "@/lib/projectApi";
import { useTimelineStore, videoKey } from "@/lib/timelineStore";
import type { VisualClip } from "@/lib/types";
import { describeFailure, FOCUS_RING } from "@/components/casting/theme";
import { msToClock } from "@/components/timeline/layout";

/** How far the element may drift from the transport before it is re-seeked. */
const SEEK_TOLERANCE_S = 0.18;

/** The API clamps `duration_s` to 1..60. */
const clampDurationS = (ms: number): number =>
  Math.max(1, Math.min(60, Math.round(ms / 1000) || 1));

/** The shot under the playhead: the last clip that has started. Clicking a clip
 * seeks to its start, so selection drives this too, with one rule not two. */
function selectActiveClipId(clips: VisualClip[] | undefined, currentMs: number) {
  if (!clips || clips.length === 0) return null;
  let best = clips[0];
  for (const c of clips) {
    if (c.startMs <= currentMs && c.startMs >= best.startMs) best = c;
  }
  return best.id;
}

// --------------------------------------------------------------------------- //
// Slate — the frame shown whenever there is no picture. Deliberately designed
// rather than blanked: it is what a viewer looks at most of the time.
// --------------------------------------------------------------------------- //

function Slate({
  clip,
  sceneTitle,
  children,
}: {
  clip: VisualClip;
  sceneTitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="absolute inset-0 overflow-hidden">
      {/* Film-slate ground: soft vignette + fine diagonal rake. */}
      <div
        aria-hidden
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 70% at 50% 30%, rgba(56,189,248,0.07), transparent 70%), #08080a",
        }}
      />
      <div
        aria-hidden
        className="absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "repeating-linear-gradient(135deg, rgba(255,255,255,0.035) 0 1px, transparent 1px 9px)",
        }}
      />
      {/* Corner ticks — framing marks, not a border. */}
      {[
        "left-3 top-3 border-l border-t",
        "right-3 top-3 border-r border-t",
        "left-3 bottom-3 border-l border-b",
        "right-3 bottom-3 border-r border-b",
      ].map((pos) => (
        <span
          key={pos}
          aria-hidden
          className={`absolute h-4 w-4 border-white/15 ${pos}`}
        />
      ))}

      {/* Slate header strip */}
      <div className="absolute inset-x-0 top-0 flex items-center gap-2 px-4 py-2.5">
        <span className="rounded bg-sky-400/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-sky-200">
          #{clip.shotOrdinal}
        </span>
        <span className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-zinc-300">
          {clip.size}
        </span>
        <span className="truncate font-mono text-[10px] uppercase tracking-[0.18em] text-zinc-600">
          {sceneTitle}
        </span>
        <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-zinc-600">
          {msToClock(clip.startMs)} · {(clip.durationMs / 1000).toFixed(1)}s
        </span>
      </div>

      {/* State body */}
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-8 text-center">
        {children}
      </div>

      {/* Slate footer: what this shot is meant to be. */}
      <div className="absolute inset-x-0 bottom-0 px-4 py-2.5">
        <p className="truncate text-[11px] leading-snug text-zinc-500">
          {clip.label}
        </p>
        {clip.subjects.length > 0 && (
          <p className="mt-0.5 truncate font-mono text-[10px] text-zinc-600">
            {clip.subjects.join(" · ")}
          </p>
        )}
      </div>
    </div>
  );
}

function StatePill({
  tone,
  children,
}: {
  tone: "idle" | "busy" | "bad" | "good";
  children: React.ReactNode;
}) {
  const skin = {
    idle: "border-[var(--hairline-strong)] bg-white/[0.03] text-zinc-400",
    busy: "border-amber-500/40 bg-amber-500/10 text-amber-200",
    bad: "border-rose-500/40 bg-rose-500/10 text-rose-200",
    good: "border-emerald-500/35 bg-emerald-500/[0.08] text-emerald-200",
  }[tone];
  return (
    <span
      className={`rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider ${skin}`}
    >
      {children}
    </span>
  );
}

// --------------------------------------------------------------------------- //

export default function VideoMonitor() {
  const reduce = useReducedMotion();

  const data = useTimelineStore((s) => s.data);
  const activeId = useTimelineStore((s) =>
    selectActiveClipId(s.data?.lanes.visual, s.currentMs),
  );
  // Returns the stored object, so this selector is stable between shot changes
  // even though it re-evaluates on every clock tick.
  const clip = useTimelineStore(
    (s) => s.data?.lanes.visual.find((c) => c.id === activeId) ?? null,
  );
  const setVideoState = useTimelineStore((s) => s.setVideoState);
  const clearVideos = useTimelineStore((s) => s.clearVideos);

  const sceneOrdinal = data?.sceneOrdinal ?? null;
  const projectId = data?.projectId ?? "";
  const shotOrdinal = clip?.shotOrdinal ?? null;

  // A shot the backend does not have. The "add a reaction shot" AI edit inserts
  // a local clip at ordinal 8.5; the render API takes an integer shot ordinal
  // from a persisted shot list, so that clip is honestly not renderable.
  const renderable =
    shotOrdinal !== null && Number.isInteger(shotOrdinal) && shotOrdinal >= 1;

  // Same key the store writes under — derived from `videoKey`, never retyped.
  const key =
    sceneOrdinal !== null && shotOrdinal !== null
      ? videoKey(sceneOrdinal, shotOrdinal)
      : null;
  const state = useTimelineStore((s) => (key ? s.videos[key] : undefined));
  const status = state?.status ?? "unknown";
  const video = state?.video ?? null;

  // Animatic mode: the shot's drawn frame, cut to the audio by the transport.
  const mode = useTimelineStore((s) => s.monitorMode);
  const setMonitorMode = useTimelineStore((s) => s.setMonitorMode);
  const setBoardState = useTimelineStore((s) => s.setBoardState);
  const board = useTimelineStore((s) => (key ? s.boards[key] : undefined));
  const boardStatus = board?.status ?? "unknown";
  const boardSrc = board?.src ?? null;
  const [boardArmed, setBoardArmed] = useState<string | null>(null);

  // Probe for a board once per shot, in either mode: the video footer says
  // "from its board" when one exists, so the answer is needed regardless.
  useEffect(() => {
    if (API_MODE === "mock" || !renderable || sceneOrdinal === null || shotOrdinal === null) return;
    if (boardStatus !== "unknown") return;
    let cancelled = false;
    setBoardState(sceneOrdinal, shotOrdinal, { status: "probing" });
    getShotBoard(projectId, sceneOrdinal, shotOrdinal)
      .then((src) => {
        if (cancelled) {
          if (src) URL.revokeObjectURL(src);
          return;
        }
        setBoardState(
          sceneOrdinal,
          shotOrdinal,
          src ? { status: "ready", src } : { status: "none", src: null },
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setBoardState(sceneOrdinal, shotOrdinal, {
          status: "error",
          src: null,
          error: describeFailure(err).detail,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [renderable, sceneOrdinal, shotOrdinal, projectId, boardStatus, setBoardState]);

  const drawBoard = useCallback(async () => {
    if (!renderable || sceneOrdinal === null || shotOrdinal === null) return;
    setBoardArmed(null);
    setBoardState(sceneOrdinal, shotOrdinal, { status: "rendering", src: null });
    try {
      await renderShotBoard(projectId, sceneOrdinal, shotOrdinal);
      const src = await getShotBoard(projectId, sceneOrdinal, shotOrdinal);
      setBoardState(
        sceneOrdinal,
        shotOrdinal,
        src ? { status: "ready", src } : { status: "none", src: null },
      );
    } catch (err: unknown) {
      setBoardState(sceneOrdinal, shotOrdinal, {
        status: "error",
        src: null,
        error: describeFailure(err).detail,
      });
    }
  }, [renderable, sceneOrdinal, shotOrdinal, projectId, setBoardState]);

  const videoRef = useRef<HTMLVideoElement>(null);
  const unmounted = useRef(false);

  // "Armed" is remembered per shot rather than reset by an effect, so moving the
  // playhead to another shot disarms the confirmation for free.
  const [armedFor, setArmedFor] = useState<string | null>(null);
  const armed = armedFor !== null && armedFor === activeId;
  const setArmed = useCallback(
    (on: boolean) => setArmedFor(on ? activeId : null),
    [activeId],
  );

  // Elapsed render time. The interval only exists while a render is running and
  // its callback is the only writer; the number itself is derived at render.
  const [renderNow, setRenderNow] = useState(0);
  const startedAt = state?.startedAt ?? null;
  const elapsed =
    status === "rendering" && startedAt !== null
      ? Math.max(0, Math.round((Math.max(renderNow, startedAt) - startedAt) / 1000))
      : 0;

  // Release every blob URL when the editor is left.
  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
      clearVideos();
    };
  }, [clearVideos]);

  // Probe once per shot. A 404 resolves to `none` (the ordinary answer) and is
  // cached, so crossing the same shot again costs nothing.
  useEffect(() => {
    if (!renderable || sceneOrdinal === null || shotOrdinal === null) return;
    if (status !== "unknown") return;

    setVideoState(sceneOrdinal, shotOrdinal, { status: "probing" });
    api
      .getShotVideo(projectId, sceneOrdinal, shotOrdinal)
      .then((v) => {
        if (unmounted.current) {
          if (v?.srcIsObjectUrl && v.src) URL.revokeObjectURL(v.src);
          return;
        }
        setVideoState(
          sceneOrdinal,
          shotOrdinal,
          v ? { status: "ready", video: v } : { status: "none" },
        );
      })
      .catch((err: unknown) => {
        if (unmounted.current) return;
        setVideoState(sceneOrdinal, shotOrdinal, {
          status: "error",
          error: describeFailure(err).detail,
        });
      });
  }, [renderable, sceneOrdinal, shotOrdinal, projectId, status, setVideoState]);

  // Ticks the elapsed readout once a second while a render is in flight.
  useEffect(() => {
    if (status !== "rendering") return;
    const id = window.setInterval(() => setRenderNow(performance.now()), 1000);
    return () => window.clearInterval(id);
  }, [status]);

  // --- follow the transport ------------------------------------------------ //
  // Imperative on purpose: no React render happens per frame, and the store is
  // read, never written. The transport clock stays the single source of time.
  useEffect(() => {
    const el = videoRef.current;
    if (!el || !clip || status !== "ready" || !video?.src) return;

    const sync = () => {
      const s = useTimelineStore.getState();
      const localS = (s.currentMs - clip.startMs) / 1000;
      const media = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : null;
      // Clamp into the clip AND into the clip's real length: a 5s Veo render
      // under a 7s shot parks on its last frame rather than looping or seeking
      // out of range.
      const target = Math.max(0, media === null ? localS : Math.min(localS, media));
      if (Math.abs(el.currentTime - target) > SEEK_TOLERANCE_S) {
        try {
          el.currentTime = target;
        } catch {
          // Not seekable yet (metadata still loading); the next tick retries.
        }
      }

      const inside =
        s.currentMs >= clip.startMs && s.currentMs < clip.startMs + clip.durationMs;
      const past = media !== null && localS >= media;
      if (s.isPlaying && inside && !past) {
        if (el.paused) el.play().catch(() => {});
      } else if (!el.paused) {
        el.pause();
      }
    };

    sync();
    return useTimelineStore.subscribe(sync);
  }, [clip, status, video?.src]);

  const startRender = useCallback(async () => {
    if (!renderable || sceneOrdinal === null || shotOrdinal === null || !clip) return;
    setArmed(false);
    setVideoState(sceneOrdinal, shotOrdinal, {
      status: "rendering",
      startedAt: performance.now(),
    });
    try {
      await api.renderShotVideo(projectId, {
        scene_ordinal: sceneOrdinal,
        shot_ordinal: shotOrdinal,
        duration_s: clampDurationS(clip.durationMs),
      });
      // The POST reports what was made; the GET is what can be played, and it
      // handles both the stored-bytes and provider-URL deliveries.
      const v = await api.getShotVideo(projectId, sceneOrdinal, shotOrdinal);
      if (unmounted.current) {
        if (v?.srcIsObjectUrl && v.src) URL.revokeObjectURL(v.src);
        return;
      }
      setVideoState(
        sceneOrdinal,
        shotOrdinal,
        v
          ? { status: "ready", video: v }
          : {
              status: "error",
              error:
                "The render reported success but no clip could be fetched back.",
            },
      );
    } catch (err) {
      if (unmounted.current) return;
      setVideoState(sceneOrdinal, shotOrdinal, {
        status: "error",
        error: describeFailure(err).detail,
      });
    }
  }, [renderable, sceneOrdinal, shotOrdinal, clip, projectId, setVideoState, setArmed]);

  const retry = useCallback(() => {
    if (sceneOrdinal === null || shotOrdinal === null) return;
    setVideoState(sceneOrdinal, shotOrdinal, { status: "unknown" });
  }, [sceneOrdinal, shotOrdinal, setVideoState]);

  // --- chrome -------------------------------------------------------------- //

  const pill =
    !data || !clip ? null : status === "ready" && video?.src ? (
      <StatePill tone="good">render available</StatePill>
    ) : status === "ready" ? (
      <StatePill tone="idle">stored remotely</StatePill>
    ) : status === "rendering" ? (
      <StatePill tone="busy">rendering</StatePill>
    ) : status === "error" ? (
      <StatePill tone="bad">render failed</StatePill>
    ) : status === "probing" || status === "unknown" ? (
      <StatePill tone="idle">checking…</StatePill>
    ) : (
      <StatePill tone="idle">no render</StatePill>
    );

  const boardPill =
    !data || !clip ? null : boardStatus === "ready" ? (
      <StatePill tone="good">board drawn</StatePill>
    ) : boardStatus === "rendering" ? (
      <StatePill tone="busy">drawing</StatePill>
    ) : boardStatus === "error" ? (
      <StatePill tone="bad">board failed</StatePill>
    ) : boardStatus === "probing" || boardStatus === "unknown" ? (
      <StatePill tone="idle">checking…</StatePill>
    ) : (
      <StatePill tone="idle">no board</StatePill>
    );

  return (
    // Height is INTRINSIC: header + the 16:9 stage + footer. The workspace
    // derives the monitor's width from the height it can spare (.ws-monitor-fit
    // in globals.css), so the stage keeps its exact 16:9 box at every size and
    // the panel never has to squash it to fit.
    <div className="cast-panel flex flex-col">
      <div className="flex items-center gap-2.5 border-b border-[var(--hairline)] px-4 py-3">
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: "rgb(var(--tl-visual))" }}
          aria-hidden
        />
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-300">
          Program monitor
        </h2>
        {mode === "video" ? pill : boardPill}
        <div
          role="tablist"
          aria-label="Monitor mode"
          className="ml-2 flex overflow-hidden rounded-md border border-[var(--hairline)]"
        >
          {(["animatic", "video"] as const).map((m) => (
            <button
              key={m}
              role="tab"
              aria-selected={mode === m}
              onClick={() => setMonitorMode(m)}
              className={`px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider transition-colors ${
                mode === m
                  ? "bg-amber-400 text-zinc-950"
                  : "bg-[var(--surface-3)] text-zinc-400 hover:text-zinc-100"
              } ${FOCUS_RING}`}
            >
              {m}
            </button>
          ))}
        </div>
        {clip && (
          <span className="ml-auto truncate font-mono text-[10px] text-zinc-600">
            shot {clip.shotOrdinal} of {data?.lanes.visual.length ?? 0}
          </span>
        )}
      </div>

      {/* 16:9 stage. Always present, always the same size — the editor's layout
          never shifts because a render does or does not exist. */}
      <div className="relative w-full overflow-hidden bg-black" style={{ aspectRatio: "16 / 9" }}>
        {/* No timeline yet */}
        {!data && <div className="cast-shimmer absolute inset-0" aria-busy />}

        {/* A timeline with no visual lane at all */}
        {data && !clip && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-8 text-center">
            <p className="text-sm font-medium text-zinc-300">No shots in this scene</p>
            <p className="max-w-sm text-xs leading-relaxed text-zinc-500">
              The visual lane is empty, so there is nothing to show a picture for.
              Author a shot list in the scene workspace and it will appear here.
            </p>
          </div>
        )}

        {/* ANIMATIC: the drawn frame, held for the shot's duration. */}
        {mode === "animatic" && clip && boardStatus === "ready" && boardSrc && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={boardSrc}
            alt={`Storyboard frame for shot ${clip.shotOrdinal}`}
            className="absolute inset-0 h-full w-full object-contain"
          />
        )}
        {mode === "animatic" && clip && boardStatus === "rendering" && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <p className="text-sm font-medium text-amber-100">Drawing shot #{clip.shotOrdinal}…</p>
            <p className="font-mono text-[10px] text-zinc-500">
              Gemini is composing the frame from the shot spec
            </p>
          </Slate>
        )}
        {mode === "animatic" && clip && renderable && (boardStatus === "probing" || boardStatus === "unknown") && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-zinc-600">
              checking for a board
            </p>
          </Slate>
        )}
        {mode === "animatic" && clip && (boardStatus === "none" || boardStatus === "error" || (!renderable && boardStatus !== "rendering")) && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <p className="text-sm font-medium text-zinc-300">
              {boardStatus === "error" ? "Board unavailable" : "No board drawn for this shot"}
            </p>
            <p className="max-w-md text-xs leading-relaxed text-zinc-500">
              {boardStatus === "error"
                ? (board?.error ?? "The request failed.")
                : renderable
                  ? "The animatic shows one drawn frame per shot, cut to the audio. Draw this one here, or draw the whole scene from the pipeline."
                  : "This shot only exists in the local edit and cannot be drawn until the shot list is saved."}
            </p>
          </Slate>
        )}

        {/* READY + playable: the real thing. */}
        {mode === "video" && clip && status === "ready" && video?.src && (
          <video
            ref={videoRef}
            src={video.src}
            // Muted always: the rendered mix on the timeline is the audio, and a
            // second audio source would mean a second clock.
            muted
            playsInline
            preload="metadata"
            className="absolute inset-0 h-full w-full object-contain"
            onError={() =>
              sceneOrdinal !== null &&
              shotOrdinal !== null &&
              setVideoState(sceneOrdinal, shotOrdinal, {
                status: "error",
                error: "The browser could not decode this clip.",
              })
            }
          />
        )}

        {/* READY but not playable here: a provider reference, not a dead player. */}
        {mode === "video" && clip && status === "ready" && !video?.src && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <p className="text-sm font-medium text-zinc-200">
              Rendered, stored outside this app
            </p>
            <p className="max-w-md text-xs leading-relaxed text-zinc-500">
              The provider delivered this clip to object storage and this
              deployment cannot read it back, so there is nothing to play here.
            </p>
            {video?.providerUrls.length ? (
              <p className="max-w-md break-all rounded-md border border-[var(--hairline)] bg-black/40 px-2 py-1 font-mono text-[10px] text-zinc-500">
                {video.providerUrls[0]}
              </p>
            ) : null}
          </Slate>
        )}

        {/* PROBING */}
        {mode === "video" && clip && (status === "probing" || status === "unknown") && renderable && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-zinc-600">
              checking for a render
            </p>
          </Slate>
        )}

        {/* NO RENDER — the ordinary case, and the one this editor is built for. */}
        {mode === "video" && clip && (status === "none" || (!renderable && status === "unknown")) && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <span
              aria-hidden
              className="flex h-11 w-11 items-center justify-center rounded-full border border-[var(--hairline-strong)] bg-white/[0.03] text-zinc-500"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="2.5" y="6" width="13" height="12" rx="2" />
                <path d="m15.5 10.5 6-3.5v10l-6-3.5" />
              </svg>
            </span>
            <p className="text-sm font-medium text-zinc-300">
              No video rendered for this shot
            </p>
            <p className="max-w-md text-xs leading-relaxed text-zinc-500">
              {renderable
                ? "The scene still plays: scrub, listen, and inspect every lane. Render this shot when you want a picture to go with it."
                : "This shot only exists in the local edit — it has no ordinal in the stored shot list, so it cannot be rendered until the shot list is saved."}
            </p>
          </Slate>
        )}

        {/* RENDERING */}
        {mode === "video" && clip && status === "rendering" && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <p className="text-sm font-medium text-amber-100">
              Rendering shot #{clip.shotOrdinal}…
            </p>
            <div className="relative h-0.5 w-56 overflow-hidden rounded-full bg-white/[0.08]">
              {!reduce && (
                <motion.div
                  className="absolute inset-y-0 w-1/3 bg-gradient-to-r from-transparent via-amber-400 to-transparent"
                  initial={{ x: "-120%" }}
                  animate={{ x: "420%" }}
                  transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut" }}
                />
              )}
            </div>
            <p className="font-mono text-[10px] tabular-nums text-zinc-500">
              {elapsed}s elapsed · the editor stays usable while this runs
            </p>
          </Slate>
        )}

        {/* ERROR */}
        {mode === "video" && clip && status === "error" && (
          <Slate clip={clip} sceneTitle={data?.sceneTitle ?? ""}>
            <span
              aria-hidden
              className="flex h-11 w-11 items-center justify-center rounded-full border border-rose-500/40 text-rose-300"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              >
                <path d="M12 7v6" />
                <path d="M12 16.5v.01" />
              </svg>
            </span>
            <p className="text-sm font-medium text-rose-100">Render unavailable</p>
            <p className="max-w-md text-xs leading-relaxed text-zinc-400">
              {state?.error ?? "The request failed."}
            </p>
          </Slate>
        )}
      </div>

      {/* Monitor footer: the affordance, and the honest caveats. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-[var(--hairline)] px-4 py-2.5">
        <p className="min-w-0 flex-1 text-[10px] leading-relaxed text-zinc-600">
          {mode === "animatic"
            ? boardStatus === "ready"
              ? "Animatic: the drawn frame is held for the shot and cut to the rendered mix by the transport."
              : "Animatic mode shows a drawn frame per shot, cut to the audio. Boards cost cents; switch to Video to render a shot with Veo."
            : status === "ready" && video?.src
              ? "Picture follows the timeline clock; clip audio is muted so the rendered mix stays the only sound."
              : boardStatus === "ready"
                ? "This shot has a board, so Veo will animate that frame (image-to-video) rather than work from the prompt alone."
                : "Video is optional here — the timeline, transport and inspector work with or without it."}
        </p>

        {mode === "video" && clip && status === "error" && (
          <button
            onClick={retry}
            className={`shrink-0 rounded-md border border-[var(--hairline-strong)] px-2.5 py-1 font-mono text-[10px] text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100 ${FOCUS_RING}`}
          >
            check again
          </button>
        )}

        {mode === "animatic" && clip && renderable && (boardStatus === "none" || boardStatus === "error") && (
          <div className="flex shrink-0 items-center gap-2">
            {boardArmed === activeId ? (
              <>
                <span className="font-mono text-[10px] text-amber-300">
                  {API_MODE === "mock" ? "mock mode has no provider —" : "Gemini image · about 4¢"}
                </span>
                <button
                  onClick={drawBoard}
                  disabled={API_MODE === "mock"}
                  className={`rounded-md bg-amber-400 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-zinc-950 hover:bg-amber-300 disabled:opacity-40 ${FOCUS_RING}`}
                >
                  draw it
                </button>
                <button
                  onClick={() => setBoardArmed(null)}
                  className={`rounded-md border border-[var(--hairline-strong)] px-2 py-1 font-mono text-[10px] text-zinc-400 hover:text-zinc-100 ${FOCUS_RING}`}
                >
                  cancel
                </button>
              </>
            ) : (
              <button
                onClick={() => setBoardArmed(activeId)}
                className={`rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-amber-200 hover:bg-amber-500/20 ${FOCUS_RING}`}
              >
                draw this board
              </button>
            )}
          </div>
        )}

        <AnimatePresence initial={false} mode="popLayout">
          {mode === "video" && clip && renderable && (status === "none" || status === "error") && (
            <motion.div
              key={armed ? "confirm" : "arm"}
              initial={reduce ? false : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0, y: -4 }}
              transition={{ duration: 0.16 }}
              className="flex shrink-0 items-center gap-2"
            >
              {armed ? (
                <>
                  <span className="font-mono text-[10px] text-amber-300">
                    {API_MODE === "mock"
                      ? "mock mode has no provider —"
                      : `spends provider credits · ${clampDurationS(clip.durationMs)}s`}
                  </span>
                  <button
                    onClick={startRender}
                    className={`rounded-md bg-amber-400 px-3 py-1 text-[11px] font-semibold text-zinc-950 transition-colors hover:bg-amber-300 ${FOCUS_RING}`}
                  >
                    Confirm render
                  </button>
                  <button
                    onClick={() => setArmed(false)}
                    className={`rounded-md px-1.5 py-1 font-mono text-[10px] text-zinc-500 transition-colors hover:text-zinc-300 ${FOCUS_RING}`}
                  >
                    cancel
                  </button>
                </>
              ) : (
                <button
                  onClick={() => setArmed(true)}
                  className={`rounded-md border border-amber-500/35 bg-amber-500/[0.08] px-3 py-1 text-[11px] font-medium text-amber-100 transition-colors hover:border-amber-400/70 hover:bg-amber-500/15 ${FOCUS_RING}`}
                >
                  Render this shot
                </button>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {clip && status === "ready" && video && (video.provider || video.durationMs) && (
          <span className="shrink-0 font-mono text-[10px] text-zinc-600">
            {[video.provider, video.model].filter(Boolean).join(" · ")}
            {video.durationMs ? ` · ${(video.durationMs / 1000).toFixed(1)}s` : ""}
          </span>
        )}
      </div>
    </div>
  );
}
