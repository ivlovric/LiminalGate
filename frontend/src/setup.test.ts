import assert from "node:assert/strict";

import { validateSetup, type SetupDraft } from "./setup.ts";

function draft(overrides: Partial<SetupDraft> = {}): SetupDraft {
  return {
    input: "synthetic",
    oscPort: 5000,
    oscHost: "0.0.0.0",
    playbackPath: "",
    playbackSpeed: 1,
    eegScale: 1,
    eegFs: 256,
    board: "muse_s_athena",
    mac: "",
    lslName: "",
    powerline: 50,
    displayHz: 64,
    minutes: 5,
    traceWindow: 10,
    smoothSec: 1,
    ...overrides,
  };
}

assert.equal(validateSetup(draft()), null);
assert.match(validateSetup(draft({ input: "osc", oscPort: 0 })) ?? "", /port/i);
assert.match(validateSetup(draft({ input: "playback", playbackPath: "  " })) ?? "", /file/i);
assert.match(validateSetup(draft({ input: "brainflow", mac: "nope" })) ?? "", /MAC/);
assert.equal(validateSetup(draft({ input: "brainflow", mac: "aa:bb:cc:dd:ee:ff" })), null);
assert.equal(validateSetup(draft({ input: "lsl", lslName: "" })), null);
assert.match(validateSetup(draft({ powerline: 40 })) ?? "", /50 or 60/);
assert.match(validateSetup(draft({ eegScale: 0 })) ?? "", /scale/i);
assert.match(validateSetup(draft({ minutes: 2 })) ?? "", /Band window/);

console.log("setup tests ok");
