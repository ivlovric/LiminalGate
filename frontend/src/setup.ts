/** Validation for the Setup panel. No DOM. */

export const INPUTS = [
  { id: "osc", title: "OSC", blurb: "Mind Monitor over UDP." },
  { id: "brainflow", title: "BrainFlow", blurb: "Direct headset, including Muse S Athena." },
  { id: "lsl", title: "LSL", blurb: "A Lab Streaming Layer EEG stream." },
  { id: "playback", title: "Playback", blurb: "Replay a recorded CSV." },
  { id: "synthetic", title: "Synthetic", blurb: "Built-in demo signal. No headset." },
] as const;

export type InputId = (typeof INPUTS)[number]["id"];

export const BOARDS = ["synthetic", "muse_s_athena", "muse_2", "muse_s", "muse_2016"] as const;

const MAC = /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/;

export interface SetupDraft {
  input: string;
  oscPort: number;
  oscHost: string;
  playbackPath: string;
  playbackSpeed: number;
  eegScale: number;
  eegFs: number;
  board: string;
  mac: string;
  lslName: string;
  powerline: number;
  displayHz: number;
  minutes: number;
  traceWindow: number;
  smoothSec: number;
}

export function validateSetup(draft: SetupDraft): string | null {
  if (!INPUTS.some((item) => item.id === draft.input)) return "Choose an input.";
  if (draft.input === "osc") {
    if (!Number.isInteger(draft.oscPort) || draft.oscPort < 1 || draft.oscPort > 65535) {
      return "OSC port must be a whole number from 1 to 65535.";
    }
    if (!draft.oscHost.trim()) return "OSC host cannot be empty.";
  }
  if (draft.input === "playback") {
    if (!draft.playbackPath.trim()) return "Choose a playback file.";
    if (!(draft.playbackSpeed >= 0)) return "Playback speed must be zero or greater. Zero plays as fast as possible.";
  }
  if (draft.input === "brainflow") {
    if (!draft.board.trim()) return "Choose a BrainFlow board.";
    if (draft.mac.trim() && !MAC.test(draft.mac.trim())) {
      return "MAC address should look like AA:BB:CC:DD:EE:FF.";
    }
  }
  if (!(draft.eegScale > 0 && draft.eegScale <= 1000)) return "EEG scale must be greater than 0 and at most 1000.";
  if (draft.powerline !== 50 && draft.powerline !== 60) return "Choose a powerline frequency of 50 or 60 Hz.";
  if (!(draft.eegFs >= 1 && draft.eegFs <= 2048)) return "Sample rate must be between 1 and 2048 Hz.";
  if (!(draft.displayHz >= 1 && draft.displayHz <= 256)) return "Display rate must be between 1 and 256 Hz.";
  if (![1, 5, 15, 30].includes(draft.minutes)) return "Band window must be 1, 5, 15, or 30 minutes.";
  if (![5, 10, 30].includes(draft.traceWindow)) return "EEG window must be 5, 10, or 30 seconds.";
  if (![0.5, 1, 2].includes(draft.smoothSec)) return "Smoothing must be 0.5, 1, or 2 seconds.";
  return null;
}
