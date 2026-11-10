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

// Every city in cities.json keeps its fixed UTC offset all year, whatever the device's tz data says.
const cities = JSON.parse(readFileSync(new URL("../cities.json", import.meta.url), "utf8")).cities;
for (const c of cities) {
  test(`${c.name} clock: UTC${c.utc_offset_hours} in January and July 2027`, () => {
    const noon = (12 + c.utc_offset_hours + 24) % 24 * 60;
    assert.equal(ds.zonedNow(c.timezone, new Date("2027-01-15T12:00:00Z")).mins, noon);
    assert.equal(ds.zonedNow(c.timezone, new Date("2027-07-15T12:00:00Z")).mins, noon);
  });
}
test("Vancouver clock: an hour behind Edmonton", () => {
  const at = new Date("2026-11-11T21:30:00Z");
  assert.equal(ds.zonedNow("America/Edmonton", at).mins, 930);
  assert.equal(ds.zonedNow("America/Vancouver", at).mins, 870);
});
test("Vancouver clock: old winters were UTC-8", () => assert.equal(ds.zonedNow("America/Vancouver", new Date("2026-01-15T20:00:00Z")).mins, 720));
test("same moment, same happy hour: on in Edmonton, not yet in Vancouver", () => {
  const deal = { start: "15:00", end: "18:00" }, at = new Date("2026-11-11T21:30:00Z");
  assert.equal(ds.dealState(deal, ds.zonedNow("America/Edmonton", at)), "on_now");
  assert.equal(ds.dealState(deal, ds.zonedNow("America/Vancouver", at)), "later");
});
