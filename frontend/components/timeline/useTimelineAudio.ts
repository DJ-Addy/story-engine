"use client";

// Owns the client-only placeholder audio engine and keeps it in step with the
// transport. The engine is a procedural synth (no bundled media) — see
// lib/timelineAudio.ts. MUTED by default, so the page is silent on load.
//
// SEAM: swap TimelineAudioEngine for a real <audio> element sourced from the
// rendered mix (GET /api/v1/projects/{id}/render/audio). The wiring below —
// mute sync, play/pause following the transport, and SFX blips from the clock —
// maps 1:1 onto <audio>.muted / play() / pause() / currentTime.

import { useCallback, useEffect, useRef } from "react";
import { useTimelineStore } from "@/lib/timelineStore";
import { TimelineAudioEngine } from "@/lib/timelineAudio";

export function useTimelineAudio() {
  const engineRef = useRef<TimelineAudioEngine | null>(null);
  const muted = useTimelineStore((s) => s.muted);
  const isPlaying = useTimelineStore((s) => s.isPlaying);

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

  // Keep the engine's mute flag mirrored to the store.
  useEffect(() => {
    engineRef.current?.setMuted(muted);
  }, [muted]);

  // The placeholder bed follows the transport (and stays silent while muted).
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    if (isPlaying && !muted) {
      engine.resume().then(() => engine.playBed()).catch(() => {});
    } else {
      engine.stopBed();
    }
  }, [isPlaying, muted]);

  // Call from a user gesture (Play / unmute) so AudioContext.resume() is allowed
  // under the browser autoplay policy.
  const activate = useCallback(() => {
    engineRef.current?.resume().catch(() => {});
  }, []);

  return { engineRef, activate };
}
