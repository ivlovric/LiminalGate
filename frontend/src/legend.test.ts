import assert from "node:assert/strict";

import { TIMELINE_BANDS, formatHz, legendItems } from "./legend.ts";

assert.equal(formatHz(4), "4");
assert.equal(formatHz(5.5), "5.5");
assert.equal(formatHz(13), "13");

const solid = legendItems(TIMELINE_BANDS, []);
assert.deepEqual(
  solid.map((item) => item.label),
  ["delta 1–4", "theta 4–8", "alpha 8–12", "beta 13–30", "gamma 30–45"],
);
assert.ok(solid.every((item) => !item.dashed));
assert.equal(solid[1]?.color, "#7aa2ff");

const withHeadset = legendItems(TIMELINE_BANDS, ["theta", "alpha"]);
assert.deepEqual(
  withHeadset.map((item) => `${item.dashed ? "dash" : "solid"} ${item.label}`),
  [
    "solid delta 1–4",
    "solid theta 4–8",
    "dash theta 4–8 headset",
    "solid alpha 8–12",
    "dash alpha 8–12 headset",
    "solid beta 13–30",
    "solid gamma 30–45",
  ],
);
assert.equal(withHeadset.find((item) => item.series === "theta device")?.color, "#7aa2ff");
