import uPlot from "uplot";

import { glide, shouldDraw } from "./pace";

const COLORS = ["#7aa2ff", "#5dcaa5", "#e0b15a", "#e07a8a", "#c4b5fd", "#8fd18a"];

export interface TimedPoint {
  t: number;
  values: (number | null)[];
}

export interface PlotOptions {
  maxFps?: number;
  glide?: boolean;
  /** How much history to keep. The visible window can be shorter. */
  retainSec?: number;
}

function sizeOf(el: HTMLElement): { width: number; height: number } {
  const rect = el.getBoundingClientRect();
  return {
    width: Math.max(0, Math.floor(rect.width)),
    height: Math.max(0, Math.floor(rect.height)),
  };
}

export class RollingPlot {
  private plot: uPlot | null = null;
  private key = "";
  private mountedKey = "";
  private xs: number[] = [];
  private ys: (number | null)[][] = [];
  private dashes: boolean[] = [];
  private observer: ResizeObserver;
  private maxFps: number;
  private glideEnabled: boolean;
  private retainSec: number;
  private paused = false;
  private pending = 0;
  private lastDraw = 0;
  private viewEnd = 0;
  private anchored = false;
  private targetEnd = 0;
  private labels: string[] = [];
  private lastW = 0;
  private lastH = 0;

  constructor(
    private el: HTMLElement,
    private windowSec: number,
    private colors: string[] = COLORS,
    options: PlotOptions = {},
  ) {
    this.maxFps = options.maxFps ?? 12;
    this.glideEnabled = options.glide ?? true;
    this.retainSec = options.retainSec ?? windowSec + 2;
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(el);
  }

  setWindow(seconds: number): void {
    this.windowSec = seconds;
    this.kick();
  }

  setPaused(paused: boolean): void {
    const was = this.paused;
    this.paused = paused;
    if (paused) this.clearPending();
    if (was && !paused) {
      if (this.labels.length) this.ensure(this.labels);
      requestAnimationFrame(() => {
        this.resize();
        this.kick();
      });
    }
  }

  reset(): void {
    this.xs = [];
    this.ys = this.ys.map(() => []);
    this.anchored = false;
    this.viewEnd = 0;
    this.targetEnd = 0;
    this.kick();
  }

  replace(channels: string[], points: TimedPoint[], dashes: boolean[] = []): void {
    this.key = channels.join("|");
    this.dashes = dashes;
    this.xs = [];
    this.ys = channels.map(() => []);
    this.anchored = false;
    for (const point of points) this.add(channels, point);
    this.ensure(channels);
    this.kick();
  }

  pushTimed(channels: string[], points: TimedPoint[], dashes: boolean[] = []): void {
    if (!channels.length || !points.length) return;
    const signature = channels.join("|");
    if (signature !== this.key) {
      this.replace(channels, points, dashes);
      return;
    }
    if (dashes.length) this.dashes = dashes;
    for (const point of points) this.add(channels, point);
    this.kick();
  }

  setSeries(x: number[], y: number[]): void {
    this.xs = x;
    this.ys = [y];
    this.key = "spectrum";
    this.dashes = [];
    this.glideEnabled = false;
    this.ensure(["mean"]);
    this.kick();
  }

  private add(channels: string[], point: TimedPoint): void {
    this.xs.push(point.t);
    if (point.t > this.targetEnd) this.targetEnd = point.t;
    channels.forEach((_, index) => {
      const value = point.values[index];
      this.ys[index].push(typeof value === "number" && Number.isFinite(value) ? value : null);
    });
  }

  private ensure(channels: string[]): void {
    this.labels = channels;
    if (this.paused) return;
    if (!this.plot || this.mountedKey !== this.key) {
      this.mount(channels);
      this.mountedKey = this.key;
    }
  }

  private clearPending(): void {
    if (!this.pending) return;
    cancelAnimationFrame(this.pending);
    window.clearTimeout(this.pending);
    this.pending = 0;
  }

  private kick(): void {
    if (this.pending || this.paused) return;
    this.pending = requestAnimationFrame((now) => this.tick(now));
  }

  private tick(now: number): void {
    this.pending = 0;
    if (this.paused) return;
    if (!shouldDraw(now, this.lastDraw, this.maxFps)) {
      const wait = Math.max(20, 1000 / this.maxFps - (now - this.lastDraw));
      this.pending = window.setTimeout(() => {
        this.pending = 0;
        this.kick();
      }, wait);
      return;
    }
    const dt = this.lastDraw ? (now - this.lastDraw) / 1000 : 0;
    this.lastDraw = now;
    let moving = false;
    if (this.glideEnabled && this.targetEnd > 0) {
      if (!this.anchored) {
        this.viewEnd = this.targetEnd;
        this.anchored = true;
      } else {
        const next = glide(this.viewEnd, this.targetEnd, dt || 1 / this.maxFps);
        moving = next !== this.targetEnd;
        this.viewEnd = next;
      }
    }
    this.trim();
    this.draw();
    if (moving) this.kick();
  }

  private trim(): void {
    const last = this.targetEnd || this.xs[this.xs.length - 1];
    if (last == null) return;
    const min = last - this.retainSec;
    let head = 0;
    while (head < this.xs.length - 2 && (this.xs[head] ?? 0) < min) head += 1;
    if (head < 64) return;
    this.xs = this.xs.slice(head);
    this.ys = this.ys.map((series) => series.slice(head));
  }

  private draw(): void {
    if (!this.plot || this.paused) return;
    this.plot.setData([this.xs, ...this.ys] as uPlot.AlignedData);
  }

  private mount(channels: string[]): void {
    this.plot?.destroy();
    const { width, height } = sizeOf(this.el);
    const series: uPlot.Series[] = [{}];
    channels.forEach((label, index) => {
      series.push({
        label,
        stroke: this.colors[index % this.colors.length],
        width: this.dashes[index] ? 1.4 : 2,
        dash: this.dashes[index] ? [6, 4] : undefined,
        spanGaps: false,
        points: { show: false },
      });
    });
    this.plot = new uPlot(
      {
        width: Math.max(120, width),
        height: Math.max(80, height),
        pxAlign: false,
        series,
        scales: {
          x: {
            time: false,
            range: (_plot, min, max) => {
              if (!this.glideEnabled || !this.anchored) return [min, max];
              return [this.viewEnd - this.windowSec, this.viewEnd];
            },
          },
        },
        axes: [
          {
            stroke: "#93a4ba",
            grid: { stroke: "#243044" },
            values: (_plot, splits) => splits.map((value) => clock(value)),
          },
          {
            stroke: "#93a4ba",
            grid: { stroke: "#243044" },
            size: 56,
          },
        ],
        legend: { show: true },
        cursor: { show: true, drag: { x: false, y: false } },
      },
      [this.xs, ...this.ys] as uPlot.AlignedData,
      this.el,
    );
  }

  private resize(): void {
    if (!this.plot || this.paused) return;
    const width = this.el.clientWidth;
    const height = this.el.clientHeight;
    if (width < 40 || height < 40) return;
    if (width === this.lastW && height === this.lastH) return;
    this.lastW = width;
    this.lastH = height;
    this.plot.setSize({ width, height });
  }
}

function clock(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  if (seconds < 1_000_000_000) return seconds.toFixed(0);
  const date = new Date(seconds * 1000);
  return date.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
