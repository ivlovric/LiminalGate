/** Labels for the band-power chart. Pure functions, no DOM. */

export interface LegendBand {
  name: string;
  low: number;
  high: number;
  color: string;
}

/** Mind Monitor's absolute graph: the five lines on the band-power chart. */
export const TIMELINE_BANDS: LegendBand[] = [
  { name: "delta", low: 1, high: 4, color: "#8b7cf6" },
  { name: "theta", low: 4, high: 8, color: "#7aa2ff" },
  { name: "alpha", low: 8, high: 12, color: "#5dcaa5" },
  { name: "beta", low: 13, high: 30, color: "#e0b15a" },
  { name: "gamma", low: 30, high: 45, color: "#e07a8a" },
];

export interface LegendItem {
  /** Series key on the plot. Headset lines end with " device". */
  series: string;
  label: string;
  color: string;
  dashed: boolean;
}

export function formatHz(value: number): string {
  if (!Number.isFinite(value)) return "";
  const rounded = Math.round(value * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

/** Solid entries always. A dashed headset entry follows a band once that band has arrived. */
export function legendItems(bands: readonly LegendBand[], headset: readonly string[]): LegendItem[] {
  const present = new Set(headset);
  const items: LegendItem[] = [];
  for (const band of bands) {
    const range = `${formatHz(band.low)}–${formatHz(band.high)}`;
    items.push({ series: band.name, label: `${band.name} ${range}`, color: band.color, dashed: false });
    if (present.has(band.name)) {
      items.push({
        series: `${band.name} device`,
        label: `${band.name} ${range} headset`,
        color: band.color,
        dashed: true,
      });
    }
  }
  return items;
}
