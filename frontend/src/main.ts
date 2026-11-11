import "uplot/dist/uPlot.min.css";

import { TonePlayer } from "./audio";
import { CARDS, ema, loadView, smoothSeries, type CardId, type ViewState } from "./pace";
import { TIMELINE_BANDS, legendItems, type LegendBand } from "./legend";
import { formatSessionWhen } from "./time";
import { BOARDS, INPUTS, validateSetup } from "./setup";
import { RollingPlot, type TimedPoint } from "./plots";
import "./style.css";

interface Band {
  name: string;
  f_low: number;
  f_high: number;
  color: string;
}
interface Ratio {
  name: string;
  expression: string;
}
interface MarkerDef {
  name: string;
  color: string;
  hotkey: string;
  osc: number | null;
}
interface FeedbackRule {
  metric: string;
  source: string;
  scope: string;
  param: string;
  in_min: number;
  in_max: number;
  out_min: number;
  out_max: number;
  smooth: number;
  gate_metric: string | null;
  gate_source: string;
  gate_scope: string;
  gate_threshold: number | null;
  gate_mode: string;
}
interface Profile {
  name: string;
  description: string;
  bands: Band[];
  ratios: Ratio[];
  markers: MarkerDef[];
  feedback: FeedbackRule[];
  relative_range: [number, number];
  window_sec: number;
  hop_sec: number;
}
interface Recording {
  session_id: string;
  subject: string;
  task: string;
  elapsed: number;
  eeg_samples: number;
  path: string;
}
interface Status {
  input: {
    type: string;
    running: boolean;
    error: string | null;
    samples_seen: number;
    eeg_fs: number;
    eeg_scale: number;
  };
  recording: Recording | null;
  profile: string;
  server_audio: boolean;
  hr: number | null;
  heart?: Heart | null;
  osc_paths?: { path: string; count: number }[];
  playback?: PlaybackInfo | null;
}
interface PlaybackInfo {
  file: string;
  position: number;
  duration: number;
  loop: number;
  paused: boolean;
  rate: number;
  warning: string;
}
interface SessionRow {
  subject_id: string;
  session_id: string;
  task: string;
  local_start: string;
  duration_sec: number | null;
  eeg_samples: number;
  streams: string[];
  inventory: { name: string; bytes: number }[];
}
interface Heart {
  state: "no_ppg" | "computing" | "ready";
  bpm: number | null;
  rmssd: number | null;
  quality: string | null;
  channel: string | null;
  source: string | null;
  temperature?: number | null;
}
interface BandPower {
  relative_mean: number;
  absolute_mean: number;
  color: string;
  f_low: number;
  f_high: number;
}
interface Config {
  input_type: string;
  osc_host: string;
  osc_port: number;
  playback_path: string;
  playback_speed: number;
  playback_loop: boolean;
  eeg_scale: number;
  eeg_fs: number;
  brainflow_board: string;
  brainflow_mac: string;
  brainflow_serial: string;
  brainflow_other_info: string;
  lsl_name: string;
  server_audio: boolean;
  powerline_hz: number;
  display_hz: number;
  profile: string;
}
interface LiveEvent {
  t: number;
  kind: string;
  name: string;
  color: string;
}

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const conn = $("conn");
const inputLabel = $("input-label");
const horseshoe = $("horseshoe");
const hrEl = $("hr");
const clock = $("clock");
const clockLabel = $("clock-label");
const toneBtn = $("tone-btn");
const recordBtn = $("record-btn");
const profileBtn = $("profile-btn");
const setupBtn = $("setup-btn");
const subject = $<HTMLInputElement>("subject");
const task = $<HTMLInputElement>("task");
const notes = $<HTMLInputElement>("notes");
const savePath = $("save-path");
const eegMeta = $("eeg-meta");
const bandBars = $("band-bars");
const ratiosEl = $("ratios");
const profileName = $("profile-name");
const toneReadout = $("tone-readout");
const toneMeter = $("tone-meter");
const markerTrack = $("marker-track");
const markerButtons = $("marker-buttons");
const drawer = $("drawer");
const drawerTitle = $("drawer-title");
const drawerBody = $("drawer-body");
const toast = $("toast");

const TIMELINE = TIMELINE_BANDS.map((band) => band.name);
let bandMeta: LegendBand[] = TIMELINE_BANDS.map((band) => ({ ...band }));
const LANE = { filtered: 140, raw: 2000 };

const VIEW_KEY = "liminalgate.view";
const tone = new TonePlayer();
const eegPlot = new RollingPlot($("eeg-plot"), 10, undefined, { maxFps: 12, glide: true, retainSec: 35 });
const specPlot = new RollingPlot($("spec-plot"), 60, undefined, {
  maxFps: 2,
  glide: false,
  xLabel: "Hz",
  yLabel: "log10 power",
});
const timelinePlot = new RollingPlot($("timeline-plot"), 300, bandMeta.map((band) => band.color), {
  maxFps: 2,
  glide: true,
  retainSec: 30 * 60,
  legend: false,
});
const hrPlot = new RollingPlot($("hr-plot"), 300, ["#e07a8a"], { maxFps: 2, glide: true, retainSec: 30 * 60 });

let token = localStorage.getItem("liminalgate.token") ?? "";
let profile: Profile | null = null;
let config: Config | null = null;
let socket: WebSocket | null = null;
let reconnectTimer = 0;
let bandKey = "";
let ratioKey = "";
let events: LiveEvent[] = [];
let recordAnchor: { elapsed: number; at: number } | null = null;
let lastVolume = 0;
let lastHz = 220;
let drawerMode: "profile" | "setup" | null = null;
let view: ViewState = loadView(localStorage.getItem(VIEW_KEY));
let traceMode: "filtered" | "raw" = view.traceMode;
let traceWindow = view.traceWindow;
let timelineMinutes = view.minutes;
let excluded = new Set<string>();
let lastQuality: number[] | null = null;
let eegChannels: string[] = ["TP9", "AF7", "AF8", "TP10"];
let traceKey = "";
let lastTraceT = Number.NEGATIVE_INFINITY;
let timelineKey = "";
let lastTimelineT = 0;
let emaStamp = 0;
let specStamp = 0;
let specSmooth: number[] | null = null;
let recordingId: string | null = null;
let slowTimer = 0;
let pendingBands: Record<string, BandPower> | null = null;
let pendingRatios: Record<string, { expression: string; mean: number | null }> | null = null;
const traceHistory: { t: number; raw: number[]; filtered: number[]; gap: boolean }[] = [];
const timelineHistory: { t: number; values: Record<string, number | null>; device: Record<string, number | null> }[] = [];
const timelineEma: Record<string, number> = {};
const timelineDevice: Record<string, number | null> = {};
const AUTO_KEY = "liminalgate.autoEvents";
let showAuto = localStorage.getItem(AUTO_KEY) === "1";
let lastHrT = 0;

function esc(value: string): string {
  return value.replace(/[&<>"']/g, (ch) =>
    ch === "&" ? "&amp;" : ch === "<" ? "&lt;" : ch === ">" ? "&gt;" : ch === '"' ? "&quot;" : "&#39;",
  );
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = await response.text();
    try {
      const parsed = JSON.parse(detail) as { detail?: string };
      if (parsed.detail) detail = parsed.detail;
    } catch {
      /* keep the raw body */
    }
    throw new Error(detail || response.statusText);
  }
  return (await response.json()) as T;
}

function showToast(message: string): void {
  toast.hidden = false;
  toast.textContent = message;
  window.setTimeout(() => {
    toast.hidden = true;
  }, 4500);
}

function setConnection(label: string, kind: "live" | "bad" | "wait"): void {
  conn.textContent = label;
  conn.title = label;
  conn.className = kind === "wait" ? "pill" : `pill ${kind}`;
  conn.dataset.state = kind;
}

function formatSec(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "0.0s";
  return `${value.toFixed(1)}s`;
}

let playbackWarning = "";
let seeking = false;

function playbackText(play: PlaybackInfo): string {
  const state = play.paused ? "paused" : "playback";
  const loop = play.loop > 0 ? ` · loop ${play.loop}` : "";
  return `${state} ${play.file} ${formatSec(play.position)}/${formatSec(play.duration)}${loop}`;
}

function applyPlayback(play: PlaybackInfo | null | undefined): void {
  const bar = document.getElementById("transport");
  const pause = document.getElementById("play-pause");
  const seek = document.getElementById("play-seek");
  if (!(bar instanceof HTMLElement) || !(pause instanceof HTMLButtonElement) || !(seek instanceof HTMLInputElement)) return;
  if (!play?.file) {
    bar.hidden = true;
    playbackWarning = "";
    return;
  }
  bar.hidden = false;
  pause.textContent = play.paused ? "Resume" : "Pause";
  const duration = Math.max(play.duration, 0.1);
  seek.max = String(duration);
  if (!seeking) seek.value = String(Math.min(duration, Math.max(0, play.position)));
  if (play.warning && play.warning !== playbackWarning) {
    playbackWarning = play.warning;
    showToast(play.warning);
  }
}

