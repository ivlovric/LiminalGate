import uPlot from "uplot";

const COLORS = ["#7aa2ff", "#5dcaa5", "#e0b15a", "#e07a8a", "#c4b5fd", "#8fd18a"];

export interface TimedPoint {
  t: number;
  values: (number | null)[];
}

function sizeOf(el: HTMLElement): { width: number; height: number } {
  const rect = el.getBoundingClientRect();
  return {
    width: Math.max(120, Math.floor(rect.width)),
    height: Math.max(80, Math.floor(rect.height)),
  };
}

export class RollingPlot {
  private plot: uPlot | null = null;
  private key = "";
  private xs: number[] = [];
  private ys: (number | null)[][] = [];
  private dashes: boolean[] = [];
  private observer: ResizeObserver;

  constructor(
    private el: HTMLElement,
    private windowSec: number,
    private colors: string[] = COLORS,
  ) {
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(el);
  }

  setWindow(seconds: number): void {
    this.windowSec = seconds;
    this.trim();
    this.draw();
  }

  reset(): void {
    this.xs = [];
    this.ys = this.ys.map(() => []);
    this.draw();
  }

  pushTimed(channels: string[], points: TimedPoint[], dashes: boolean[] = []): void {
    if (!channels.length) {
      return;
    }
    const signature = channels.join("|");
    if (signature !== this.key) {
      this.key = signature;
      this.dashes = dashes;
      this.xs = [];
      this.ys = channels.map(() => []);
      this.mount(channels);
    }
    for (const point of points) {
      this.xs.push(point.t);
      channels.forEach((_, index) => {
        const value = point.values[index];
        this.ys[index].push(typeof value === "number" && Number.isFinite(value) ? value : null);
      });
    }
    this.trim();
    this.draw();
  }

  setSeries(x: number[], y: number[]): void {
    this.xs = x;
    this.ys = [y];
    if (!this.plot || this.key !== "spectrum") {
      this.key = "spectrum";
      this.dashes = [];
      this.mount(["mean"]);
      return;
    }
    this.plot.setData([x, y]);
  }

  private trim(): void {
    if (!this.xs.length) {
      return;
    }
    const last = this.xs[this.xs.length - 1] ?? 0;
    const min = last - this.windowSec;
    while (this.xs.length > 2 && this.xs[0] < min) {
      this.xs.shift();
      this.ys.forEach((series) => series.shift());
    }
  }

  private draw(): void {
    this.plot?.setData([this.xs, ...this.ys] as uPlot.AlignedData);
  }

  private mount(channels: string[]): void {
    this.plot?.destroy();
    const { width, height } = sizeOf(this.el);
    const series: uPlot.Series[] = [{}];
    channels.forEach((label, index) => {
      series.push({
        label,
        stroke: this.colors[index % this.colors.length],
        width: this.dashes[index] ? 1.2 : 1.6,
        dash: this.dashes[index] ? [6, 4] : undefined,
        spanGaps: false,
        points: { show: false },
      });
    });
    this.plot = new uPlot(
      {
        width,
        height,
        series,
        scales: { x: { time: false } },
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
    if (!this.plot) {
      return;
    }
    const { width, height } = sizeOf(this.el);
    this.plot.setSize({ width, height });
  }
}

function clock(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) {
    return "";
  }
  if (seconds < 1_000_000_000) {
    return seconds.toFixed(1);
  }
  const date = new Date(seconds * 1000);
  return date.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
