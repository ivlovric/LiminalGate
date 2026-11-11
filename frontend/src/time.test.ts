import assert from "node:assert/strict";

import { formatSessionWhen } from "./time.ts";

assert.equal(formatSessionWhen("2026-11-11T14:27:24+00:00", "Europe/Zagreb"), "11.11.2026 15:27");
assert.equal(formatSessionWhen("2026-11-11 14:27:24+00:00", "Europe/Zagreb"), "11.11.2026 15:27");
assert.equal(formatSessionWhen("2026-07-01T12:00:00Z", "Europe/Zagreb"), "01.07.2026 14:00");
assert.equal(formatSessionWhen(""), "");
assert.equal(formatSessionWhen("not a time"), "not a time");

console.log("time tests ok");