function applyRecording(rec: Recording | null): void {
  if (!rec) {
    if (recordBtn.dataset.recording === "yes") void loadRecordings();
    if (!recordingId && recordBtn.dataset.recording !== "yes") return;
    recordingId = null;
    recordAnchor = null;
    recordBtn.textContent = "Record";
    recordBtn.classList.remove("recording");
    recordBtn.dataset.recording = "no";
    clock.textContent = "idle";
    clockLabel.textContent = "session";
    return;
  }
  savePath.textContent = rec.path;
  clockLabel.textContent = "recording";
  recordBtn.classList.add("recording");
  recordBtn.dataset.recording = "yes";
  if (recordingId === rec.session_id && recordAnchor) return;
  recordingId = rec.session_id;
  recordAnchor = { elapsed: rec.elapsed, at: performance.now() };
}

function setFeedback(volume: number, frequency: number): void {
  lastVolume = volume;
  lastHz = frequency;
  const on = tone.running;
  toneReadout.dataset.toneState = on ? "on" : "off";
  toneReadout.dataset.volume = volume.toFixed(3);
  toneReadout.dataset.hz = Math.round(frequency).toString();
  const level = `${Math.round(frequency)} Hz · ${Math.round(volume * 100)}%`;
  toneReadout.textContent = on ? `Tone on · ${level}` : `Tone off · ${level}`;
  toneMeter.style.width = `${Math.min(100, Math.round(volume * 100))}%`;
  if (on) tone.update(volume, frequency);
}

function qualityClass(value: number): string {
  if (value <= 1) return "good";
  if (value <= 2) return "medium";
  if (value < 4) return "poor";
  return "bad";
}

function renderHorseshoe(channels: string[], quality: number[] | null): void {
  const names = channels.filter((name) => !name.toUpperCase().startsWith("AUX"));
  const count = Math.max(names.length, quality?.length ?? 0);
  if (!count) {
    horseshoe.innerHTML = "";
    return;
  }
  const html = Array.from({ length: count }, (_, index) => {
    const name = names[index] ?? channels[index] ?? `${index + 1}`;
    const value = quality?.[index];
    const bad = excluded.has(name) || (value != null && value >= 4);
    const klass = bad ? "bad" : value == null ? "" : qualityClass(value);
    const title = excluded.has(name) ? "excluded from averages" : "";
    return `<span title="${esc(title)}"><i class="${klass}"></i>${esc(name)}</span>`;
  }).join("");
  if (html === horseshoe.dataset.markup) return;
  horseshoe.dataset.markup = html;
  horseshoe.innerHTML = html;
}

function tracePoint(point: (typeof traceHistory)[number], previous: number | null): TimedPoint[] {
  const lane = LANE[traceMode];
  const rows: TimedPoint[] = [];
  if (point.gap && previous != null) {
    rows.push({ t: previous + (point.t - previous) * 0.5, values: eegChannels.map(() => null) });
  }
  const source = traceMode === "raw" ? point.raw : point.filtered;
  rows.push({
    t: point.t,
    values: eegChannels.map((_, index) => {
      const value = source[index];
      return typeof value === "number" ? value + index * lane : null;
    }),
  });
  return rows;
}

function syncTraces(full: boolean): void {
  const title = document.getElementById("eeg-title");
  if (title) title.textContent = traceMode === "filtered" ? "EEG" : "EEG raw";
  const key = `${eegChannels.join("|")}:${traceMode}`;
  if (!traceHistory.length) return;
  if (full || key !== traceKey) {
    traceKey = key;
    const rows: TimedPoint[] = [];
    let previous: number | null = null;
    for (const point of traceHistory) {
      rows.push(...tracePoint(point, previous));
      previous = point.t;
    }
    lastTraceT = traceHistory[traceHistory.length - 1].t;
    eegPlot.replace(eegChannels, rows);
    return;
  }
  const fresh: TimedPoint[] = [];
  let previous: number | null = null;
  for (const point of traceHistory) {
    if (point.t > lastTraceT) fresh.push(...tracePoint(point, previous));
    previous = point.t;
  }
  if (!fresh.length) return;
  lastTraceT = traceHistory[traceHistory.length - 1].t;
  eegPlot.pushTimed(eegChannels, fresh);
}

function timelineSeries(): {
  names: string[];
  dashes: boolean[];
  colors: string[];
  legend: ReturnType<typeof legendItems>;
} {
  const headset = view.showDevice ? TIMELINE.filter((name) => timelineDevice[name] != null) : [];
  const legend = legendItems(bandMeta, headset);
  const ordered = [...legend.filter((item) => !item.dashed), ...legend.filter((item) => item.dashed)];
  return {
    names: ordered.map((item) => item.series),
    dashes: ordered.map((item) => item.dashed),
    colors: ordered.map((item) => item.color),
    legend,
  };
}

let legendKey = "";

function renderTimelineLegend(items: ReturnType<typeof legendItems>): void {
  const host = document.getElementById("timeline-legend");
  if (!host) return;
  const key = items.map((item) => `${item.series}:${item.label}:${item.color}:${item.dashed}`).join("|");
  if (key === legendKey) return;
  legendKey = key;
  host.innerHTML = items
    .map((item) => {
      const pressed = timelinePlot.shown(item.series);
      const kind = item.dashed ? "dashed" : "solid";
      return `<button type="button" data-series="${esc(item.series)}" aria-pressed="${pressed ? "true" : "false"}" style="--swatch:${esc(item.color)}"><i class="swatch ${kind}" aria-hidden="true"></i><span>${esc(item.label)}</span></button>`;
    })
    .join("");
}

function timelineValues(row: (typeof timelineHistory)[number], names: string[]): (number | null)[] {
  return names.map((name) => {
    if (name.endsWith(" device")) return row.device[name.replace(" device", "")] ?? null;
    return row.values[name] ?? null;
  });
}

function syncTimeline(full: boolean): void {
  const { names, dashes, colors, legend } = timelineSeries();
  renderTimelineLegend(legend);
  if (!timelineHistory.length) return;
  const key = names.join("|");
  if (full || key !== timelineKey) {
    timelineKey = key;
    const min = timelineHistory[timelineHistory.length - 1].t - 30 * 60;
    timelinePlot.replace(
      names,
      timelineHistory.filter((row) => row.t >= min).map((row) => ({ t: row.t, values: timelineValues(row, names) })),
      dashes,
      colors,
    );
    return;
  }
  const row = timelineHistory[timelineHistory.length - 1];
  timelinePlot.pushTimed(names, [{ t: row.t, values: timelineValues(row, names) }], dashes, colors);
}

function cardShown(id: CardId): boolean {
  const panel = document.querySelector(`[data-card="${id}"]`);
  return Boolean(panel && !panel.hasAttribute("hidden") && !panel.classList.contains("collapsed"));
}

function saveView(): void {
  view.traceMode = traceMode;
  view.traceWindow = traceWindow;
  view.minutes = timelineMinutes;
  localStorage.setItem(VIEW_KEY, JSON.stringify(view));
}

