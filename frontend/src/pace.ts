/** View layout and the smoothing used by the calm dashboard. Pure functions, no DOM. */

export const CARDS = ["timeline", "eeg", "spectrum", "bands", "heart", "ratios", "markers", "session"] as const;
export type CardId = (typeof CARDS)[number];

export const MINUTE_CHOICES = [1, 5, 15, 30] as const;
export const TRACE_WINDOWS = [5, 10, 30] as const;
export const SMOOTH_CHOICES = [0.5, 1, 2] as const;

export interface ViewState {
  hidden: CardId[];
  collapsed: CardId[];
  calm: boolean;
  minutes: number;
  traceWindow: number;
  traceMode: "filtered" | "raw";
  /** Exponential smoothing time constant for the band chart, in seconds. */
  smoothSec: number;
}

/** First visit: one large band chart, the bars, and markers. EEG and spectrum are folded. */
export const DEFAULT_VIEW: ViewState = {
  hidden: ["ratios", "session"],
  collapsed: ["eeg", "spectrum", "heart"],
  calm: false,
  minutes: 5,
  traceWindow: 10,
  traceMode: "filtered",
  smoothSec: 1,
};

const CARD_SET = new Set<string>(CARDS);

export function ema(previous: number | undefined, value: number, dt: number, tau: number): number {
  if (previous == null || !Number.isFinite(previous) || !(dt > 0) || !(tau > 0)) return value;
  const alpha = 1 - Math.exp(-dt / tau);
  return previous + alpha * (value - previous);
}

/** Ease a chart's right edge toward the newest sample so packets don't jump. */
export function glide(view: number, target: number, dt: number, tau = 0.55): number {
  if (!Number.isFinite(view) || !Number.isFinite(target)) return target;
  if (!(dt > 0)) return view;
  const next = view + (1 - Math.exp(-dt / tau)) * (target - view);
  return Math.abs(target - next) < 0.03 ? target : next;
}

export function shouldDraw(now: number, last: number, maxFps: number): boolean {
  if (!(maxFps > 0)) return true;
  return now - last >= 1000 / maxFps - 0.5;
}

export function smoothSeries(previous: number[] | null, next: number[], dt: number, tau: number): number[] {
  if (!previous || previous.length !== next.length) return next.slice();
  return next.map((value, index) => ema(previous[index], value, dt, tau));
}

export function visibleCards(view: ViewState): CardId[] {
  if (view.calm) return ["timeline"];
  return CARDS.filter((id) => !view.hidden.includes(id));
}

export function loadView(raw: string | null): ViewState {
  if (!raw) return { ...DEFAULT_VIEW, hidden: [...DEFAULT_VIEW.hidden], collapsed: [...DEFAULT_VIEW.collapsed] };
  try {
    const parsed = JSON.parse(raw) as Partial<ViewState>;
    const hidden = Array.isArray(parsed.hidden) ? parsed.hidden.filter((id): id is CardId => CARD_SET.has(id)) : [...DEFAULT_VIEW.hidden];
    const collapsed = Array.isArray(parsed.collapsed)
      ? parsed.collapsed.filter((id): id is CardId => CARD_SET.has(id) && !hidden.includes(id))
      : [...DEFAULT_VIEW.collapsed];
    const minutes = MINUTE_CHOICES.find((choice) => choice === parsed.minutes) ?? DEFAULT_VIEW.minutes;
    const traceWindow = TRACE_WINDOWS.find((choice) => choice === parsed.traceWindow) ?? DEFAULT_VIEW.traceWindow;
    const traceMode = parsed.traceMode === "raw" ? "raw" : "filtered";
    const smoothSec = SMOOTH_CHOICES.find((choice) => choice === parsed.smoothSec) ?? DEFAULT_VIEW.smoothSec;
    return {
      hidden,
      collapsed,
      calm: Boolean(parsed.calm),
      minutes,
      traceWindow,
      traceMode,
      smoothSec,
    };
  } catch {
    return { ...DEFAULT_VIEW, hidden: [...DEFAULT_VIEW.hidden], collapsed: [...DEFAULT_VIEW.collapsed] };
  }
}
