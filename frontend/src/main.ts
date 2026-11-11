import "uplot/dist/uPlot.min.css";

import { TonePlayer } from "./audio";
import { RollingPlot } from "./plots";
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

const tone = new TonePlayer();
const eegPlot = new RollingPlot($("eeg-plot"), 6);
const specPlot = new RollingPlot($("spec-plot"), 60);

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
  conn.className = kind === "wait" ? "pill" : `pill ${kind}`;
  conn.dataset.state = kind;
}

function applyRecording(rec: Recording | null): void {
  recordBtn.textContent = rec ? "Stop" : "Record";
  recordBtn.classList.toggle("recording", Boolean(rec));
  recordBtn.dataset.recording = rec ? "yes" : "no";
  if (rec) {
    recordAnchor = { elapsed: rec.elapsed, at: performance.now() };
    savePath.textContent = rec.path;
    clockLabel.textContent = "recording";
  } else if (recordAnchor) {
    recordAnchor = null;
    clock.textContent = "idle";
    clockLabel.textContent = "session";
  }
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
  if (!quality || !quality.length) {
    horseshoe.innerHTML = "";
    return;
  }
  horseshoe.innerHTML = quality
    .map((value, index) => {
      const name = channels[index] ?? `${index + 1}`;
      return `<span><i class="${qualityClass(value)}"></i>${esc(name)}</span>`;
    })
    .join("");
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

function paintTrack(): void {
  const now = Date.now() / 1000;
  const span = 20;
  markerTrack.innerHTML = events
    .filter((event) => now - event.t < span)
    .map((event) => {
      const left = ((event.t - (now - span)) / span) * 100;
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
    setConnection(input.error ? "input error" : input.running ? "live" : "stopped", input.error || !input.running ? "bad" : "live");
  }
  if (status.hr != null) hrEl.textContent = status.hr.toFixed(0);
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
    const eeg = message.eeg as { channels: string[]; t0: number; fs: number; samples: number[][] };
    if (eeg.samples.length) eegPlot.push(eeg.channels, eeg.t0, eeg.fs, eeg.samples);
    renderHorseshoe(eeg.channels, (message.quality as number[] | null) ?? null);
    if (typeof message.hr === "number") hrEl.textContent = message.hr.toFixed(0);
    applyRecording((message.recording as Recording | null) ?? null);
    return;
  }
  if (message.type === "features") {
    renderBands((message.bands as Record<string, BandPower>) ?? {});
    renderRatios((message.ratios as Record<string, { expression: string; mean: number | null }>) ?? {});
    const spectrum = message.spectrum as { freqs: number[]; psd_log10: number[] };
    if (spectrum?.freqs?.length) specPlot.setSeries(spectrum.freqs, spectrum.psd_log10);
    const feedback = message.feedback as { volume: number; frequency: number };
    setFeedback(feedback.volume, feedback.frequency);
    if (typeof message.hr === "number") hrEl.textContent = message.hr.toFixed(0);
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
  next.onopen = () => setConnection("live", "live");
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

function openDrawer(mode: "profile" | "setup"): void {
  drawerMode = mode;
  drawer.hidden = false;
  drawerTitle.textContent = mode === "profile" ? "Profile" : "Setup";
  if (mode === "profile") renderProfileForm();
  else renderSetupForm();
}

function closeDrawer(): void {
  drawer.hidden = true;
  drawerMode = null;
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

function renderSetupForm(): void {
  const cfg = config;
  drawerBody.innerHTML = `
    <p class="hint">The gateway listens for Mind Monitor OSC on the host and port below. Point OSC Stream Target at this computer. Scale multiplies raw EEG before analysis (BrainFlow and muse-lsl can differ by about 4.5× on Athena).</p>
    <div class="stack">
      ${field("Input", `<select id="cfg-input">${opt(["synthetic", "osc", "playback", "brainflow", "lsl"], cfg?.input_type ?? "synthetic")}</select>`)}
      ${field("OSC host", `<input id="cfg-osc-host" value="${esc(cfg?.osc_host ?? "0.0.0.0")}" />`)}
      ${field("OSC port", `<input id="cfg-osc-port" type="number" value="${cfg?.osc_port ?? 5000}" />`)}
      ${field("Playback file", `<input id="cfg-playback" value="${esc(cfg?.playback_path ?? "")}" placeholder="/data/demo.csv" />`)}
      ${field("Playback speed (0 = as fast as possible)", `<input id="cfg-speed" type="number" step="0.1" value="${cfg?.playback_speed ?? 1}" />`)}
      ${field("EEG scale", `<input id="cfg-scale" type="number" step="0.1" value="${cfg?.eeg_scale ?? 1}" />`)}
      ${field("EEG sample rate", `<input id="cfg-fs" type="number" value="${cfg?.eeg_fs ?? 256}" />`)}
      ${field("BrainFlow board", `<input id="cfg-board" value="${esc(cfg?.brainflow_board ?? "synthetic")}" placeholder="synthetic or muse_s_athena" />`)}
      ${field("BrainFlow MAC", `<input id="cfg-mac" value="${esc(cfg?.brainflow_mac ?? "")}" />`)}
      ${field("LSL stream name", `<input id="cfg-lsl" value="${esc(cfg?.lsl_name ?? "")}" />`)}
      ${field("Powerline Hz (metadata only)", `<input id="cfg-power" type="number" value="${cfg?.powerline_hz ?? 50}" />`)}
      ${field("Access token for this browser", `<input id="cfg-token" type="password" value="${esc(token)}" autocomplete="off" />`)}
      <label><input id="cfg-audio" type="checkbox" ${cfg?.server_audio ? "checked" : ""}/> Host speaker (aplay)</label>
      <button type="button" id="save-setup" class="primary">Apply</button>
      <a href="/docs" target="_blank" rel="noreferrer">OpenAPI docs</a>
    </div>`;
}

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
    const input = text(document.getElementById("cfg-input"));
    const body = {
      input_type: input,
      osc_host: text(document.getElementById("cfg-osc-host")),
      osc_port: num(document.getElementById("cfg-osc-port"), 5000),
      playback_path: text(document.getElementById("cfg-playback")),
      playback_speed: num(document.getElementById("cfg-speed"), 1),
      eeg_scale: num(document.getElementById("cfg-scale"), 1),
      eeg_fs: num(document.getElementById("cfg-fs"), 256),
      brainflow_board: text(document.getElementById("cfg-board")),
      brainflow_mac: text(document.getElementById("cfg-mac")),
      lsl_name: text(document.getElementById("cfg-lsl")),
      powerline_hz: num(document.getElementById("cfg-power"), 50),
      server_audio: (document.getElementById("cfg-audio") as HTMLInputElement | null)?.checked ?? false,
    };
    const nextToken = text(document.getElementById("cfg-token"));
    token = nextToken;
    if (token) localStorage.setItem("liminalgate.token", token);
    else localStorage.removeItem("liminalgate.token");
    void api<Config>("/api/config", { method: "PUT", body: JSON.stringify(body) })
      .then((saved) => {
        config = saved;
        showToast(`Input is ${saved.input_type}`);
        socket?.close();
        connect();
      })
      .catch((error: Error) => showToast(error.message));
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

toneBtn.addEventListener("click", () => {
  void tone
    .start()
    .then(() => {
      toneBtn.textContent = "Tone on";
      setFeedback(lastVolume, lastHz);
    })
    .catch((error: Error) => showToast(error.message));
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
  if (!recordAnchor) return;
  const seconds = recordAnchor.elapsed + (performance.now() - recordAnchor.at) / 1000;
  const whole = Math.max(0, Math.floor(seconds));
  clock.textContent = `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  paintTrack();
}, 250);

connect();
