"use client";

// Owns the one <audio> element for the workspace and keeps it in step with the
// transport. When a timeline with measured (`rendered`) timings loads, the
// scene's mix is fetched from the API and handed to the engine; when the
// timings are only estimated there is no mix, and the engine stays silent
// rather than inventing one — the transport still scrubs, it just has nothing
// to play, and the mute button says so.
//
// The engine follows the store imperatively (store.subscribe) rather than via
// hooks, for the same reason the program monitor does: a moving playhead must
// not re-render this component sixty times a second.

import { useCallback, useEffect, useRef } from "react";
import { useTimelineStore } from "@/lib/timelineStore";
import { TimelineAudioEngine } from "@/lib/timelineAudio";
import { getSceneAudio } from "@/lib/projectApi";
import { API_MODE } from "@/lib/api";

export function useTimelineAudio() {
  const engineRef = useRef<TimelineAudioEngine | null>(null);
  const muted = useTimelineStore((s) => s.muted);
  const isPlaying = useTimelineStore((s) => s.isPlaying);
  const projectId = useTimelineStore((s) => s.data?.projectId ?? null);
  const sceneOrdinal = useTimelineStore((s) => s.data?.sceneOrdinal ?? null);
  const timingSource = useTimelineStore((s) => s.data?.timingSource ?? null);
  const setAudioAvailable = useTimelineStore((s) => s.setAudioAvailable);

  // Construct the engine client-side only, and tear it down on unmount.
  useEffect(() => {
    const engine = new TimelineAudioEngine();
    engine.setMuted(useTimelineStore.getState().muted);
    engineRef.current = engine;
    return () => {
      engine.dispose();
      engineRef.current = null;
    };
  }, []);

  // Fetch the rendered mix for the loaded scene. Only a `rendered` timeline has
  // one: an estimated timeline is the planner's guess and there is no WAV
  // behind it. The mock has no fixture audio, so it skips the request.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    if (
      API_MODE === "mock" ||
      projectId === null ||
      sceneOrdinal === null ||
      timingSource !== "rendered"
    ) {
      void engine.load(null);
      setAudioAvailable(false);
      return;
    }
    let cancelled = false;
    getSceneAudio(projectId, sceneOrdinal)
      .then(async (src) => {
        if (cancelled) {
          if (src) URL.revokeObjectURL(src);
          return;
        }
        // The engine decodes the whole mix before reporting it playable, so
        // "sound" on the transport bar means sound, not a pending fetch.
        const ok = await engine.load(src);
        if (cancelled) return;
        setAudioAvailable(ok);
        const s = useTimelineStore.getState();
        engine.syncTime(s.currentMs);
        if (ok && s.isPlaying) engine.playBed();
      })
      .catch(() => {
        if (cancelled) return;
        void engine.load(null);
        setAudioAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, sceneOrdinal, timingSource, setAudioAvailable]);

  // Mirror the mute flag.
  useEffect(() => {
    engineRef.current?.setMuted(muted);
  }, [muted]);

  // Play / pause follow the transport.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    if (isPlaying) engine.playBed();
    else engine.stopBed();
  }, [isPlaying]);

  // Seek follows the clock — every tick and every scrub — without re-rendering.
  useEffect(() => {
    return useTimelineStore.subscribe((s, prev) => {
      if (s.currentMs !== prev.currentMs) engineRef.current?.syncTime(s.currentMs);
    });
  }, []);

  // Call from a user gesture (Play / unmute) so the browser lets audio start.
  const activate = useCallback(() => {
    engineRef.current?.resume().catch(() => {});
  }, []);

  return { engineRef, activate };
}
