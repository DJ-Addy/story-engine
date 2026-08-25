"use client";

// The virtual transport clock. A requestAnimationFrame loop advances the store's
// currentMs while playing, so scrub/playback work with NO audio file at all
// (the real render is wired in later — see useTimelineAudio's SEAM note).
//
// It reads/writes the store via getState() and only *subscribes* to isPlaying,
// so this hook never re-renders per frame. Components that must reflect the
// moving playhead (the playhead line + the clock readout) subscribe to currentMs
// themselves; the lanes deliberately do not.

import { useEffect } from "react";
import type { RefObject } from "react";
import { useTimelineStore } from "@/lib/timelineStore";
import type { TimelineAudioEngine } from "@/lib/timelineAudio";

export function useTransportClock(
  engineRef: RefObject<TimelineAudioEngine | null>,
) {
  const isPlaying = useTimelineStore((s) => s.isPlaying);

  useEffect(() => {
    if (!isPlaying) return;
    let raf = 0;
    let prev = performance.now();

    const step = (now: number) => {
      const delta = now - prev;
      prev = now;

      const store = useTimelineStore.getState();
      const before = store.currentMs;
      store.tick(delta);
      const after = useTimelineStore.getState().currentMs;

      // Trigger a soft blip for every SFX marker the playhead just crossed.
      const engine = engineRef.current;
      if (engine && after > before && store.data) {
        for (const marker of store.data.lanes.sfx) {
          if (marker.atMs > before && marker.atMs <= after) engine.blip();
        }
      }

      // End-stop: pause the transport when we reach the end of the timeline.
      if (after >= useTimelineStore.getState().durationMs) {
        useTimelineStore.getState().pause();
        return;
      }
      raf = requestAnimationFrame(step);
    };

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [isPlaying, engineRef]);
}
