import { create } from "zustand";
import type { TimelineData, TimelineSelection } from "@/lib/types";

// Zoom is expressed as pixels-per-second of timeline; the lanes and ruler read
// it to lay out clips on a shared horizontal time axis.
const ZOOM_MIN = 24;
const ZOOM_MAX = 320;
const ZOOM_DEFAULT = 80;
const ZOOM_STEP = 1.4;

const clamp = (v: number, lo: number, hi: number): number =>
  Math.max(lo, Math.min(hi, v));

/** Suggested edits the AI-assist strip offers. Applying one runs a small mock
 * mutation on the timeline data in the store (see `applyAiEdit`) — a stub that
 * gestures at "AI as the main source of editing" but operates on mock data. */
export const AI_EDITS = [
  {
    id: "tighten-pacing",
    label: "Tighten pacing",
    description: "Trim dead air between clips so the scene plays ~12% tighter.",
  },
  {
    id: "duck-ambience",
    label: "Duck ambience under dialogue",
    description: "Drop the ambience beds wherever a line is speaking.",
  },
  {
    id: "add-reaction-shot",
    label: "Add a reaction shot",
    description: "Insert a reaction close-up right after Mara's ultimatum.",
  },
] as const;

export type AiEditId = (typeof AI_EDITS)[number]["id"];

interface TimelineState {
  data: TimelineData | null;
  durationMs: number;
  /** Virtual transport clock position, advanced by `tick` on each rAF. */
  currentMs: number;
  isPlaying: boolean;
  /** Zoom: pixels per second of timeline. */
  pxPerSecond: number;
  selection: TimelineSelection | null;
  /** Placeholder audio is muted by default so the page is silent-safe. */
  muted: boolean;
  appliedEdits: string[];

  load(data: TimelineData): void;
  play(): void;
  pause(): void;
  togglePlay(): void;
  stop(): void;
  tick(deltaMs: number): void;
  seek(ms: number): void;
  nudge(deltaMs: number): void;
  setZoom(pxPerSecond: number): void;
  zoomIn(): void;
  zoomOut(): void;
  select(sel: TimelineSelection | null): void;
  selectAndSeek(sel: TimelineSelection): void;
  setMuted(muted: boolean): void;
  toggleMuted(): void;
  applyAiEdit(editId: string): void;
}

/** Deep clone so store edits never mutate the loaded/fixture data in place. */
function cloneTimeline(t: TimelineData): TimelineData {
  return {
    ...t,
    scenes: t.scenes.map((s) => ({ ...s })),
    lanes: {
      visual: t.lanes.visual.map((c) => ({ ...c, subjects: [...c.subjects] })),
      dialogue: t.lanes.dialogue.map((c) => ({ ...c })),
      ambience: t.lanes.ambience.map((b) => ({ ...b })),
      sfx: t.lanes.sfx.map((m) => ({ ...m })),
    },
  };
}

/** The scene's playable length = the latest end across every lane. */
function computeDuration(t: TimelineData): number {
  let max = 0;
  for (const c of t.lanes.visual) max = Math.max(max, c.startMs + c.durationMs);
  for (const c of t.lanes.dialogue) max = Math.max(max, c.startMs + c.durationMs);
  for (const b of t.lanes.ambience) max = Math.max(max, b.startMs + b.durationMs);
  for (const m of t.lanes.sfx) max = Math.max(max, m.atMs);
  return Math.ceil(max);
}

function selectionStart(t: TimelineData, sel: TimelineSelection): number | null {
  switch (sel.lane) {
    case "visual":
      return t.lanes.visual.find((c) => c.id === sel.id)?.startMs ?? null;
    case "dialogue":
      return t.lanes.dialogue.find((c) => c.id === sel.id)?.startMs ?? null;
    case "ambience":
      return t.lanes.ambience.find((b) => b.id === sel.id)?.startMs ?? null;
    case "sfx":
      return t.lanes.sfx.find((m) => m.id === sel.id)?.atMs ?? null;
  }
}

