// The look switch (site/sky.js): sun times per city and the Daylight / Blue Hour rule.
// Run: node --test tests/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const sky = require("../site/sky.js");
const ds = require("../site/deal-state.js");

const hm = (m) => `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
const near = (got, want, slack = 3) => assert.ok(Math.abs(got - want) <= slack, `${hm(got)} vs ${hm(want)}`);
const at = (h, m) => h * 60 + m;

// Reference times for 13 Nov 2026 (NOAA), on each city's clock: Alberta UTC-6, B.C. UTC-7, Ontario EST UTC-5.
const CASES = [
  ["Edmonton", 53.5444, -113.4909, "America/Edmonton", at(8, 56), at(17, 40)],
  ["Vancouver", 49.2827, -123.1207, "America/Vancouver", at(8, 20), at(17, 33)],
  ["Toronto", 43.6532, -79.3832, "America/Toronto", at(7, 9), at(16, 54)],
];
for (const [name, lat, lng, tz, rise, set] of CASES) {
  test(`${name}: sunrise and sunset on 13 Nov 2026`, () => {
    const sun = sky.sunTimes(lat, lng, "2026-11-13", (d) => ds.zonedNow(tz, d));
    near(sun.rise, rise);
    near(sun.set, set);
  });
}

test("Daylight until 30 minutes before sunset, then Blue Hour", () => {
  const sun = { rise: at(8, 56), set: at(17, 40) };
  assert.equal(sky.lookAt(at(8, 55), sun), "night");
  assert.equal(sky.lookAt(at(8, 56), sun), "day");
  assert.equal(sky.lookAt(at(12, 20), sun), "day");
  assert.equal(sky.lookAt(at(17, 9), sun), "day");
  assert.equal(sky.lookAt(at(17, 10), sun), "night");
  assert.equal(sky.lookAt(at(23, 40), sun), "night");
});

test("Vancouver's sunset lands on the right day (it is after midnight UTC)", () => {
  const sun = sky.sunTimes(49.2827, -123.1207, "2026-11-13", (d) => ds.zonedNow("America/Vancouver", d));
  assert.ok(sun.set > sun.rise);
});

test("summer: long days still work", () => {
  const sun = sky.sunTimes(53.5444, -113.4909, "2027-06-21", (d) => ds.zonedNow("America/Edmonton", d));
  assert.ok(sun.rise < at(6, 0) && sun.set > at(21, 30), `${hm(sun.rise)}–${hm(sun.set)}`);
});