function applyView(): void {
  document.body.classList.toggle("calm", view.calm);
  const calmBtn = document.getElementById("calm-btn");
  if (calmBtn) {
    calmBtn.textContent = view.calm ? "Exit calm" : "Calm";
    calmBtn.classList.toggle("on", view.calm);
  }
  for (const id of CARDS) {
    const panel = document.querySelector(`[data-card="${id}"]`);
    if (!(panel instanceof HTMLElement)) continue;
    const hidden = view.calm ? id !== "timeline" : view.hidden.includes(id);
    const collapsed = view.calm ? false : view.collapsed.includes(id);
    panel.hidden = hidden;
    panel.classList.toggle("collapsed", collapsed);
    panel.querySelector("[data-collapse]")?.setAttribute("aria-expanded", collapsed ? "false" : "true");
    const box = document.querySelector(`[data-show="${id}"]`);
    if (box instanceof HTMLInputElement) box.checked = !view.hidden.includes(id);
  }
  eegPlot.setPaused(!cardShown("eeg"));
  specPlot.setPaused(!cardShown("spectrum"));
  timelinePlot.setPaused(!cardShown("timeline"));
  hrPlot.setPaused(!cardShown("heart"));
  eegPlot.setWindow(traceWindow);
  timelinePlot.setWindow(timelineMinutes * 60);
  hrPlot.setWindow(timelineMinutes * 60);
  for (const button of document.querySelectorAll<HTMLButtonElement>("#minute-toggle button")) {
    button.classList.toggle("on", Number(button.dataset.minutes) === timelineMinutes);
  }
  const deviceBtn = document.getElementById("device-lines");
  if (deviceBtn) {
    deviceBtn.classList.toggle("on", view.showDevice);
    deviceBtn.setAttribute("aria-pressed", view.showDevice ? "true" : "false");
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>("#trace-window button")) {
    button.classList.toggle("on", Number(button.dataset.window) === traceWindow);
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>("#trace-mode button")) {
    button.classList.toggle("on", button.dataset.mode === traceMode);
  }
  saveView();
  if (!view.hidden.includes("recordings") && !view.calm) void loadRecordings();
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

async function downloadFile(path: string, filename: string): Promise<void> {
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { headers });
  if (!response.ok) throw new Error(`Download failed (${response.status})`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function loadRecordings(): Promise<void> {
  const host = document.getElementById("recordings");
  const panel = document.querySelector('[data-card="recordings"]');
  if (!host || !(panel instanceof HTMLElement) || panel.hidden) return;
  try {
    const rows = await api<SessionRow[]>("/api/sessions");
    if (!rows.length) {
      host.innerHTML = `<p class="hint">No sessions yet. Record one, then it will show up here.</p>`;
      return;
    }
    host.innerHTML = rows
      .map((row) => {
        const subject = encodeURIComponent(row.subject_id);
        const session = encodeURIComponent(row.session_id);
        const when = row.local_start ? formatSessionWhen(row.local_start) : row.session_id;
        const duration = row.duration_sec == null ? "—" : formatSec(row.duration_sec);
        const streams = (row.streams ?? []).join(", ") || "session";
        const files = (row.inventory ?? [])
          .map(
            (file) =>
              `<button type="button" data-file="${esc(file.name)}" data-subject="${esc(row.subject_id)}" data-session="${esc(row.session_id)}">${esc(file.name.split("/").pop() || file.name)} · ${formatBytes(file.bytes)}</button>`,
          )
          .join("");
        const disclosure = files
          ? `<details class="file-list"><summary>Files</summary><div class="file-grid">${files}</div></details>`
          : "";
        return `<article class="recording">
          <header><strong>${esc(when)}</strong> <span class="muted">${esc(row.task)} · ${duration} · ${row.eeg_samples.toLocaleString()} EEG · ${esc(streams)}</span></header>
          <div class="files">
            <button type="button" data-zip="${subject}/${session}">Download zip</button>
            <button type="button" data-delete="${esc(row.subject_id)}" data-session="${esc(row.session_id)}">Delete</button>
            ${disclosure}
          </div>
        </article>`;
      })
      .join("");
  } catch (error) {
    host.innerHTML = `<p class="hint">${esc(error instanceof Error ? error.message : "Could not list sessions")}</p>`;
  }
}

function renderBands(bands: Record<string, BandPower>): void {
  const names = Object.keys(bands);
  const key = names.join(",");
  if (key !== bandKey) {
    bandKey = key;
    bandBars.innerHTML = names
      .map(
        (name) =>
          `<div class="band" data-band="${esc(name)}"><b></b><div class="track-bar"><i></i></div><em></em></div>`,
      )
      .join("");
  }
  for (const name of names) {
    const row = bandBars.querySelector(`[data-band="${CSS.escape(name)}"]`);
    if (!row) continue;
    const band = bands[name];
    const pct = Math.max(0, Math.min(100, band.relative_mean * 100));
    const label = row.querySelector("b");
    const bar = row.querySelector("i");
    const value = row.querySelector("em");
    if (label) label.textContent = `${name} ${band.f_low}–${band.f_high}`;
    if (bar instanceof HTMLElement) {
      bar.style.width = `${pct}%`;
      bar.style.background = band.color;
    }
    if (value) value.textContent = `${pct.toFixed(0)}%`;
  }
}

function renderRatios(items: Record<string, { expression: string; mean: number | null }>): void {
  const names = Object.keys(items);
  const key = names.join(",");
  if (key !== ratioKey) {
    ratioKey = key;
    ratiosEl.innerHTML = names.map((name) => `<div data-ratio="${esc(name)}"><strong>—</strong><span></span></div>`).join("");
  }
  for (const name of names) {
    const row = ratiosEl.querySelector(`[data-ratio="${CSS.escape(name)}"]`);
    const item = items[name];
    const strong = row?.querySelector("strong");
    const span = row?.querySelector("span");
    if (strong) strong.textContent = item.mean == null || Number.isNaN(item.mean) ? "—" : item.mean.toFixed(2);
    if (span) span.textContent = `${name}  ${item.expression}`;
  }
}

function renderMarkers(): void {
  const markers = profile?.markers ?? [];
  markerButtons.innerHTML = markers
    .map((marker) => {
      const key = marker.hotkey ? ` (${marker.hotkey})` : "";
      return `<button type="button" data-marker="${esc(marker.name)}" style="border-color:${esc(marker.color)}">${esc(marker.name)}${esc(key)}</button>`;
    })
    .join("");
  paintTrack();
}

function heartLabel(heart: Heart | null | undefined): string {
  if (!heart || heart.state === "no_ppg") return "no PPG data";
  if (heart.state !== "ready" || heart.bpm == null || !Number.isFinite(heart.bpm)) return "computing";
  return heart.bpm.toFixed(0);
}

function showHeart(heart: Heart | null | undefined, t?: number): void {
  const label = heartLabel(heart);
  const ready = Boolean(heart && heart.state === "ready" && heart.bpm != null);
  hrEl.textContent = label;
  hrEl.closest(".hr-readout")?.classList.toggle("ready", ready);
  const headerUnit = document.getElementById("hr-header-unit");
  if (headerUnit) headerUnit.hidden = !ready;
  const value = document.getElementById("hr-value");
  if (value) value.textContent = label;
  const unit = document.getElementById("hr-unit");
  if (unit) unit.hidden = !ready;
  const detail = document.getElementById("hr-detail");
  if (detail) detail.textContent = ready ? `${label} bpm` : label;
  const meta = document.getElementById("hr-meta");
  if (meta) {
    if (!heart || heart.state === "no_ppg") meta.textContent = "Waiting for /muse/ppg or a headset heart-rate path.";
    else if (!ready) meta.textContent = "PPG is arriving. Looking for a steady pulse.";
    else {
      const bits = [heart.source === "device" ? "headset" : heart.channel ? `channel ${heart.channel}` : "PPG"];
      if (heart.quality) bits.push(heart.quality);
      if (heart.rmssd != null && Number.isFinite(heart.rmssd)) bits.push(`RMSSD ${heart.rmssd.toFixed(0)} ms`);
      meta.textContent = bits.join(" · ");
    }
  }
  const temp = document.getElementById("hr-temp");
  const celsius = heart?.temperature;
  if (temp) {
    if (typeof celsius === "number" && Number.isFinite(celsius)) {
      temp.hidden = false;
      temp.textContent = `${celsius.toFixed(0)} °C`;
    } else {
      temp.hidden = true;
      temp.textContent = "";
    }
  }
  if (!ready || heart?.bpm == null) return;
  const stamp = typeof t === "number" && Number.isFinite(t) ? t : Date.now() / 1000;
  if (stamp - lastHrT < 1) return;
  lastHrT = stamp;
  hrPlot.pushTimed(["bpm"], [{ t: stamp, values: [heart.bpm] }]);
}

function paintTrack(): void {
  const now = Date.now() / 1000;
  const span = 20;
  markerTrack.innerHTML = events
    .filter((event) => now - event.t < span)
    .filter((event) => showAuto || (event.kind !== "blink" && event.kind !== "jaw_clench"))
    .map((event) => {
      const left = Math.min(97, Math.max(0, ((event.t - (now - span)) / span) * 100));
      return `<i title="${esc(event.kind)} ${esc(event.name)}" style="left:${left}%;background:${esc(event.color)}"></i>`;
    })
    .join("");
}

function applyProfile(next: Profile): void {
  profile = next;
  profileName.textContent = next.name;
  renderMarkers();
  if (drawerMode === "profile") renderProfileForm();
}

function applyStatus(status: Status): void {
  const input = status.input;
  inputLabel.textContent = `${input.type} · ${input.eeg_fs.toFixed(0)} Hz · scale ${input.eeg_scale}`;
  eegMeta.textContent = input.error ? input.error : `${input.samples_seen.toLocaleString()} samples`;
  if (socket?.readyState === WebSocket.OPEN) {
    if (status.playback?.file) {
      setConnection(playbackText(status.playback), status.playback.paused || input.error ? "wait" : "live");
    } else {
      setConnection(input.error ? "input error" : input.running ? "live" : "stopped", input.error || !input.running ? "bad" : "live");
    }
  }
  applyPlayback(status.playback);
  showHeart(status.heart ?? (status.hr != null ? { state: "ready", bpm: status.hr, rmssd: null, quality: null, channel: null, source: null } : null));
  const paths = document.getElementById("cfg-paths");
  if (paths && drawerMode === "setup") {
    const rows = status.osc_paths ?? [];
    paths.innerHTML = rows.length
      ? rows.map((row) => `<li>${esc(row.path)} <span>${row.count.toLocaleString()}</span></li>`).join("")
      : "<li>No OSC paths yet.</li>";
  }
  applyRecording(status.recording);
  profileName.textContent = status.profile;
}

function onMessage(raw: string): void {
  const message = JSON.parse(raw) as { type: string } & Record<string, unknown>;
  if (message.type === "hello") {
    applyProfile(message.profile as Profile);
    config = message.config as Config;
    applyStatus(message.status as Status);
    events = ((message.events as LiveEvent[]) ?? []).slice(-40);
    paintTrack();
    return;
  }
  if (message.type === "display") {
    const eeg = message.eeg as {
      channels?: string[];
      points?: { t: number; raw: number[]; filtered: number[]; gap?: boolean }[];
    };
    if (eeg.channels?.length) eegChannels = eeg.channels;
    for (const point of eeg.points ?? []) {
      traceHistory.push({
        t: point.t,
        raw: point.raw,
        filtered: point.filtered,
        gap: Boolean(point.gap),
      });
    }
    const newest = traceHistory[traceHistory.length - 1]?.t;
    if (newest != null) {
      const min = newest - 35;
      while (traceHistory.length > 2 && traceHistory[0].t < min) traceHistory.shift();
    }
    if (cardShown("eeg")) syncTraces(false);
    const playback = message.playback as PlaybackInfo | null | undefined;
    if (playback?.file) {
      setConnection(playbackText(playback), playback.paused ? "wait" : "live");
      applyPlayback(playback);
    }
    if (Array.isArray(message.quality)) lastQuality = message.quality as number[];
    renderHorseshoe(eegChannels, lastQuality);
    showHeart(message.heart as Heart | undefined, typeof message.t === "number" ? message.t : undefined);
    applyRecording((message.recording as Recording | null) ?? null);
    return;
  }
  if (message.type === "features") {
    pendingBands = (message.bands as Record<string, BandPower>) ?? {};
    pendingRatios = (message.ratios as Record<string, { expression: string; mean: number | null }>) ?? {};
    const spectrum = message.spectrum as { freqs: number[]; psd_log10: number[] };
    const stamp = typeof message.t === "number" ? message.t : 0;
    if (spectrum?.freqs?.length && cardShown("spectrum")) {
      const dt = specStamp ? Math.min(2, stamp - specStamp) : 0.5;
      specStamp = stamp;
      specSmooth = smoothSeries(specSmooth, spectrum.psd_log10, dt, view.smoothSec * 2);
      specPlot.setSeries(spectrum.freqs, specSmooth);
    }
    const timeline = message.timeline as
      | Record<string, { log10: number | null; device: number | null; f_low?: number; f_high?: number; color?: string }>
      | undefined;
    if (timeline && stamp) {
      const dt = emaStamp ? Math.min(2, stamp - emaStamp) : 0;
      emaStamp = stamp;
      const smoothed: Record<string, number | null> = {};
      const device: Record<string, number | null> = {};
      for (const name of TIMELINE) {
        const sample = timeline[name];
        const meta = bandMeta.find((band) => band.name === name);
        if (meta && sample) {
          if (typeof sample.f_low === "number") meta.low = sample.f_low;
          if (typeof sample.f_high === "number") meta.high = sample.f_high;
          if (typeof sample.color === "string" && sample.color) meta.color = sample.color;
        }
        const value = sample?.log10;
        if (typeof value === "number" && Number.isFinite(value)) {
          timelineEma[name] = ema(timelineEma[name], value, dt, view.smoothSec);
          smoothed[name] = timelineEma[name];
        } else {
          smoothed[name] = timelineEma[name] ?? null;
        }
        device[name] = typeof sample?.device === "number" ? sample.device : null;
        if (device[name] != null) timelineDevice[name] = device[name];
      }
      if (stamp - lastTimelineT >= 0.5) {
        lastTimelineT = stamp;
        timelineHistory.push({ t: stamp, values: smoothed, device });
        const min = stamp - 30 * 60;
        while (timelineHistory.length > 2 && timelineHistory[0].t < min) timelineHistory.shift();
        syncTimeline(false);
      }
    }
    if (!slowTimer) {
      slowTimer = window.setTimeout(() => {
        slowTimer = 0;
        if (pendingBands && cardShown("bands")) renderBands(pendingBands);
        if (pendingRatios && cardShown("ratios")) renderRatios(pendingRatios);
      }, 500);
    }
    const dropped = message.excluded as string[] | undefined;
    if (dropped) {
      excluded = new Set(dropped);
      renderHorseshoe(eegChannels, lastQuality);
    }
    const feedback = message.feedback as { volume: number; frequency: number };
    setFeedback(feedback.volume, feedback.frequency);
    showHeart(message.heart as Heart | undefined, typeof message.t === "number" ? message.t : undefined);
    const quality = message.quality as number[] | null;
    const channels = (message.channels as string[]) ?? [];
    if (quality) renderHorseshoe(channels, quality);
    return;
  }
  if (message.type === "event") {
    events.push(message as unknown as LiveEvent);
    if (events.length > 40) events.shift();
    paintTrack();
    return;
  }
  if (message.type === "status") applyStatus(message as unknown as Status);
  if (message.type === "profile") applyProfile(message.profile as Profile);
}

function connect(): void {
  if (reconnectTimer) window.clearTimeout(reconnectTimer);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  const next = new WebSocket(`${proto}://${location.host}/ws${query}`);
  socket = next;
  setConnection("connecting", "wait");
  next.onopen = () => setConnection("connected", "wait");
  next.onmessage = (event) => onMessage(String(event.data));
  next.onclose = () => {
    if (socket !== next) return;
    setConnection("reconnecting", "bad");
    reconnectTimer = window.setTimeout(connect, 1200);
  };
}

function sendMarker(name: string): void {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "marker", name }));
    return;
  }
  void api("/api/markers", { method: "POST", body: JSON.stringify({ name }) }).catch((error: Error) => showToast(error.message));
}