export const useTimelineStore = create<TimelineState>((set, get) => ({
  data: null,
  durationMs: 0,
  currentMs: 0,
  isPlaying: false,
  pxPerSecond: ZOOM_DEFAULT,
  selection: null,
  muted: true,
  appliedEdits: [],

  load(data) {
    set({
      data: cloneTimeline(data),
      durationMs: data.durationMs,
      currentMs: 0,
      isPlaying: false,
      pxPerSecond: ZOOM_DEFAULT,
      selection: null,
      appliedEdits: [],
      // `muted` is intentionally preserved across loads (default true).
    });
  },

  play() {
    if (get().data) set({ isPlaying: true });
  },
  pause() {
    set({ isPlaying: false });
  },
  togglePlay() {
    if (get().isPlaying) set({ isPlaying: false });
    else if (get().data) set({ isPlaying: true });
  },
  stop() {
    set({ isPlaying: false, currentMs: 0 });
  },

  tick(deltaMs) {
    const { isPlaying, currentMs, durationMs } = get();
    if (!isPlaying) return;
    const next = currentMs + deltaMs;
    if (next >= durationMs) {
      // End-stop: park on the last frame and pause.
      set({ currentMs: durationMs, isPlaying: false });
    } else {
      set({ currentMs: next });
    }
  },

  seek(ms) {
    set({ currentMs: clamp(ms, 0, get().durationMs) });
  },
  nudge(deltaMs) {
    get().seek(get().currentMs + deltaMs);
  },

  setZoom(pxPerSecond) {
    set({ pxPerSecond: clamp(pxPerSecond, ZOOM_MIN, ZOOM_MAX) });
  },
  zoomIn() {
    set({ pxPerSecond: clamp(get().pxPerSecond * ZOOM_STEP, ZOOM_MIN, ZOOM_MAX) });
  },
  zoomOut() {
    set({ pxPerSecond: clamp(get().pxPerSecond / ZOOM_STEP, ZOOM_MIN, ZOOM_MAX) });
  },

  select(sel) {
    set({ selection: sel });
  },
  selectAndSeek(sel) {
    const { data, durationMs } = get();
    const start = data ? selectionStart(data, sel) : null;
    if (start === null) set({ selection: sel });
    else set({ selection: sel, currentMs: clamp(start, 0, durationMs) });
  },

  setMuted(muted) {
    set({ muted });
  },
  toggleMuted() {
    set({ muted: !get().muted });
  },

  applyAiEdit(editId) {
    const state = get();
    // Idempotent: an already-applied edit (or an empty timeline) is a no-op.
    if (state.data === null || state.appliedEdits.includes(editId)) return;
    const data = cloneTimeline(state.data);

    if (editId === "tighten-pacing") {
      const f = 0.88;
      for (const c of data.lanes.visual) {
        c.startMs = Math.round(c.startMs * f);
        c.durationMs = Math.round(c.durationMs * f);
      }
      for (const c of data.lanes.dialogue) {
        c.startMs = Math.round(c.startMs * f);
        c.durationMs = Math.round(c.durationMs * f);
      }
      for (const b of data.lanes.ambience) {
        b.startMs = Math.round(b.startMs * f);
        b.durationMs = Math.round(b.durationMs * f);
      }
      for (const m of data.lanes.sfx) m.atMs = Math.round(m.atMs * f);
      for (const s of data.scenes) s.startMs = Math.round(s.startMs * f);
    } else if (editId === "duck-ambience") {
      for (const b of data.lanes.ambience) {
        const bEnd = b.startMs + b.durationMs;
        const overlaps = data.lanes.dialogue.some(
          (d) => b.startMs < d.startMs + d.durationMs && d.startMs < bEnd,
        );
        if (overlaps) b.ducked = true;
      }
    } else if (editId === "add-reaction-shot") {
      const REACTION_MS = 2500;
      const anchor = data.lanes.visual.find((c) => c.shotOrdinal === 8);
      if (!anchor) return;
      const insertionMs = anchor.startMs + anchor.durationMs;
      for (const c of data.lanes.visual) if (c.startMs >= insertionMs) c.startMs += REACTION_MS;
      for (const c of data.lanes.dialogue) if (c.startMs >= insertionMs) c.startMs += REACTION_MS;
      for (const b of data.lanes.ambience) {
        if (b.startMs >= insertionMs) b.startMs += REACTION_MS;
        else if (b.startMs + b.durationMs > insertionMs) b.durationMs += REACTION_MS;
      }
      for (const m of data.lanes.sfx) if (m.atMs >= insertionMs) m.atMs += REACTION_MS;
      for (const s of data.scenes) if (s.startMs >= insertionMs) s.startMs += REACTION_MS;
      data.lanes.visual.push({
        id: "vis-reaction",
        startMs: insertionMs,
        durationMs: REACTION_MS,
        shotOrdinal: 8.5,
        size: "cu",
        label: "Reaction — Voss recoils as the ultimatum lands",
        subjects: ["Voss"],
      });
      data.lanes.visual.sort((a, b) => a.startMs - b.startMs);
    } else {
      return; // Unknown edit id — leave the timeline untouched.
    }

    const durationMs = computeDuration(data);
    data.durationMs = durationMs;
    set({
      data,
      durationMs,
      currentMs: clamp(state.currentMs, 0, durationMs),
      appliedEdits: [...state.appliedEdits, editId],
    });
  },
}));
