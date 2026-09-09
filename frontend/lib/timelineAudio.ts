// The timeline's sound: the rendered scene mix, played through one <audio>
// element that FOLLOWS the transport clock.
//
// This replaced a procedural placeholder synth — a low oscillator bed and a
// blip on every SFX marker — that had been left in as a "seam" to close later.
// It was never closed: a scene could be rendered to a 4-minute, 35-clip WAV on
// the server and the workspace would still play a drone, muted by default, so
// the one thing a listener came for was the one thing they never heard.
//
// The method names are kept from the placeholder (`resume`, `setMuted`,
// `playBed`, `stopBed`, `blip`, `dispose`) so `useTransportClock` and the
// transport bar did not have to change. `blip` is a no-op: the SFX are baked
// into the mix, and layering a synthesized click over a real footstep would be
// the placeholder leaking back in.
//
// THE CLOCK STAYS IN THE STORE. The element is seeked to `currentMs` when it
// drifts, and never writes time back — the same rule the program monitor
// follows — so scrubbing, the playhead and the video all read one clock. The
// cost is that the audio may be re-seeked a few times a second while playing if
// the browser's clock and rAF disagree; the tolerance below keeps that
// inaudible.

/** How far the element may drift from the transport before it is re-seeked.
 * Wide enough that normal rAF jitter never triggers it; tight enough that a
 * seek is heard as a seek and not as lag. */
const SEEK_TOLERANCE_S = 0.18;

export class TimelineAudioEngine {
  private el: HTMLAudioElement | null = null;
  private muted = false;
  private src: string | null = null;
  private srcIsObjectUrl = false;
  /** Whether the transport wants sound right now (playBed/stopBed). */
  private wantPlaying = false;

  /** Lazily create the element. Returns null with no DOM (SSR). */
  private ensure(): HTMLAudioElement | null {
    if (this.el) return this.el;
    if (typeof window === "undefined") return null;
    const el = new Audio();
    el.preload = "auto";
    el.muted = this.muted;
    this.el = el;
    return el;
  }

  /** Point the element at a rendered mix (an object URL from the API, or null
   * when the scene has no render). Replacing the source releases the previous
   * object URL, so a scene switch does not leak the last scene's WAV. */
  load(src: string | null, srcIsObjectUrl = true): void {
    if (this.src === src) return;
    if (this.srcIsObjectUrl && this.src) {
      try {
        URL.revokeObjectURL(this.src);
      } catch {
        // Already released, or never a blob URL.
      }
    }
    this.src = src;
    this.srcIsObjectUrl = srcIsObjectUrl && src !== null;
    const el = this.ensure();
    if (!el) return;
    el.pause();
    if (src) {
      el.src = src;
      el.load();
    } else {
      el.removeAttribute("src");
      el.load();
    }
  }

  /** True when there is a real mix to play. The transport bar uses this to
   * label the mute button honestly ("no render" rather than "muted"). */
  hasSource(): boolean {
    return this.src !== null;
  }

  /** Must be called from a user gesture (Play / unmute). Browsers gate audio
   * playback behind a gesture; the element's own play() is what unlocks it. */
  async resume(): Promise<void> {
    const el = this.ensure();
    if (!el || !this.src) return;
    if (!this.wantPlaying) return;
    try {
      await el.play();
    } catch {
      // Autoplay policy refused: the next gesture will succeed.
    }
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.el) this.el.muted = muted;
  }

  /** Transport started. */
  playBed(): void {
    this.wantPlaying = true;
    const el = this.ensure();
    if (!el || !this.src) return;
    if (el.paused) el.play().catch(() => {});
  }

  /** Transport paused or stopped. */
  stopBed(): void {
    this.wantPlaying = false;
    if (this.el && !this.el.paused) this.el.pause();
  }

  /** Keep the element on the transport clock. Called on every clock tick and
   * on every seek; cheap when nothing has drifted. */
  syncTime(currentMs: number): void {
    const el = this.el;
    if (!el || !this.src) return;
    const target = Math.max(0, currentMs / 1000);
    if (Math.abs(el.currentTime - target) > SEEK_TOLERANCE_S) {
      try {
        el.currentTime = target;
      } catch {
        // Metadata not loaded yet; the next tick retries.
      }
    }
  }

  /** SFX markers are already in the mix. Kept so the clock's call site is
   * unchanged. */
  blip(): void {}

  dispose(): void {
    this.stopBed();
    this.load(null);
    this.el = null;
  }
}