let setupPoll = 0;

function openDrawer(mode: "profile" | "setup"): void {
  drawerMode = mode;
  drawer.hidden = false;
  drawerTitle.textContent = mode === "profile" ? "Profile" : "Setup";
  if (mode === "profile") renderProfileForm();
  else {
    renderSetupForm();
    void refreshSetupLive();
    if (setupPoll) window.clearInterval(setupPoll);
    setupPoll = window.setInterval(() => void refreshSetupLive(), 1000);
  }
}

function closeDrawer(): void {
  drawer.hidden = true;
  drawerMode = null;
  if (setupPoll) window.clearInterval(setupPoll);
  setupPoll = 0;
}

async function refreshSetupLive(): Promise<void> {
  if (drawerMode !== "setup") return;
  try {
    const status = await api<Status>("/api/status");
    const input = status.input;
    const state = input.error ? input.error : input.running ? "live" : "stopped";
    const active = document.getElementById("cfg-active");
    if (active) active.textContent = `Active now: ${input.type} · ${input.eeg_fs.toFixed(0)} Hz · ${state}`;
    const packets = document.getElementById("cfg-packets");
    if (packets) packets.textContent = `Packets received: ${input.samples_seen.toLocaleString()}`;
  } catch {
    /* the form stays usable if a poll fails */
  }
}

function field(label: string, html: string): string {
  return `<label>${esc(label)}${html}</label>`;
}

function renderProfileForm(): void {
  const current = profile;
  if (!current) {
    drawerBody.innerHTML = "<p>Waiting for the server.</p>";
    return;
  }
  const bandRows = current.bands
    .map(
      (band) => `<div class="row band-row">
        <input data-k="name" value="${esc(band.name)}" aria-label="band name" />
        <input data-k="low" type="number" step="0.1" value="${band.f_low}" aria-label="low Hz" />
        <input data-k="high" type="number" step="0.1" value="${band.f_high}" aria-label="high Hz" />
        <input data-k="color" type="color" value="${esc(band.color)}" aria-label="color" />
        <button type="button" data-remove="band">Remove</button>
      </div>`,
    )
    .join("");
  const ratioRows = current.ratios
    .map(
      (ratio) => `<div class="row">
        <input data-k="name" value="${esc(ratio.name)}" aria-label="ratio name" />
        <input data-k="expression" value="${esc(ratio.expression)}" aria-label="expression" />
        <button type="button" data-remove="ratio">Remove</button>
      </div>`,
    )
    .join("");
  const markerRows = current.markers
    .map(
      (marker) => `<div class="row marker-row">
        <input data-k="name" value="${esc(marker.name)}" aria-label="marker name" />
        <input data-k="color" type="color" value="${esc(marker.color)}" aria-label="color" />
        <input data-k="hotkey" maxlength="1" value="${esc(marker.hotkey)}" aria-label="hotkey" />
        <input data-k="osc" type="number" min="1" max="16" value="${marker.osc ?? ""}" aria-label="OSC number" />
        <button type="button" data-remove="marker">Remove</button>
      </div>`,
    )
    .join("");
  const rules = current.feedback
    .map(
      (rule) => `<div class="rule">
        <div class="row">
          <input data-k="metric" value="${esc(rule.metric)}" aria-label="metric" />
          <select data-k="source" aria-label="source">${opt(["relative", "absolute"], rule.source)}</select>
          <select data-k="param" aria-label="parameter">${opt(["volume", "frequency"], rule.param)}</select>
          <input data-k="smooth" type="number" min="0" max="1" step="0.05" value="${rule.smooth}" aria-label="smooth" />
        </div>
        <div class="row">
          <input data-k="in_min" type="number" step="any" value="${rule.in_min}" aria-label="in min" />
          <input data-k="in_max" type="number" step="any" value="${rule.in_max}" aria-label="in max" />
          <input data-k="out_min" type="number" step="any" value="${rule.out_min}" aria-label="out min" />
          <input data-k="out_max" type="number" step="any" value="${rule.out_max}" aria-label="out max" />
        </div>
        <div class="row">
          <input data-k="gate_metric" value="${esc(rule.gate_metric ?? "")}" placeholder="gate metric" aria-label="gate metric" />
          <select data-k="gate_mode" aria-label="gate">${opt(["none", "mute_above", "mute_below"], rule.gate_mode)}</select>
          <input data-k="gate_threshold" type="number" step="any" value="${rule.gate_threshold ?? ""}" aria-label="gate threshold" />
          <button type="button" data-remove="rule">Remove</button>
        </div>
      </div>`,
    )
    .join("");
  drawerBody.innerHTML = `
    <p class="hint">Names use letters, digits, and underscore. Band edges are inclusive. Saving replaces the live profile.</p>
    <div class="stack">
      ${field("Saved profiles", `<select id="profile-pick"></select>`)}
      ${field("Name", `<input id="pf-name" value="${esc(current.name)}" />`)}
      ${field("Description", `<textarea id="pf-desc" rows="2">${esc(current.description)}</textarea>`)}
      <div class="row">
        <input id="pf-low" type="number" step="0.1" value="${current.relative_range[0]}" aria-label="relative low" />
        <input id="pf-high" type="number" step="0.1" value="${current.relative_range[1]}" aria-label="relative high" />
        <input id="pf-window" type="number" step="0.5" value="${current.window_sec}" aria-label="window seconds" />
        <input id="pf-hop" type="number" step="0.05" value="${current.hop_sec}" aria-label="hop seconds" />
      </div>
    </div>
    <h3>Bands</h3>
    <div id="band-editor">${bandRows}</div>
    <button type="button" id="add-band">Add band</button>
    <h3>Ratios</h3>
    <div id="ratio-editor">${ratioRows}</div>
    <button type="button" id="add-ratio">Add ratio</button>
    <h3>Markers</h3>
    <div id="marker-editor">${markerRows}</div>
    <button type="button" id="add-marker">Add marker</button>
    <h3>Feedback</h3>
    <p class="hint">Map a metric onto volume or frequency. A gate fades volume when another band dominates.</p>
    <div id="rule-editor">${rules}</div>
    <button type="button" id="add-rule">Add rule</button>
    <div class="row" style="margin-top:0.8rem">
      <button type="button" id="save-profile" class="primary">Save profile</button>
    </div>`;
  void fillProfilePick(current.name);
}

