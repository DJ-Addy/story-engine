// SEAM — mock placeholder audio. Real audio: swap this engine for a single
// <audio> element sourced from GET /api/v1/projects/{id}/render/audio, drive
// audio.currentTime from the transport's currentMs, and delete this synth.
//
// A tiny, self-contained Web Audio placeholder so the timeline is audible with
// NO bundled media: a subtle low ambient bed plus soft blips fired when the
// transport crosses an SFX marker. SSR-safe — the AudioContext is created
// lazily inside a user gesture, never at module load. Muted by default.

const MASTER_LEVEL = 0.7; // master gain target when unmuted (0 when muted)
const BED_LEVEL = 0.06; // low bed level → ~0.04 at the output; deliberately subtle
const BLIP_LEVEL = 0.14; // per-blip envelope peak

type AudioContextCtor = typeof AudioContext;

export class TimelineAudioEngine {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private muted = true;
  private bedGain: GainNode | null = null;
  private bedNodes: OscillatorNode[] = [];
  private bedPlaying = false;

  /** Lazily construct the AudioContext + master gain. Returns null with no DOM. */
  private ensureContext(): AudioContext | null {
    if (this.ctx) return this.ctx;
    if (typeof window === "undefined") return null;
    const Ctor: AudioContextCtor | undefined =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: AudioContextCtor })
        .webkitAudioContext;
    if (!Ctor) return null;
    const ctx = new Ctor();
    const master = ctx.createGain();
    master.gain.value = this.muted ? 0 : MASTER_LEVEL;
    master.connect(ctx.destination);
    this.ctx = ctx;
    this.master = master;
    return ctx;
  }

  /** Must be called from a user gesture (play / unmute) to unlock audio. */
  async resume(): Promise<void> {
    const ctx = this.ensureContext();
    if (!ctx) return;
    if (ctx.state === "suspended") await ctx.resume();
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (!this.ctx || !this.master) return;
    // Soft ramp to avoid clicks.
    this.master.gain.setTargetAtTime(
      muted ? 0 : MASTER_LEVEL,
      this.ctx.currentTime,
      0.05,
    );
    if (muted) this.stopBed();
  }

  /** Start the ambient bed: two low detuned oscillators through a lowpass, with
   * a slow LFO on the level for gentle movement. No-op if muted / already on. */
  playBed(): void {
    if (this.muted || this.bedPlaying) return;
    const ctx = this.ensureContext();
    if (!ctx || !this.master) return;

    const bedGain = ctx.createGain();
    bedGain.gain.value = BED_LEVEL;
    const lowpass = ctx.createBiquadFilter();
    lowpass.type = "lowpass";
    lowpass.frequency.value = 400;
    lowpass.connect(bedGain);
    bedGain.connect(this.master);

    const osc1 = ctx.createOscillator();
    osc1.type = "sine";
    osc1.frequency.value = 55;
    const osc2 = ctx.createOscillator();
    osc2.type = "triangle";
    osc2.frequency.value = 110;
    osc2.detune.value = 6;
    osc1.connect(lowpass);
    osc2.connect(lowpass);

    // Very slow LFO modulating the bed level so it breathes rather than drones.
    const lfo = ctx.createOscillator();
    lfo.type = "sine";
    lfo.frequency.value = 0.07;
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = BED_LEVEL * 0.5;
    lfo.connect(lfoGain);
    lfoGain.connect(bedGain.gain);

    const now = ctx.currentTime;
    osc1.start(now);
    osc2.start(now);
    lfo.start(now);

    this.bedGain = bedGain;
    this.bedNodes = [osc1, osc2, lfo];
    this.bedPlaying = true;
  }

  stopBed(): void {
    if (!this.bedPlaying) return;
    const t = this.ctx ? this.ctx.currentTime : 0;
    for (const osc of this.bedNodes) {
      try {
        osc.stop(t);
      } catch {
        // already stopped — safe to ignore
      }
      osc.disconnect();
    }
    this.bedNodes = [];
    if (this.bedGain) {
      this.bedGain.disconnect();
      this.bedGain = null;
    }
    this.bedPlaying = false;
  }

  /** A soft, short blip for an SFX marker crossing. No-op if muted / no DOM. */
  blip(): void {
    if (this.muted) return;
    const ctx = this.ensureContext();
    if (!ctx || !this.master) return;
    const now = ctx.currentTime;

    const osc = ctx.createOscillator();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(760, now);
    osc.frequency.exponentialRampToValueAtTime(680, now + 0.12);

    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, now);
    env.gain.exponentialRampToValueAtTime(BLIP_LEVEL, now + 0.008);
    env.gain.exponentialRampToValueAtTime(0.0001, now + 0.13);

    osc.connect(env);
    env.connect(this.master);
    osc.start(now);
    osc.stop(now + 0.16);
    osc.onended = () => {
      osc.disconnect();
      env.disconnect();
    };
  }

  dispose(): void {
    this.stopBed();
    if (this.ctx) void this.ctx.close();
    this.ctx = null;
    this.master = null;
  }
}
