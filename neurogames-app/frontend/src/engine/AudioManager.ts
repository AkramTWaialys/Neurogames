/**
 * AudioManager.ts — Lightweight synthesized sound system for NeuroGames.
 *
 * Uses the Web Audio API to generate tones on the fly — no external
 * audio files needed.  Exposes a simple `play(name)` method and a
 * mute toggle.  Designed to be instantiated once per GameCanvas.
 */

export type SoundName =
  | "correct"
  | "wrong"
  | "levelup"
  | "click"
  | "tick"
  | "pause"
  | "combo";

export class AudioManager {
  private ctx: AudioContext | null = null;
  private _muted = false;

  /** Lazily create the AudioContext (browsers require user gesture). */
  private ensure(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
    }
    if (this.ctx.state === "suspended") {
      this.ctx.resume();
    }
    return this.ctx;
  }

  get muted() {
    return this._muted;
  }

  setMuted(m: boolean) {
    this._muted = m;
  }

  toggleMute(): boolean {
    this._muted = !this._muted;
    return this._muted;
  }

  /** Play a named sound.  Fails silently if muted or context unavailable. */
  play(name: SoundName) {
    if (this._muted) return;
    try {
      const ctx = this.ensure();
      switch (name) {
        case "correct":
          this._playTone(ctx, 880, 0.12, "sine", 0.25);
          setTimeout(() => this._playTone(ctx, 1100, 0.10, "sine", 0.20), 80);
          break;
        case "wrong":
          this._playTone(ctx, 220, 0.18, "square", 0.15);
          break;
        case "levelup":
          this._playTone(ctx, 523, 0.10, "sine", 0.25);
          setTimeout(() => this._playTone(ctx, 659, 0.10, "sine", 0.22), 100);
          setTimeout(() => this._playTone(ctx, 784, 0.10, "sine", 0.20), 200);
          setTimeout(() => this._playTone(ctx, 1047, 0.15, "sine", 0.28), 300);
          break;
        case "click":
          this._playTone(ctx, 600, 0.05, "sine", 0.12);
          break;
        case "tick":
          this._playTone(ctx, 1000, 0.03, "sine", 0.08);
          break;
        case "pause":
          this._playTone(ctx, 440, 0.15, "triangle", 0.15);
          break;
        case "combo":
          this._playTone(ctx, 700, 0.08, "sine", 0.22);
          setTimeout(() => this._playTone(ctx, 900, 0.08, "sine", 0.22), 60);
          setTimeout(() => this._playTone(ctx, 1200, 0.12, "sine", 0.25), 120);
          break;
      }
    } catch {
      /* AudioContext may be blocked by browser policy — fail silently */
    }
  }

  private _playTone(
    ctx: AudioContext,
    freq: number,
    duration: number,
    type: OscillatorType,
    volume: number,
  ) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(volume, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + duration + 0.05);
  }

  dispose() {
    if (this.ctx) {
      this.ctx.close();
      this.ctx = null;
    }
  }
}
