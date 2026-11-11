/** Browser biofeedback tone. Playback starts on a click because of autoplay policy. */

interface WebkitWindow extends Window {
  webkitAudioContext?: typeof AudioContext;
}

export class TonePlayer {
  private ctx: AudioContext | null = null;
  private osc: OscillatorNode | null = null;
  private gain: GainNode | null = null;
  private wanted = false;
  private generation = 0;
  volume = 0;
  frequency = 220;

  get running(): boolean {
    return this.wanted && this.ctx?.state === "running";
  }

  async start(): Promise<void> {
    const generation = ++this.generation;
    this.wanted = true;
    if (!this.ctx) {
      const Ctx = window.AudioContext ?? (window as WebkitWindow).webkitAudioContext;
      if (!Ctx) {
        this.wanted = false;
        throw new Error("This browser has no Web Audio API");
      }
      this.ctx = new Ctx();
      this.gain = this.ctx.createGain();
      this.gain.gain.value = 0;
      const filter = this.ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = 1800;
      this.osc = this.ctx.createOscillator();
      this.osc.type = "sine";
      this.osc.frequency.value = 220;
      this.osc.connect(filter);
      filter.connect(this.gain);
      this.gain.connect(this.ctx.destination);
      this.osc.start();
    }
    await this.ctx.resume();
    if (generation !== this.generation || !this.wanted) return;
    this.apply();
  }

  /** Fade the tone out, then suspend the audio context so a second click is silent. */
  async stop(): Promise<void> {
    const generation = ++this.generation;
    this.wanted = false;
    if (!this.ctx || !this.gain) return;
    const now = this.ctx.currentTime;
    this.gain.gain.cancelScheduledValues(now);
    this.gain.gain.setValueAtTime(this.gain.gain.value, now);
    this.gain.gain.setTargetAtTime(0, now, 0.03);
    await new Promise((resolve) => window.setTimeout(resolve, 140));
    if (generation !== this.generation || !this.ctx) return;
    if (this.ctx.state === "running") await this.ctx.suspend();
  }

  update(volume: number, frequency: number): void {
    this.volume = clamp(volume, 0, 1);
    this.frequency = clamp(frequency, 40, 2000);
    this.apply();
  }

  private apply(): void {
    if (!this.wanted || !this.ctx || !this.gain || !this.osc || this.ctx.state !== "running") {
      return;
    }
    const now = this.ctx.currentTime;
    // Cap the speaker level so a full-scale rule is clear without clipping.
    this.gain.gain.setTargetAtTime(this.volume * 0.3, now, 0.08);
    this.osc.frequency.setTargetAtTime(this.frequency, now, 0.08);
  }
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}
