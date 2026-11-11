import assert from "node:assert/strict";

import { DEFAULT_VIEW, ema, glide, loadView, shouldDraw, smoothSeries, visibleCards } from "./pace.ts";

const fresh = loadView(null);
assert.deepEqual(fresh.collapsed, ["eeg", "spectrum", "heart"]);
assert.deepEqual(fresh.hidden, ["ratios", "session"]);
assert.equal(fresh.minutes, 5);
assert.equal(fresh.traceWindow, 10);
assert.equal(fresh.traceMode, "filtered");
assert.equal(fresh.smoothSec, 1);
assert.equal(fresh.calm, false);
assert.ok(visibleCards(fresh).includes("timeline"));
assert.ok(visibleCards(fresh).includes("bands"));
assert.ok(visibleCards(fresh).includes("markers"));
assert.ok(!visibleCards(fresh).includes("ratios"));

assert.deepEqual(visibleCards({ ...fresh, calm: true }), ["timeline"]);

const restored = loadView(
  JSON.stringify({ hidden: ["markers"], collapsed: ["bands", "nope"], minutes: 15, traceWindow: 30, traceMode: "raw", calm: true }),
);
assert.deepEqual(restored.hidden, ["markers"]);
assert.deepEqual(restored.collapsed, ["bands"]);
assert.equal(restored.minutes, 15);
assert.equal(restored.traceWindow, 30);
assert.equal(restored.traceMode, "raw");
assert.equal(restored.calm, true);
assert.deepEqual(loadView("{").minutes, DEFAULT_VIEW.minutes);
assert.equal(loadView(JSON.stringify({ minutes: 2, traceWindow: 2, smoothSec: 9 })).minutes, 5);
assert.equal(loadView(JSON.stringify({ smoothSec: 2 })).smoothSec, 2);

const step = ema(0, 10, 1, 1);
assert.ok(step > 6 && step < 7, `ema step ${step}`);
assert.equal(ema(undefined, 3, 1, 1), 3);

let edge = 0;
for (let i = 0; i < 8; i += 1) edge = glide(edge, 1, 0.1, 0.55);
assert.ok(edge > 0.7 && edge <= 1, `glide ${edge}`);

assert.equal(shouldDraw(100, 0, 2), false);
assert.equal(shouldDraw(500, 0, 2), true);

const blended = smoothSeries([0, 0], [10, 10], 1, 1);
assert.equal(blended.length, 2);
assert.ok(blended[0] > 6 && blended[0] < 7);

console.log("pace tests ok");
