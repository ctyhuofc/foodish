// Browser side of the shared deal-timing rules: must pass every case in spec/deal-states.json.
// Run: node --test tests/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const ds = require("../site/deal-state.js");
const spec = JSON.parse(readFileSync(new URL("../spec/deal-states.json", import.meta.url), "utf8"));

for (const c of spec.cases) {
  test(c.name, () => assert.equal(ds.dealState(c.deal, ds.momentFromLocal(c.at)), c.expect));
}

test("spec lists exactly the states the code returns", () => {
  assert.deepEqual(new Set(Object.keys(spec.states)), new Set(Object.values(ds.STATES)));
});

// Alberta stays on UTC-6 year-round from 1 Nov 2026; these must hold even with a device's outdated tz data.
const at = (iso) => ds.zonedNow("America/Edmonton", new Date(iso));
test("Edmonton clock: November evening is UTC-6", () => assert.deepEqual(at("2026-11-11T23:30:00Z"), { date: "2026-11-11", day: 3, mins: 1050 }));
test("Edmonton clock: just after UTC midnight is still yesterday", () => assert.deepEqual(at("2026-11-12T03:00:00Z"), { date: "2026-11-11", day: 3, mins: 1260 }));
test("Edmonton clock: midnight reads as 0, not 24", () => assert.equal(at("2026-11-12T06:00:00Z").mins, 0));
test("Edmonton clock: January 2027 stays UTC-6", () => assert.equal(at("2027-01-15T18:00:00Z").mins, 720));
test("Edmonton clock: summer is UTC-6", () => assert.equal(at("2026-07-01T18:00:00Z").mins, 720));
test("Edmonton clock: old winters were UTC-7", () => assert.equal(at("2026-01-15T19:00:00Z").mins, 720));
