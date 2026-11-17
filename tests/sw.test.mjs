// The service worker (site/sw.js): which pages a notification may open, and what is kept for offline use.
// Runs sw.js with a pretend browser (self, caches, clients, fetch); nothing goes over the network.
// Run: node --test tests/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SW = readFileSync(new URL("../site/sw.js", import.meta.url), "utf8");
const ORIGIN = "https://www.foodish.ca";

function load({ online = true } = {}) {
  const handlers = {}, store = new Map(), opened = [];
  const cache = { async put(k, res) { store.set(typeof k === "string" ? k : k.url, res); }, async addAll() {} };
  const self = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    registration: { showNotification: async () => {} },
    clients: {
      wins: [],
      async matchAll() { return this.wins; },
      async openWindow(u) { opened.push(u); },
      async claim() {},
    },
  };
  const ctx = {
    self, URL, Promise,
    location: new URL(ORIGIN + "/sw.js"),
    Request: class { constructor(r, init) { Object.assign(this, r, init); } },
    caches: {
      async open() { return cache; },
      async match(k) { return store.get(typeof k === "string" ? k : k.url); },
      async keys() { return []; },
    },
    fetch: async (req) => { if (!online) throw new TypeError("offline"); return { ok: true, url: req.url, clone() { return { copy: req.url }; } }; },
  };
  vm.runInNewContext(SW, ctx);
  return { handlers, store, opened, self };
}
// Fire a fetch event and wait for its answer (and for the cache write that follows it).
async function get(sw, url, mode = "no-cors") {
  let answer;
  sw.handlers.fetch({ request: { method: "GET", url, mode }, respondWith: (p) => { answer = p; } });
  const res = await answer;
  await new Promise((r) => setTimeout(r, 0));
  return res;
}
async function tap(sw, data) {
  let done;
  sw.handlers.notificationclick({ notification: { close() {}, data }, waitUntil: (p) => { done = p; } });
  await done;
}

test("a notification opens only this site's pages", async () => {
  const cases = [
    [{ url: "./" }, ORIGIN + "/"],
    [{ url: "/?city=vancouver" }, ORIGIN + "/?city=vancouver"],
    [{ url: ORIGIN + "/?view=map" }, ORIGIN + "/?view=map"],
    [{ url: "https://evil.example/phish" }, ORIGIN + "/"],
    [{ url: "//evil.example/x" }, ORIGIN + "/"],
    [{ url: "javascript:alert(1)" }, ORIGIN + "/"],
    [{ url: "http://www.foodish.ca/" }, ORIGIN + "/"],   // not https: another origin
    [{}, ORIGIN + "/"],
    [null, ORIGIN + "/"],
  ];
  for (const [data, want] of cases) {
    const sw = load();
    await tap(sw, data);
    assert.deepEqual(sw.opened, [want], JSON.stringify(data));
  }
});

test("an open Foodish window is reused, with the same check", async () => {
  const sw = load(), went = [];
  sw.self.clients.wins = [{ focus: async () => {}, navigate: (u) => went.push(u) }];
  await tap(sw, { url: "https://evil.example/" });
  assert.deepEqual(went, [ORIGIN + "/"]);
  assert.deepEqual(sw.opened, []);
});

test("a search in the address is not kept: pages are cached under their plain address", async () => {
  const sw = load();
  await get(sw, ORIGIN + "/?q=tacos&city=vancouver", "navigate");
  await get(sw, ORIGIN + "/app.js");
  await get(sw, "https://raw.githubusercontent.com/x/foodish/main/data/deals.json", "cors");
  await get(sw, "https://tile.openstreetmap.org/1/2/3.png");
  assert.deepEqual([...sw.store.keys()].sort(), [ORIGIN + "/", ORIGIN + "/app.js", "https://raw.githubusercontent.com/x/foodish/main/data/deals.json"].sort());
  for (const k of sw.store.keys()) assert.ok(!k.includes("tacos"), k);
});

test("offline, a link with a search still opens the app", async () => {
  const sw = load({ online: false });
  sw.store.set(ORIGIN + "/", { cached: "app" });
  assert.deepEqual(await get(sw, ORIGIN + "/?q=wings", "navigate"), { cached: "app" });
  assert.deepEqual(await get(sw, ORIGIN + "/", "navigate"), { cached: "app" });
});

test("SHELL lists the start page, and CACHE has a version", () => {
  assert.match(SW, /const CACHE = "foodish-v\d+";/);
  assert.match(SW, /const SHELL = \["\.\/",/);
});
