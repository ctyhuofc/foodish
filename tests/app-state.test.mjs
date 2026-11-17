// The app's page-free rules (site/app-state.js): the address bar, the status line, alerts, the chosen day.
// Run: node --test tests/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const as = require("../site/app-state.js");

const RULES = { cityIds: ["edmonton", "vancouver", "toronto"], radii: [1, 2, 5, 10, 999], today: 0 };
const BLANK = { city: null, cat: null, day: null, onNow: false, view: "list", radius: null, q: "", look: null };

// [address, what it means] with today = Sunday (0)
const LINKS = [
  ["", {}],
  ["?city=vancouver", { city: "vancouver" }],
  ["?city=Vancouver%20", { city: "vancouver" }],
  ["?city=nowhere", {}],
  ["?cat=Wings", { cat: "wings" }],
  ["?day=3", { day: 3 }],
  ["?day=0", {}],                        // today is the live view, not a schedule
  ["?day=9", {}],
  ["?day=-1", {}],
  ["?day=tuesday", {}],
  ["?now=1", { onNow: true }],
  ["?now=yes", {}],
  ["?view=map", { view: "map" }],
  ["?view=satellite", {}],
  ["?km=2", { radius: 2 }],
  ["?km=999", { radius: 999 }],
  ["?km=3", {}],
  ["?q=tacos", { q: "tacos" }],
  ["?look=day", { look: "day" }],
  ["?look=night", { look: "night" }],
  ["?look=pink", {}],
  ["?city=toronto&cat=pizza&day=5&now=1&view=map&km=1&q=slice&look=day",
    { city: "toronto", cat: "pizza", day: 5, onNow: true, view: "map", radius: 1, q: "slice", look: "day" }],
];
for (const [search, want] of LINKS) {
  test(`parseLink ${search || "(empty)"}`, () => {
    assert.deepEqual(as.parseLink(search, RULES), { ...BLANK, ...want });
  });
}

test("parseLink without today keeps the day as written", () => {
  assert.equal(as.parseLink("?day=0", { ...RULES, today: undefined }).day, 0);
});

const OPTS = { defaultCity: "edmonton", savedRadius: 5 };
const base = { city: "edmonton", cityLocked: false, cat: null, day: null, onNow: false, view: "list", radius: 5, q: "", look: null };
// [filters, address]
const BUILDS = [
  [{}, ""],
  [{ cityLocked: true }, "city=edmonton"],             // picked on purpose: kept in a shared link
  [{ city: "vancouver" }, "city=vancouver"],
  [{ cat: "wings" }, "cat=wings"],
  [{ day: 3 }, "day=3"],
  [{ day: 0 }, "day=0"],
  [{ onNow: true }, "now=1"],
  [{ view: "map" }, "view=map"],
  [{ radius: 2 }, "km=2"],
  [{ radius: 5 }, ""],                                  // the saved distance isn't repeated
  [{ q: "  tacos " }, "q=tacos"],
  [{ q: "   " }, ""],
  [{ look: "night" }, "look=night"],
  [{ city: "toronto", cat: "happy hour", day: 4, onNow: true, view: "map", radius: 10, q: "beer", look: "day" },
    "city=toronto&cat=happy+hour&day=4&now=1&view=map&km=10&q=beer&look=day"],
];
for (const [f, want] of BUILDS) {
  test(`buildLink ${JSON.stringify(f)}`, () => assert.equal(as.buildLink({ ...base, ...f }, OPTS), want));
}

test("buildLink → parseLink round trip", () => {
  const f = { ...base, city: "vancouver", cat: "happy hour", day: 2, onNow: true, view: "map", radius: 1, q: "beer", look: "night" };
  const back = as.parseLink("?" + as.buildLink(f, OPTS), RULES);
  assert.deepEqual(back, { city: "vancouver", cat: "happy hour", day: 2, onNow: true, view: "map", radius: 1, q: "beer", look: "night" });
});

test("historyStep is view|day|category", () => {
  assert.equal(as.historyStep({ view: "list", day: null, cat: null }), "list|null|null");
  assert.equal(as.historyStep({ view: "map", day: 3, cat: "wings" }), "map|3|wings");
});

// [inputs, kind, retry]
const STATUS = [
  [{ load: "loading", hasData: false }, "loading", false],
  [{ load: "loading", hasData: false, locNote: "Location is off" }, "loading", false],
  [{ load: "error", hasData: false, errorDetail: "error 500" }, "error", true],
  [{ load: "error", hasData: false, errorDetail: "error 500", locNote: "Location is off", stale: true }, "error", true],
  [{ load: "error", hasData: true, errorDetail: "error 500" }, "none", false],      // a failed refresh keeps the deals shown
  [{ load: "ready", hasData: true, locNote: "Location is off", stale: true }, "note", false],
  [{ load: "ready", hasData: true, stale: true }, "stale", false],
  [{ load: "loading", hasData: true, stale: true }, "stale", false],
  [{ load: "ready", hasData: true }, "none", false],
];
for (const [input, kind, retry] of STATUS) {
  test(`statusLine ${JSON.stringify(input)} → ${kind}`, () => {
    const s = as.statusLine(input);
    assert.equal(s.kind, kind);
    assert.equal(s.retry, retry);
  });
}
test("statusLine wording", () => {
  assert.equal(as.statusLine({ load: "error", hasData: false, errorDetail: "you're offline" }).text, "Couldn't load deals right now (you're offline).");
  assert.equal(as.statusLine({ load: "loading", hasData: false }).text, "Loading today's deals…");
  assert.equal(as.statusLine({ load: "ready", hasData: true, stale: true }).text, "These deals were last checked over a day ago.");
  assert.equal(as.statusLine({ load: "ready", hasData: true, locNote: "Showing Downtown Toronto." }).text, "Showing Downtown Toronto.");
  assert.equal(as.statusLine({ load: "ready", hasData: true }).text, "");
});

