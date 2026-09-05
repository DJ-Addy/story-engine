"use client";

// ONE selection across both halves of the workspace.
//
// The scene half (shot list, axis map, continuity) keys everything off
// `useSceneStore.selectedOrdinal` — a ShotSpec ordinal. The timeline half keys
// everything off `useTimelineStore.selection` — a {lane, id} pair. The bridge
// between them is `VisualClip.shotOrdinal`: the visual lane is built from the
// same shot list, so one shot ordinal names exactly one visual clip.
//
// The two effects below mirror a change in either direction and then stop,
// because each writes only when the other side already disagrees. Selecting a
// shot also seeks the playhead to that clip's start (`selectAndSeek`), which is
// what makes the program monitor follow the shot list — the monitor shows the
// shot under the playhead, so moving the playhead IS how you change what it
// shows. One rule, not two.

import { useEffect } from "react";
import { useSceneStore } from "@/lib/store";
import { useTimelineStore } from "@/lib/timelineStore";

export function useSceneTimelineSync(): void {
  const selectedOrdinal = useSceneStore((s) => s.selectedOrdinal);
  const selection = useTimelineStore((s) => s.selection);
  // Re-run when the timeline is (re)loaded: the clip ids change with it.
  const timelineData = useTimelineStore((s) => s.data);

  // Shot list -> visual lane (+ playhead).
  useEffect(() => {
    if (selectedOrdinal === null || !timelineData) return;
    const clip = timelineData.lanes.visual.find(
      (c) => c.shotOrdinal === selectedOrdinal,
    );
    if (!clip) return; // A shot with no clip on the lane: leave the transport be.
    const current = useTimelineStore.getState().selection;
    if (current?.lane === "visual" && current.id === clip.id) return;
    useTimelineStore.getState().selectAndSeek({ lane: "visual", id: clip.id });
  }, [selectedOrdinal, timelineData]);

  // Visual lane -> shot list.
  useEffect(() => {
    if (!selection || selection.lane !== "visual" || !timelineData) return;
    const clip = timelineData.lanes.visual.find((c) => c.id === selection.id);
    if (!clip) return;
    // The "add a reaction shot" AI edit inserts a local clip at ordinal 8.5,
    // which has no row in the stored shot list. Nothing to select — say nothing
    // rather than selecting a shot that does not exist.
    const shots = useSceneStore.getState().shots;
    if (!shots.some((s) => s.ordinal === clip.shotOrdinal)) return;
    if (useSceneStore.getState().selectedOrdinal === clip.shotOrdinal) return;
    useSceneStore.getState().selectShot(clip.shotOrdinal);
  }, [selection, timelineData]);
}
