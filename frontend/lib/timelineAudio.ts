// The timeline's sound: the rendered scene mix, decoded once with Web Audio
// and played from wherever the transport clock says.
//
// This replaced two earlier engines. The first was a procedural placeholder —
// an oscillator bed and a blip per SFX marker — left in behind a "seam" note
// and never swapped, so a rendered scene played a drone, muted. The second
// was a plain <audio> element fed the mix as a blob URL, which is the obvious
// design and did not work: in the browser it was tested in, the element sat
// at NETWORK_LOADING with readyState 0 and never fired loadedmetadata for a
// valid 11.8 MB PCM WAV — detached or attached to the document — while
// `decodeAudioData` decoded the same bytes in well under a second. A player
// whose loading a browser may defer is not a player a demo can depend on.
//
// Web Audio has no such deferral. The whole mix is decoded into an
// AudioBuffer up front (a four-minute mono scene at 48 kHz is ~45 MB of
// floats, which is fine), and playback is a BufferSource started at an
// offset. Seeking is therefore trivial and exact: stop the source, start a
// new one at the new offset.
//
// THE CLOCK STAYS IN THE STORE. Every tick calls `syncTime`; if the audio's
// own position has drifted from the transport by more than the tolerance it
// is restarted at the transport's position. The audio never writes time back,
// so scrubbing, the playhead and the picture all read one clock.
//
// The method names are kept from the placeholder (`resume`, `setMuted`,
// `playBed`, `stopBed`, `blip`, `dispose`) so `useTransportClock` and the
// transport bar did not have to change. `blip` is a no-op: the SFX are baked
// into the mix.

/** How far playback may drift from the transport before it is restarted at
 * the transport's position. rAF jitter never reaches this; a real seek does. */
const SEEK_TOLERANCE_S = 0.18;

type AudioContextCtor = typeof AudioContext;

export interface TimelineAudioDiagnostics {
  loaded: boolean;
  durationS: number | null;
  playing: boolean;
  muted: boolean;
  contextState: string | null;
  positionS: number;
}

export class TimelineAudioEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private buffer: AudioBuffer | null = null;
  private source: AudioBufferSourceNode | null = null;
  private muted = false;
  /** The object URL the buffer came from, released on replace/dispose. */
  private src: string | null = null;
  /** Transport intent: play or pause, independent of whether a buffer exists
   * yet — a mix that finishes decoding while the transport is rolling should
   * start on its own. */
  private wantPlaying = false;
  /** Where the transport is, in seconds, as of the last sync. */
  private transportS = 0;
  /** ctx.currentTime when the current source started, and the buffer offset it
   * started from — together they give the audio's own position. */
  private startedAtCtx = 0;
  private startedAtOffset = 0;
  /** Generation counter so a stale decode cannot install itself over a newer
   * load (scene switches faster than a 12 MB fetch resolves). */
  private generation = 0;

  private ensure(): AudioContext | null {
    if (this.ctx) return this.ctx;
    if (typeof window === "undefined") return null;
    const Ctor: AudioContextCtor | undefined =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext;
    if (!Ctor) return null;
    const ctx = new Ctor();
    const master = ctx.createGain();
    master.gain.value = this.muted ? 0 : 1;
    master.connect(ctx.destination);
    this.ctx = ctx;
    this.master = master;
    // Never attached to the DOM, so nothing could inspect it. Exposed for
    // diagnosis: "the Odyssey has no sound" should be a question with a
    // readable answer, not an inferred one.
    (window as unknown as { __timelineAudio?: () => TimelineAudioDiagnostics }).__timelineAudio =
      () => this.diagnostics();
    return ctx;
  }

  /** Fetch and decode a rendered mix from an object URL the caller owns; the
   * engine takes over releasing it. Resolves true when the mix is playable.
   * `null` clears the current mix. */
  async load(src: string | null): Promise<boolean> {
    if (this.src === src && (src === null || this.buffer)) return this.buffer !== null;
    const generation = ++this.generation;
    this.stopSource();
    if (this.src) {
      try {
        URL.revokeObjectURL(this.src);
      } catch {
        // Already released, or never a blob URL.
      }
    }
    this.src = src;
    this.buffer = null;
    if (!src) return false;
    const ctx = this.ensure();
    if (!ctx) return false;
    try {
      const bytes = await (await fetch(src)).arrayBuffer();
      const decoded = await ctx.decodeAudioData(bytes);
      if (generation !== this.generation) return false; // superseded
      this.buffer = decoded;
      if (this.wantPlaying) this.startAt(this.transportS);
      return true;
    } catch {
      if (generation === this.generation) this.buffer = null;
      return false;
    }
  }

  hasSource(): boolean {
    return this.buffer !== null;
  }

  /** Call from a user gesture (Play / unmute): browsers keep a context
   * suspended until one, and resuming is what lets sound out. */
  async resume(): Promise<void> {
    const ctx = this.ensure();
    if (!ctx) return;
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
      } catch {
        // The next gesture will succeed.
      }
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.ctx && this.master) {
      this.master.gain.setTargetAtTime(muted ? 0 : 1, this.ctx.currentTime, 0.02);
    }
  }

  /** Transport started. */
  playBed(): void {
    this.wantPlaying = true;
    if (!this.buffer) return;
    void this.resume();
    this.startAt(this.transportS);
  }

  /** Transport paused or stopped. */
  stopBed(): void {
    this.wantPlaying = false;
    this.stopSource();
  }

  /** Keep playback on the transport clock. Called on every tick and every
   * scrub; cheap when nothing has drifted. */
  syncTime(currentMs: number): void {
    this.transportS = Math.max(0, currentMs / 1000);
    if (!this.buffer || !this.wantPlaying) return;
    if (!this.source) {
      this.startAt(this.transportS);
      return;
    }
    const drift = Math.abs(this.positionS() - this.transportS);
    if (drift > SEEK_TOLERANCE_S) this.startAt(this.transportS);
  }

  /** SFX markers are already in the mix. Kept so the clock's call site is
   * unchanged. */
  blip(): void {}

  dispose(): void {
    this.stopBed();
    void this.load(null);
    if (this.ctx) {
      void this.ctx.close().catch(() => {});
      this.ctx = null;
      this.master = null;
    }
  }

  diagnostics(): TimelineAudioDiagnostics {
    return {
      loaded: this.buffer !== null,
      durationS: this.buffer ? +this.buffer.duration.toFixed(2) : null,
      playing: this.source !== null,
      muted: this.muted,
      contextState: this.ctx?.state ?? null,
      positionS: +this.positionS().toFixed(2),
    };
  }

  // --- internals ------------------------------------------------------------ //

  private positionS(): number {
    if (!this.ctx || !this.source) return this.transportS;
    return this.startedAtOffset + (this.ctx.currentTime - this.startedAtCtx);
  }

  private startAt(offsetS: number): void {
    const ctx = this.ensure();
    if (!ctx || !this.master || !this.buffer) return;
    this.stopSource();
    if (offsetS >= this.buffer.duration) return; // past the end: silence
    const source = ctx.createBufferSource();
    source.buffer = this.buffer;
    source.connect(this.master);
    source.onended = () => {
      if (this.source === source) this.source = null;
    };
    source.start(0, offsetS);
    this.source = source;
    this.startedAtCtx = ctx.currentTime;
    this.startedAtOffset = offsetS;
  }

  private stopSource(): void {
    const source = this.source;
    if (!source) return;
    this.source = null;
    try {
      source.onended = null;
      source.stop();
    } catch {
      // Already stopped.
    }
    source.disconnect();
  }
}