function opt(values: string[], selected: string): string {
  return values.map((value) => `<option value="${value}"${value === selected ? " selected" : ""}>${value}</option>`).join("");
}

async function fillProfilePick(active: string): Promise<void> {
  const select = document.getElementById("profile-pick") as HTMLSelectElement | null;
  if (!select) return;
  const rows = await api<{ name: string }[]>("/api/profiles");
  select.innerHTML = rows.map((row) => `<option value="${esc(row.name)}">${esc(row.name)}</option>`).join("");
  select.value = active;
  select.addEventListener("change", () => {
    void api<Profile>("/api/active-profile", { method: "POST", body: JSON.stringify({ name: select.value }) })
      .then((next) => applyProfile(next))
      .catch((error: Error) => showToast(error.message));
  });
}

function num(el: Element | null, fallback: number): number {
  const value = el instanceof HTMLInputElement || el instanceof HTMLSelectElement ? Number(el.value) : Number.NaN;
  return Number.isFinite(value) ? value : fallback;
}

function text(el: Element | null): string {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement
    ? el.value.trim()
    : "";
}

function readProfile(): Profile {
  const bands = [...drawerBody.querySelectorAll("#band-editor .band-row")].map((row) => ({
    name: text(row.querySelector('[data-k="name"]')),
    f_low: num(row.querySelector('[data-k="low"]'), 0),
    f_high: num(row.querySelector('[data-k="high"]'), 1),
    color: text(row.querySelector('[data-k="color"]')) || "#8eb6ff",
  }));
  const ratios = [...drawerBody.querySelectorAll("#ratio-editor .row")].map((row) => ({
    name: text(row.querySelector('[data-k="name"]')),
    expression: text(row.querySelector('[data-k="expression"]')),
  }));
  const markers = [...drawerBody.querySelectorAll("#marker-editor .marker-row")].map((row) => {
    const oscRaw = text(row.querySelector('[data-k="osc"]'));
    return {
      name: text(row.querySelector('[data-k="name"]')),
      color: text(row.querySelector('[data-k="color"]')) || "#d4a0ff",
      hotkey: text(row.querySelector('[data-k="hotkey"]')).toLowerCase(),
      osc: oscRaw ? Number(oscRaw) : null,
    };
  });
  const feedback = [...drawerBody.querySelectorAll("#rule-editor .rule")].map((row) => {
    const gate = text(row.querySelector('[data-k="gate_metric"]'));
    const threshold = text(row.querySelector('[data-k="gate_threshold"]'));
    return {
      metric: text(row.querySelector('[data-k="metric"]')),
      source: text(row.querySelector('[data-k="source"]')) || "relative",
      scope: "mean",
      param: text(row.querySelector('[data-k="param"]')) || "volume",
      in_min: num(row.querySelector('[data-k="in_min"]'), 0),
      in_max: num(row.querySelector('[data-k="in_max"]'), 1),
      out_min: num(row.querySelector('[data-k="out_min"]'), 0),
      out_max: num(row.querySelector('[data-k="out_max"]'), 1),
      smooth: num(row.querySelector('[data-k="smooth"]'), 0.35),
      gate_metric: gate || null,
      gate_source: "relative",
      gate_scope: "mean",
      gate_threshold: threshold ? Number(threshold) : null,
      gate_mode: text(row.querySelector('[data-k="gate_mode"]')) || "none",
    };
  });
  return {
    name: text(document.getElementById("pf-name")),
    description: text(document.getElementById("pf-desc")),
    bands,
    ratios,
    markers,
    feedback,
    relative_range: [num(document.getElementById("pf-low"), 1), num(document.getElementById("pf-high"), 45)],
    window_sec: num(document.getElementById("pf-window"), 4),
    hop_sec: num(document.getElementById("pf-hop"), 0.25),
  };
}

function choiceButtons(selected: string): string {
  return INPUTS.map(
    (item) =>
      `<button type="button" class="choice${item.id === selected ? " on" : ""}" data-input="${item.id}" aria-pressed="${item.id === selected}">
        <strong>${esc(item.title)}</strong><small>${esc(item.blurb)}</small>
      </button>`,
  ).join("");
}