test("alertsState: on only when wanted and allowed", () => {
  assert.equal(as.alertsState({ wanted: false, permission: "granted" }), "off");
  assert.equal(as.alertsState({ wanted: false, permission: "denied" }), "off");
  assert.equal(as.alertsState({ wanted: true, permission: "granted" }), "on");
  assert.equal(as.alertsState({ wanted: true, permission: "denied" }), "blocked");
  assert.equal(as.alertsState({ wanted: true, permission: "default" }), "blocked");
  assert.equal(as.alertsState({ wanted: true, permission: "unsupported" }), "blocked");
});

test("alertsMessage: the blocked note comes and goes; other messages stay", () => {
  const other = "Couldn't set up daily alerts right now. Please try again later.";
  assert.equal(as.alertsMessage("blocked", ""), as.BLOCKED_TEXT);
  assert.equal(as.alertsMessage("blocked", other), other);
  assert.equal(as.alertsMessage("on", as.BLOCKED_TEXT), "");
  assert.equal(as.alertsMessage("off", as.BLOCKED_TEXT), "");
  assert.equal(as.alertsMessage("on", other), other);
  assert.equal(as.alertsMessage("off", ""), "");
});

test("normalizeDay: today is the live view", () => {
  assert.equal(as.normalizeDay(2, 2), null);   // left open past midnight: Tuesday is now today
  assert.equal(as.normalizeDay(2, 1), 2);
  assert.equal(as.normalizeDay(null, 1), null);
  assert.equal(as.normalizeDay(0, 6), 0);
  assert.equal(as.normalizeDay(7, 1), null);
});

test("submit form: Send is off only while sending", () => {
  assert.deepEqual(as.submitView(as.SUBMIT.SENDING), { disabled: true, text: "Sending…" });
  assert.deepEqual(as.submitView(as.SUBMIT.SENT), { disabled: false, text: "Thanks! We'll check it and add it soon." });
  assert.deepEqual(as.submitView(as.SUBMIT.ERROR), { disabled: false, text: "Couldn't send that. Please try again." });
  assert.deepEqual(as.submitView(as.SUBMIT.EMAIL), { disabled: false, text: "Your email app should open with the deal filled in." });
  assert.deepEqual(as.submitView(as.SUBMIT.IDLE), { disabled: false, text: "" });
});

// Turning daily alerts off must really delete the sign-up kept by the alerts service. The browser's address is
// remembered first ("queued"), and forgotten only once the service has said it's deleted, or the same address
// is signed up again (then the service's copy is the current one and must not be deleted later).
test("alerts off: the sign-up waits to be deleted until the service says it's done", () => {
  const A = "https://fcm.googleapis.com/fcm/send/aaa", B = "https://updates.push.services.mozilla.com/wpush/v2/bbb";
  const next = as.nextUnsubscribes;
  assert.deepEqual(next([], { endpoint: A, result: "queued" }), [A]);
  assert.deepEqual(next([A], { endpoint: A, result: "queued" }), [A]);            // never twice
  assert.deepEqual(next([A], { endpoint: B, result: "queued" }), [A, B]);         // an older one still waits
  assert.deepEqual(next([A, B], { endpoint: A, result: "deleted" }), [B]);
  assert.deepEqual(next([A], { endpoint: A, result: "failed" }), [A]);            // try again on the next open
  assert.deepEqual(next([A], { endpoint: A, result: "signed_up" }), []);          // on again with the same address
  assert.deepEqual(next([A], { endpoint: B, result: "signed_up" }), [A]);
  assert.deepEqual(next(null, { endpoint: A, result: "deleted" }), []);
  // the list is small, and only holds real https addresses (it is read back from storage)
  const many = ["1", "2", "3", "4", "5", "6"].map((n) => `https://push.example/${n}`);
  assert.deepEqual(many.reduce((l, e) => next(l, { endpoint: e, result: "queued" }), []), many.slice(-5));
  assert.deepEqual(next(["javascript:x", 42, "http://plain.example/x", A], { endpoint: B, result: "failed" }), [A]);
  assert.deepEqual(next("nonsense", { endpoint: "not a url", result: "queued" }), []);
});

// Visits are counted by path only: what comes after the "?" (a search like ?q=tacos) is never sent.
test("visitData sends the path, never the address's search part", () => {
  const sent = as.visitData({ p: "/?q=tacos&city=vancouver", q: "?q=tacos&city=vancouver", t: "Foodish", r: "", s: [390] }, "/");
  assert.deepEqual(sent, { p: "/", t: "Foodish", r: "", s: [390] });
  assert.equal(as.visitData({ p: "/x?q=1" }, "").p, "/");
  assert.equal(as.visitData(null, "/privacy/").p, "/privacy/");
});

// A deal's link only ever opens a plain web page (FIX: a website could talk the AI into a "javascript:" link).
test("safeUrl keeps http(s) links and drops everything else", () => {
  assert.equal(as.safeUrl("https://wingplace.ca/specials"), "https://wingplace.ca/specials");
  assert.equal(as.safeUrl("http://wingplace.ca/"), "http://wingplace.ca/");
  for (const bad of ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,<b>x</b>", "vbscript:x",
    "//evil.example", "/specials", "not a url", "", null, undefined, 42, { href: "https://x.ca" }]) {
    assert.equal(as.safeUrl(bad), "", String(bad));
  }
});