function renderSetupForm(): void {
  const cfg = config;
  const selected = cfg?.input_type ?? "synthetic";
  const power = cfg?.powerline_hz === 60 ? 60 : 50;
  const board = cfg?.brainflow_board ?? "synthetic";
  const boards = BOARDS.includes(board as (typeof BOARDS)[number]) ? [...BOARDS] : [board, ...BOARDS];
  drawerBody.innerHTML = `
    <p id="cfg-active" class="active-line">Active now: ${esc(selected)}</p>
    <div id="cfg-inputs" class="choices" data-selected="${esc(selected)}">${choiceButtons(selected)}</div>
    <div class="stack for-input" data-for="osc" ${selected === "osc" ? "" : "hidden"}>
      ${field("OSC port", `<input id="cfg-osc-port" type="number" min="1" max="65535" inputmode="numeric" value="${cfg?.osc_port ?? 5000}" />`)}
      <p class="hint">Mind Monitor → OSC Stream Target IP = this computer, port = the published host port.</p>
      <p id="cfg-packets" class="active-line">Packets received: —</p>
      <p class="hint">OSC paths seen</p>
      <ul id="cfg-paths" class="path-list"><li>No OSC paths yet.</li></ul>
    </div>
    <div class="stack for-input" data-for="brainflow" ${selected === "brainflow" ? "" : "hidden"}>
      ${field("Board", `<select id="cfg-board">${boards.map((name) => `<option value="${esc(name)}"${name === board ? " selected" : ""}>${esc(name)}</option>`).join("")}</select>`)}
      <button type="button" id="cfg-ble-scan">Scan for devices</button>
      <p class="hint">Needs the host's Bluetooth. In Docker, run with compose.host.yaml.</p>
      <p id="cfg-ble-note" class="hint"></p>
      <div id="cfg-ble-list" class="device-list"></div>
      ${field("MAC address", `<input id="cfg-mac" value="${esc(cfg?.brainflow_mac ?? "")}" placeholder="AA:BB:CC:DD:EE:FF" autocomplete="off" />`)}
      ${field("Serial", `<input id="cfg-serial" value="${esc(cfg?.brainflow_serial ?? "")}" autocomplete="off" />`)}
    </div>
    <div class="stack for-input" data-for="lsl" ${selected === "lsl" ? "" : "hidden"}>
      ${field("Discovered streams", `<select id="cfg-lsl-list"><option value="">Any EEG stream</option></select>`)}
      <button type="button" id="cfg-lsl-refresh">Find streams</button>
      <p id="cfg-lsl-note" class="hint"></p>
      ${field("Stream name", `<input id="cfg-lsl" value="${esc(cfg?.lsl_name ?? "")}" placeholder="blank uses the first EEG stream" />`)}
    </div>
    <div class="stack for-input" data-for="playback" ${selected === "playback" ? "" : "hidden"}>
      ${field("File", `<select id="cfg-file"><option value="">Custom path…</option></select>`)}
      ${field("Path", `<input id="cfg-playback" value="${esc(cfg?.playback_path ?? "")}" placeholder="/data/demo.csv" />`)}
      ${field("Speed", `<input id="cfg-speed" type="number" min="0" step="0.1" value="${cfg?.playback_speed ?? 1}" />`)}
      <p class="hint">0 plays as fast as possible. 1 follows the sample rate. AUX columns are not EEG lanes.</p>
      <label class="hint">Upload a CSV<input id="cfg-upload" type="file" accept=".csv,text/csv" /></label>
      <p id="cfg-play-note" class="hint"></p>
    </div>
    <div class="stack for-input" data-for="synthetic" ${selected === "synthetic" ? "" : "hidden"}>
      <p class="hint">Nothing else to set. Apply starts the built-in theta-dominant signal.</p>
    </div>
    <h3>Signal</h3>
    <div class="stack">
      <div class="seg">
        <button type="button" data-scale="1">1 · Mind Monitor</button>
        <button type="button" data-scale="4.5">4.5</button>
        <button type="button" data-scale="0.22">0.22</button>
      </div>
      ${field("EEG scale", `<input id="cfg-scale" type="number" min="0.0001" max="1000" step="0.01" value="${cfg?.eeg_scale ?? 1}" />`)}
      <p class="hint">Mind Monitor OSC is already in µV, so leave this at 1. BrainFlow and muse-lsl can disagree by about 4.5× on Athena.</p>
      <div class="seg" id="cfg-line">
        <button type="button" data-line="50" class="${power === 50 ? "on" : ""}">50 Hz</button>
        <button type="button" data-line="60" class="${power === 60 ? "on" : ""}">60 Hz</button>
      </div>
      <input id="cfg-power" type="hidden" value="${power}" />
      <p class="hint">Powerline sets the display notch. Recorded EEG stays unfiltered.</p>
    </div>
    <h3>Display</h3>
    <div class="stack">
      ${field("Band chart", `<select id="cfg-minutes">${[1, 5, 15, 30].map((value) => `<option value="${value}"${value === timelineMinutes ? " selected" : ""}>${value} min</option>`).join("")}</select>`)}
      ${field("EEG window", `<select id="cfg-trace">${[5, 10, 30].map((value) => `<option value="${value}"${value === traceWindow ? " selected" : ""}>${value} s</option>`).join("")}</select>`)}
      ${field("Smoothing", `<select id="cfg-smooth">${[0.5, 1, 2].map((value) => `<option value="${value}"${value === view.smoothSec ? " selected" : ""}>${value} s</option>`).join("")}</select>`)}
    </div>
    <h3>Session</h3>
    <div class="stack">
      ${field("Profile", `<select id="cfg-profile"><option value="${esc(profile?.name ?? "")}">${esc(profile?.name ?? "…")}</option></select>`)}
      ${field("Subject", `<input id="cfg-subject" value="${esc(subject.value)}" maxlength="32" />`)}
      ${field("Task", `<input id="cfg-task" value="${esc(task.value)}" maxlength="32" />`)}
      ${field("Notes", `<input id="cfg-notes" value="${esc(notes.value)}" maxlength="240" />`)}
    </div>
    <details class="advanced">
      <summary>Advanced</summary>
      <div class="stack">
        ${field("OSC bind host", `<input id="cfg-osc-host" value="${esc(cfg?.osc_host ?? "0.0.0.0")}" />`)}
        ${field("EEG sample rate", `<input id="cfg-fs" type="number" min="1" max="2048" value="${cfg?.eeg_fs ?? 256}" />`)}
        ${field("Display rate (Hz)", `<input id="cfg-display" type="number" min="1" max="256" value="${cfg?.display_hz ?? 64}" />`)}
        ${field("BrainFlow other info", `<input id="cfg-other" value="${esc(cfg?.brainflow_other_info ?? "")}" />`)}
        <label><input id="cfg-loop" type="checkbox" ${cfg?.playback_loop !== false ? "checked" : ""}/> Loop playback</label>
        <label><input id="cfg-audio" type="checkbox" ${cfg?.server_audio ? "checked" : ""}/> Host speaker (aplay)</label>
        ${field("Access token for this browser", `<input id="cfg-token" type="password" value="${esc(token)}" autocomplete="off" />`)}
        <a href="/docs" target="_blank" rel="noreferrer">OpenAPI docs</a>
      </div>
    </details>
    <p id="cfg-feedback" class="form-note" role="status"></p>
    <button type="button" id="save-setup" class="primary">Apply</button>`;
  void fillSetupLists();
}

interface BrainflowBoard {
  value: string;
  enum: string;
  board_id: number;
  label: string;
}
interface BleDevice {
  name: string;
  mac: string;
  rssi: number | null;
  serial: string;
  board: string;
  board_id: number;
  board_label: string;
  note: string;
}

async function fillSetupLists(): Promise<void> {
  const files = document.getElementById("cfg-file");
  const currentPath = text(document.getElementById("cfg-playback"));
  if (files instanceof HTMLSelectElement) {
    try {
      const body = await api<{ files: { path: string; name: string; eeg: boolean }[] }>("/api/playback/files");
      const options = body.files
        .map((file) => {
          const label = file.eeg ? file.name : `${file.name} (not EEG)`;
          return `<option value="${esc(file.path)}"${file.eeg ? "" : " disabled"}>${esc(label)}</option>`;
        })
        .join("");
      files.innerHTML = `<option value="">Custom path…</option>${options}`;
      if (currentPath && body.files.some((file) => file.path === currentPath && file.eeg)) files.value = currentPath;
    } catch {
      /* the path field still works */
    }
  }
  const boardPick = document.getElementById("cfg-board");
  if (boardPick instanceof HTMLSelectElement) {
    try {
      const body = await api<{ boards: BrainflowBoard[] }>("/api/brainflow/boards");
      const current = boardPick.value;
      boardPick.innerHTML = body.boards
        .map((row) => `<option value="${esc(row.value)}">${esc(row.label)}</option>`)
        .join("");
      ensureBoardOption(boardPick, current, current);
      boardPick.value = current;
    } catch {
      /* the short Muse list still works */
    }
  }
  const profilePick = document.getElementById("cfg-profile");
  if (profilePick instanceof HTMLSelectElement) {
    try {
      const rows = await api<{ name: string }[]>("/api/profiles");
      profilePick.innerHTML = rows.map((row) => `<option value="${esc(row.name)}">${esc(row.name)}</option>`).join("");
      if (profile?.name) profilePick.value = profile.name;
    } catch {
      /* keep the current name */
    }
  }
}

function setupFeedback(message: string, kind: "ok" | "bad"): void {
  const note = document.getElementById("cfg-feedback");
  if (!note) return;
  note.textContent = message;
  note.className = kind === "ok" ? "form-note ok" : "form-note bad";
}

function selectInput(kind: string): void {
  const wrap = document.getElementById("cfg-inputs");
  if (wrap) wrap.dataset.selected = kind;
  for (const button of drawerBody.querySelectorAll<HTMLButtonElement>("[data-input]")) {
    const on = button.dataset.input === kind;
    button.classList.toggle("on", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  }
  for (const block of drawerBody.querySelectorAll<HTMLElement>("[data-for]")) {
    block.hidden = block.dataset.for !== kind;
  }
}

function ensureBoardOption(select: HTMLSelectElement, value: string, label: string): void {
  if (!value || [...select.options].some((option) => option.value === value)) return;
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label || value;
  select.append(option);
}

async function scanBrainflowDevices(): Promise<void> {
  const note = document.getElementById("cfg-ble-note");
  const list = document.getElementById("cfg-ble-list");
  const button = document.getElementById("cfg-ble-scan");
  if (button instanceof HTMLButtonElement) button.disabled = true;
  if (note) {
    note.className = "hint";
    note.textContent = "Scanning…";
  }
  try {
    const body = await api<{ available: boolean; devices: BleDevice[]; error: string | null }>("/api/brainflow/devices");
    if (note) {
      note.className = body.error ? "form-note bad" : "hint";
      note.textContent = body.error
        ? body.error
        : body.devices.length
          ? `${body.devices.length} headset(s). Pick one to fill the board, MAC, and serial.`
          : "No compatible headsets found.";
    }
    if (list) {
      list.innerHTML = body.devices
        .map((device) => {
          const rssi = device.rssi == null ? "RSSI —" : `${device.rssi} dBm`;
          return `<button type="button" data-ble="1" data-mac="${esc(device.mac)}" data-serial="${esc(device.serial)}" data-board="${esc(device.board)}" data-board-id="${device.board_id}" data-board-label="${esc(device.board_label)}" data-note="${esc(device.note)}">
            <strong>${esc(device.name || device.mac || "Headset")}</strong>
            <small>${esc(device.mac)} · ${esc(rssi)} · ${esc(device.board_label)} (${device.board_id})</small>
          </button>`;
        })
        .join("");
    }
  } catch (error) {
    if (note) {
      note.className = "form-note bad";
      note.textContent = error instanceof Error ? error.message : "Could not scan for devices.";
    }
  } finally {
    if (button instanceof HTMLButtonElement) button.disabled = false;
  }
}

function applyBleDevice(button: HTMLButtonElement): void {
  const board = button.dataset.board ?? "";
  const label = button.dataset.boardLabel ?? board;
  const boardId = button.dataset.boardId ?? "";
  const select = document.getElementById("cfg-board");
  if (select instanceof HTMLSelectElement) {
    ensureBoardOption(select, board, boardId ? `${label} (${boardId})` : label);
    select.value = board;
  }
  const mac = document.getElementById("cfg-mac");
  if (mac instanceof HTMLInputElement) mac.value = button.dataset.mac ?? "";
  const serial = document.getElementById("cfg-serial");
  if (serial instanceof HTMLInputElement) serial.value = button.dataset.serial ?? "";
  const note = document.getElementById("cfg-ble-note");
  if (note) {
    note.className = "hint";
    note.textContent = button.dataset.note || `Using ${label} (${boardId}).`;
  }
}

async function findLslStreams(): Promise<void> {
  const note = document.getElementById("cfg-lsl-note");
  const list = document.getElementById("cfg-lsl-list");
  if (note) note.textContent = "Looking for streams…";
  try {
    const body = await api<{ available: boolean; streams: { name: string; type: string; channels: number; srate: number }[]; error: string | null }>(
      "/api/lsl/streams",
    );
    if (!(list instanceof HTMLSelectElement)) return;
    const current = text(document.getElementById("cfg-lsl"));
    const options = body.streams
      .map((stream) => {
        const label = `${stream.name || "(unnamed)"} · ${stream.type} · ${stream.srate || "?"} Hz`;
        return `<option value="${esc(stream.name)}">${esc(label)}</option>`;
      })
      .join("");
    list.innerHTML = `<option value="">Any EEG stream</option>${options}`;
    if (current) list.value = current;
    if (note) note.textContent = body.error ? body.error : body.streams.length ? `${body.streams.length} stream(s)` : "No streams found.";
  } catch (error) {
    if (note) note.textContent = error instanceof Error ? error.message : "Could not look for streams.";
  }
}

function applySetup(): void {
  const input = document.getElementById("cfg-inputs")?.dataset.selected ?? "synthetic";
  const draft = {
    input,
    oscPort: num(document.getElementById("cfg-osc-port"), Number.NaN),
    oscHost: text(document.getElementById("cfg-osc-host")) || "0.0.0.0",
    playbackPath: text(document.getElementById("cfg-playback")),
    playbackSpeed: num(document.getElementById("cfg-speed"), Number.NaN),
    eegScale: num(document.getElementById("cfg-scale"), Number.NaN),
    eegFs: num(document.getElementById("cfg-fs"), Number.NaN),
    board: text(document.getElementById("cfg-board")),
    mac: text(document.getElementById("cfg-mac")),
    lslName: text(document.getElementById("cfg-lsl")),
    powerline: num(document.getElementById("cfg-power"), Number.NaN),
    displayHz: num(document.getElementById("cfg-display"), Number.NaN),
    minutes: num(document.getElementById("cfg-minutes"), Number.NaN),
    traceWindow: num(document.getElementById("cfg-trace"), Number.NaN),
    smoothSec: num(document.getElementById("cfg-smooth"), Number.NaN),
  };
  const problem = validateSetup(draft);
  if (problem) {
    setupFeedback(problem, "bad");
    return;
  }
  subject.value = text(document.getElementById("cfg-subject"));
  task.value = text(document.getElementById("cfg-task"));
  notes.value = text(document.getElementById("cfg-notes"));
  timelineMinutes = draft.minutes;
  traceWindow = draft.traceWindow;
  view.smoothSec = draft.smoothSec;
  applyView();
  const nextToken = text(document.getElementById("cfg-token"));
  token = nextToken;
  if (token) localStorage.setItem("liminalgate.token", token);
  else localStorage.removeItem("liminalgate.token");
  const body = {
    input_type: draft.input,
    osc_host: draft.oscHost,
    osc_port: draft.oscPort,
    playback_path: draft.playbackPath,
    playback_speed: Number.isFinite(draft.playbackSpeed) ? draft.playbackSpeed : 1,
    playback_loop: (document.getElementById("cfg-loop") as HTMLInputElement | null)?.checked ?? true,
    eeg_scale: draft.eegScale,
    eeg_fs: draft.eegFs,
    brainflow_board: draft.board || "synthetic",
    brainflow_mac: draft.mac,
    brainflow_serial: text(document.getElementById("cfg-serial")),
    brainflow_other_info: text(document.getElementById("cfg-other")),
    lsl_name: draft.lslName,
    powerline_hz: draft.powerline,
    display_hz: draft.displayHz,
    server_audio: (document.getElementById("cfg-audio") as HTMLInputElement | null)?.checked ?? false,
    profile: text(document.getElementById("cfg-profile")) || undefined,
  };
  void api<Config>("/api/config", { method: "PUT", body: JSON.stringify(body) })
    .then((saved) => {
      config = saved;
      const message = `Applied. ${saved.input_type} is the active input.`;
      setupFeedback(message, "ok");
      showToast(message);
      socket?.close();
      connect();
      void refreshSetupLive();
    })
    .catch((error: Error) => {
      setupFeedback(error.message, "bad");
      showToast(error.message);
    });
}

async function describePlayback(path: string): Promise<void> {
  const note = document.getElementById("cfg-play-note");
  if (!note || !path) return;
  try {
    const info = await api<{ warning: string; rate: number; duration: number; eeg: boolean }>(
      `/api/playback/info?path=${encodeURIComponent(path)}`,
    );
    const bits = [`${info.rate.toFixed(1)} Hz`, `${info.duration.toFixed(1)} s`];
    if (!info.eeg) bits.push("not an EEG file");
    if (info.warning) bits.push(info.warning);
    note.textContent = bits.join(" · ");
  } catch (error) {
    note.textContent = error instanceof Error ? error.message : "";
  }
}

async function uploadPlayback(file: File, input: HTMLInputElement): Promise<void> {
  const note = document.getElementById("cfg-play-note");
  if (note) note.textContent = "Uploading…";
  try {
    const saved = await api<{ path: string; warning: string }>(`/api/playback/upload?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      body: await file.arrayBuffer(),
      headers: { "Content-Type": "text/csv" },
    });
    const path = document.getElementById("cfg-playback");
    if (path instanceof HTMLInputElement) path.value = saved.path;
    if (note) note.textContent = saved.warning || "Uploaded. Apply to play it.";
    await fillSetupLists();
    const files = document.getElementById("cfg-file");
    if (files instanceof HTMLSelectElement) files.value = saved.path;
  } catch (error) {
    if (note) note.textContent = error instanceof Error ? error.message : "Upload failed";
  } finally {
    input.value = "";
  }
}

drawerBody.addEventListener("change", (event) => {
  const target = event.target;
  if (target instanceof HTMLInputElement && target.id === "cfg-upload" && target.files?.[0]) {
    void uploadPlayback(target.files[0], target);
    return;
  }
  if (!(target instanceof HTMLSelectElement)) return;
  if (target.id === "cfg-file" && target.value) {
    const path = document.getElementById("cfg-playback");
    if (path instanceof HTMLInputElement) path.value = target.value;
    void describePlayback(target.value);
  }
  if (target.id === "cfg-lsl-list") {
    const name = document.getElementById("cfg-lsl");
    if (name instanceof HTMLInputElement) name.value = target.value;
  }
});

drawerBody.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === "add-band") {
    const next = readProfile();
    next.bands.push({ name: "gamma40", f_low: 40, f_high: 45, color: "#e07a8a" });
    profile = next;
    renderProfileForm();
    return;
  }
  if (target.id === "add-ratio") {
    const next = readProfile();
    next.ratios.push({ name: "new_ratio", expression: "theta/delta" });
    profile = next;
    renderProfileForm();
    return;
  }
  if (target.id === "add-marker") {
    const next = readProfile();
    next.markers.push({ name: "custom", color: "#f2d479", hotkey: "", osc: null });
    profile = next;
    renderProfileForm();
    return;
  }
  if (target.id === "add-rule") {
    const next = readProfile();
    next.feedback.push({
      metric: next.bands[0]?.name ?? "alpha",
      source: "relative",
      scope: "mean",
      param: "volume",
      in_min: 0,
      in_max: 0.4,
      out_min: 0,
      out_max: 0.7,
      smooth: 0.35,
      gate_metric: null,
      gate_source: "relative",
      gate_scope: "mean",
      gate_threshold: null,
      gate_mode: "none",
    });
    profile = next;
    renderProfileForm();
    return;
  }
  if (target.id === "save-profile") {
    const next = readProfile();
    void api<Profile>(`/api/profiles/${encodeURIComponent(next.name)}`, { method: "PUT", body: JSON.stringify(next) })
      .then((saved) => {
        applyProfile(saved);
        showToast(`Saved profile ${saved.name}`);
      })
      .catch((error: Error) => showToast(error.message));
    return;
  }
  if (target.id === "save-setup") {
    applySetup();
    return;
  }
  const bleButton = target.closest("[data-ble]");
  if (bleButton instanceof HTMLButtonElement) {
    applyBleDevice(bleButton);
    return;
  }
  const inputButton = target.closest("[data-input]");
  if (inputButton instanceof HTMLButtonElement && inputButton.dataset.input) {
    selectInput(inputButton.dataset.input);
    return;
  }
  if (target.dataset.scale) {
    const scale = document.getElementById("cfg-scale");
    if (scale instanceof HTMLInputElement) scale.value = target.dataset.scale;
    return;
  }
  if (target.dataset.line) {
    const line = document.getElementById("cfg-power");
    if (line instanceof HTMLInputElement) line.value = target.dataset.line;
    for (const button of drawerBody.querySelectorAll<HTMLButtonElement>("[data-line]")) {
      button.classList.toggle("on", button === target);
    }
    return;
  }
  if (target.id === "cfg-ble-scan") {
    void scanBrainflowDevices();
    return;
  }
  if (target.id === "cfg-lsl-refresh") {
    void findLslStreams();
    return;
  }
  const remove = target.dataset.remove;
  if (!remove) return;
  const next = readProfile();
  const row = target.closest(".band-row, .row, .rule");
  if (remove === "band" && row) next.bands.splice([...drawerBody.querySelectorAll("#band-editor .band-row")].indexOf(row), 1);
  if (remove === "ratio" && row) next.ratios.splice([...drawerBody.querySelectorAll("#ratio-editor .row")].indexOf(row), 1);
  if (remove === "marker" && row) next.markers.splice([...drawerBody.querySelectorAll("#marker-editor .marker-row")].indexOf(row), 1);
  if (remove === "rule" && row) next.feedback.splice([...drawerBody.querySelectorAll("#rule-editor .rule")].indexOf(row), 1);
  profile = next;
  renderProfileForm();
});

function paintToneButton(on = tone.running): void {
  toneBtn.textContent = on ? "Tone on" : "Tone";
  toneBtn.classList.toggle("on", on);
  toneBtn.setAttribute("aria-pressed", on ? "true" : "false");
}

let toneWanted = false;
toneBtn.addEventListener("click", () => {
  toneWanted = !toneWanted;
  paintToneButton(toneWanted);
  const action = toneWanted ? tone.start() : tone.stop();
  void action
    .then(() => {
      toneWanted = tone.running;
      paintToneButton();
      setFeedback(lastVolume, lastHz);
    })
    .catch((error: Error) => {
      toneWanted = false;
      paintToneButton();
      showToast(error.message);
    });
});

recordBtn.addEventListener("click", () => {
  const recording = recordBtn.dataset.recording === "yes";
  if (!recording) {
    void api("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ subject: subject.value, task: task.value, notes: notes.value }),
    }).catch((error: Error) => showToast(error.message));
    return;
  }
  void api<{ path?: string; files?: { edf?: string } }>("/api/sessions/stop", { method: "POST" })
    .then((result) => {
      const path = result.path ?? "";
      showToast(`Saved ${path}${result.files?.edf ? " · EDF written" : ""}`);
      savePath.textContent = path;
    })
    .catch((error: Error) => showToast(error.message));
});

profileBtn.addEventListener("click", () => openDrawer("profile"));
setupBtn.addEventListener("click", () => openDrawer("setup"));
document.getElementById("drawer-close")?.addEventListener("click", closeDrawer);
drawer.addEventListener("click", (event) => {
  if (event.target === drawer) closeDrawer();
});

document.getElementById("trace-mode")?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement) || !target.dataset.mode) return;
  traceMode = target.dataset.mode === "raw" ? "raw" : "filtered";
  applyView();
  syncTraces(true);
});

document.getElementById("trace-window")?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement) || !target.dataset.window) return;
  traceWindow = Number(target.dataset.window);
  applyView();
});

document.getElementById("minute-toggle")?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLButtonElement) || !target.dataset.minutes) return;
  timelineMinutes = Number(target.dataset.minutes);
  applyView();
});

document.getElementById("device-lines")?.addEventListener("click", () => {
  view.showDevice = !view.showDevice;
  applyView();
  syncTimeline(true);
});

document.getElementById("timeline-legend")?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const button = target.closest("button[data-series]");
  if (!(button instanceof HTMLButtonElement)) return;
  const name = button.dataset.series ?? "";
  const shown = timelinePlot.toggle(name);
  button.setAttribute("aria-pressed", shown ? "true" : "false");
});

renderTimelineLegend(legendItems(bandMeta, []));

document.querySelector("main")?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const button = target.closest("[data-collapse]");
  if (!(button instanceof HTMLButtonElement) || view.calm) return;
  const id = button.dataset.collapse as CardId;
  view.collapsed = view.collapsed.includes(id) ? view.collapsed.filter((item) => item !== id) : [...view.collapsed, id];
  applyView();
  if (id === "eeg") syncTraces(true);
  if (id === "timeline") syncTimeline(true);
  if (id === "bands" && pendingBands) renderBands(pendingBands);
  if (id === "ratios" && pendingRatios) renderRatios(pendingRatios);
});

const viewMenu = document.getElementById("view-menu");
const viewBtn = document.getElementById("view-btn");
viewBtn?.addEventListener("click", () => {
  if (!viewMenu) return;
  viewMenu.hidden = !viewMenu.hidden;
  viewBtn.setAttribute("aria-expanded", viewMenu.hidden ? "false" : "true");
});
viewMenu?.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement) || !target.dataset.show) return;
  const id = target.dataset.show as CardId;
  view.hidden = target.checked ? view.hidden.filter((item) => item !== id) : [...view.hidden, id];
  if (!target.checked) view.collapsed = view.collapsed.filter((item) => item !== id);
  applyView();
});
const showAutoBox = document.getElementById("show-auto");
if (showAutoBox instanceof HTMLInputElement) {
  showAutoBox.checked = showAuto;
  showAutoBox.addEventListener("change", () => {
    showAuto = showAutoBox.checked;
    localStorage.setItem(AUTO_KEY, showAuto ? "1" : "0");
    paintTrack();
  });
}
document.getElementById("calm-btn")?.addEventListener("click", () => {
  view.calm = !view.calm;
  if (viewMenu) viewMenu.hidden = true;
  applyView();
  syncTimeline(true);
  if (!view.calm) syncTraces(true);
});
document.addEventListener("click", (event) => {
  if (!(event.target instanceof Node) || !viewMenu || viewMenu.hidden) return;
  if (viewBtn?.parentElement?.contains(event.target)) return;
  viewMenu.hidden = true;
  viewBtn?.setAttribute("aria-expanded", "false");
});

document.getElementById("recordings")?.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const zip = target.dataset.zip;
  if (zip) {
    const [subject, session] = zip.split("/");
    void downloadFile(`/api/sessions/${subject}/${session}/archive`, `ses-${session}.zip`).catch((error: Error) => showToast(error.message));
    return;
  }
  const file = target.dataset.file;
  if (file && target.dataset.subject && target.dataset.session) {
    const subject = encodeURIComponent(target.dataset.subject);
    const session = encodeURIComponent(target.dataset.session);
    const name = file.split("/").pop() || "download";
    void downloadFile(`/api/sessions/${subject}/${session}/files/${file.split("/").map(encodeURIComponent).join("/")}`, name).catch(
      (error: Error) => showToast(error.message),
    );
    return;
  }
  if (target.dataset.delete && target.dataset.session) {
    const subject = target.dataset.delete;
    const session = target.dataset.session;
    if (!window.confirm("Delete this session from disk?")) return;
    void api(`/api/sessions/${encodeURIComponent(subject)}/${encodeURIComponent(session)}`, { method: "DELETE" })
      .then(() => loadRecordings())
      .catch((error: Error) => showToast(error.message));
  }
});

document.getElementById("recordings-refresh")?.addEventListener("click", () => void loadRecordings());

document.getElementById("play-pause")?.addEventListener("click", () => {
  const pause = document.getElementById("play-pause");
  const action = pause?.textContent === "Resume" ? "resume" : "pause";
  void api("/api/playback/control", { method: "POST", body: JSON.stringify({ action }) }).catch((error: Error) => showToast(error.message));
});
document.getElementById("play-restart")?.addEventListener("click", () => {
  void api("/api/playback/control", { method: "POST", body: JSON.stringify({ action: "restart" }) }).catch((error: Error) =>
    showToast(error.message),
  );
});
const playSeek = document.getElementById("play-seek");
playSeek?.addEventListener("pointerdown", () => {
  seeking = true;
});
playSeek?.addEventListener("change", () => {
  seeking = false;
  if (!(playSeek instanceof HTMLInputElement)) return;
  void api("/api/playback/control", {
    method: "POST",
    body: JSON.stringify({ action: "seek", position: Number(playSeek.value) }),
  }).catch((error: Error) => showToast(error.message));
});

markerButtons.addEventListener("click", (event) => {
  const target = event.target;
  if (target instanceof HTMLElement && target.dataset.marker) sendMarker(target.dataset.marker);
});

document.addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
  const key = event.key.toLowerCase();
  const marker = profile?.markers.find((item) => item.hotkey === key);
  if (!marker) return;
  event.preventDefault();
  sendMarker(marker.name);
});

window.setInterval(() => {
  if (recordAnchor) {
    const seconds = recordAnchor.elapsed + (performance.now() - recordAnchor.at) / 1000;
    const whole = Math.max(0, Math.floor(seconds));
    const label = `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
    clock.textContent = label;
    recordBtn.textContent = `Stop ${label}`;
  }
  if (cardShown("markers")) paintTrack();
}, 1000);

applyView();
connect();
