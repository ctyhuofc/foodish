// The alerts service (worker/worker.js): which sign-ups it stores, the body size limit, and the routes that stay closed.
// Runs the worker's fetch() with a pretend Cloudflare storage; nothing goes over the network.
// Run: node --test tests/*.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../worker/worker.js";

// Pretend KV storage: just the four calls the worker uses.
function kv() {
  const m = new Map();
  return {
    m,
    async put(k, v) { m.set(k, v); },
    async get(k, type) { const v = m.get(k); return v == null ? null : type === "json" ? JSON.parse(v) : v; },
    async delete(k) { m.delete(k); },
    async list() { return { keys: [...m.keys()].map((name) => ({ name })), list_complete: true }; },
  };
}
const fakeEnv = (extra = {}) => ({ SUBS: kv(), SUBMISSIONS: kv(), ADMIN_TOKEN: "test-admin-token", ...extra });

const API = "https://foodish-api.example.workers.dev";
const post = (path, body, headers = {}) =>
  new Request(API + path, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });

const b64url = (bytes) => Buffer.from(bytes).toString("base64url");
// Made-up browser keys of the real shape: a fresh P-256 public key (65 bytes, starts with 4) and 16 random bytes.
async function fakeKeys() {
  const k = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const raw = new Uint8Array(await crypto.subtle.exportKey("raw", k.publicKey));
  return { p256dh: b64url(raw), auth: b64url(crypto.getRandomValues(new Uint8Array(16))) };
}
async function signUp(env, endpoint, keys) {
  const res = await worker.fetch(post("/subscribe", { subscription: { endpoint, keys: keys || (await fakeKeys()) }, lat: 53.54, lng: -113.49 }), env);
  return { status: res.status, body: await res.json() };
}

test("sign-ups from Chrome, Firefox, Edge and Safari are stored as sent", async () => {
  const endpoints = [
    "https://fcm.googleapis.com/fcm/send/dQw4w9WgXcQ:APA91bHPRgkF3JUikC4ENAHEeMrd41Zxv3hVZjC9KtT8OvPVGJ",
    "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABmZ2x0ZXN0aW5nLW5vdC1hLXJlYWwtdG9rZW4",
    "https://wns2-par02p.notify.windows.com/w/?token=BQYAAABmYWtlLXRva2VuLWZvci10ZXN0cw%3d%3d",
    "https://web.push.apple.com/QGuQyavXutnMH3Oxe4kPZ8Ez6ZYB9vAWGfpL1ZsQ8mDd",
  ];
  const env = fakeEnv();
  for (const e of endpoints) {
    const keys = await fakeKeys();
    assert.deepEqual(await signUp(env, e, keys), { status: 200, body: { ok: true } }, e);
    const stored = [...env.SUBS.m.values()].map((v) => JSON.parse(v).subscription).find((s) => s.endpoint === e);
    assert.deepEqual(stored, { endpoint: e, keys });
  }
  assert.equal(env.SUBS.m.size, 4);
});

test("keys with padding or in plain base64 are accepted too", async () => {
  const k = await fakeKeys();
  const std = { p256dh: Buffer.from(k.p256dh, "base64url").toString("base64"), auth: Buffer.from(k.auth, "base64url").toString("base64") };
  const padded = { p256dh: k.p256dh + "=", auth: k.auth + "==" };
  for (const keys of [std, padded]) assert.equal((await signUp(fakeEnv(), "https://fcm.googleapis.com/fcm/send/abc", keys)).status, 200);
});

test("anything that isn't a real browser sign-up is refused and nothing is stored", async () => {
  const good = await fakeKeys();
  const offCurve = Buffer.from(good.p256dh, "base64url"); offCurve[40] ^= 1;
  const cases = [
    ["http://fcm.googleapis.com/fcm/send/abc", good],
    ["https://attacker.example/push", good],
    ["https://fcm.googleapis.com.evil.ca/fcm/send/abc", good],
    ["https://evilfcm.googleapis.com/fcm/send/abc", good],
    ["https://push.apple.com.evil.ca/x", good],
    ["https://user:pw@fcm.googleapis.com/fcm/send/abc", good],
    ["https://fcm.googleapis.com\\@evil.ca/fcm/send/abc", good],
    ["https://fcm.googleapis.com:8443/fcm/send/abc", good],
    ["https://10.0.0.1/push", good],
    ["https://fcm.googleapis.com/" + "a".repeat(1000), good],
    [42, good],
    ["https://fcm.googleapis.com/fcm/send/abc", { p256dh: "AAAA", auth: good.auth }], // too short
    ["https://fcm.googleapis.com/fcm/send/abc", { p256dh: good.p256dh + "AAAA", auth: good.auth }], // too long
    ["https://fcm.googleapis.com/fcm/send/abc", { p256dh: good.p256dh, auth: b64url(new Uint8Array(17)) }],
    ["https://fcm.googleapis.com/fcm/send/abc", { p256dh: "not base64 at all!!", auth: good.auth }],
    ["https://fcm.googleapis.com/fcm/send/abc", { p256dh: b64url(offCurve), auth: good.auth }], // not a real key
    ["https://fcm.googleapis.com/fcm/send/abc", { p256dh: good.p256dh }],
    ["https://fcm.googleapis.com/fcm/send/abc", { p256dh: 7, auth: good.auth }],
  ];
  for (const [endpoint, keys] of cases) {
    const env = fakeEnv();
    assert.deepEqual(await signUp(env, endpoint, keys), { status: 400, body: { error: "bad subscription" } }, String(endpoint));
    assert.equal(env.SUBS.m.size, 0);
  }
});

test("a body that says it's too big is refused before it's read", async () => {
  const env = fakeEnv();
  let read = false;
  const req = {
    method: "POST", url: API + "/subscribe", headers: new Headers({ "content-length": "5000000" }),
    text: async () => { read = true; return "{}"; },
  };
  assert.equal((await worker.fetch(req, env)).status, 400);
  assert.equal(read, false, "the body should not be read");
  // ...and a big body without the header is still refused after reading
  const big = { subscription: { endpoint: "https://fcm.googleapis.com/x", keys: await fakeKeys() }, pad: "x".repeat(5000) };
  assert.equal((await worker.fetch(post("/subscribe", big), env)).status, 400);
  assert.equal(env.SUBS.m.size, 0);
});

test("deal tips by API (/submit) are closed unless TIPS_ENABLED is 1", async () => {
  const tip = { restaurant: "Wing Shack", deal: "Wings 50 cents Wednesday" };
  const off = fakeEnv();
  assert.equal((await worker.fetch(post("/submit", tip), off)).status, 404);
  assert.equal(off.SUBMISSIONS.m.size, 0);
  const on = fakeEnv({ TIPS_ENABLED: "1" });
  assert.equal((await worker.fetch(post("/submit", tip), on)).status, 200);
  assert.equal(on.SUBMISSIONS.m.size, 1);
});

test("the owner's lists need the admin token", async () => {
  const env = fakeEnv();
  await signUp(env, "https://fcm.googleapis.com/fcm/send/abc");
  for (const path of ["/subscriptions", "/submissions"]) {
    for (const headers of [{}, { authorization: "Bearer wrong" }]) {
      assert.equal((await worker.fetch(new Request(API + path, { headers }), env)).status, 404, path);
    }
    const ok = await worker.fetch(new Request(API + path, { headers: { authorization: "Bearer test-admin-token" } }), env);
    assert.equal(ok.status, 200, path);
  }
  // no token configured at all: still closed
  const bare = fakeEnv({ ADMIN_TOKEN: undefined });
  assert.equal((await worker.fetch(new Request(API + "/subscriptions", { headers: { authorization: "Bearer undefined" } }), bare)).status, 404);
});

test("unsubscribe removes a stored sign-up", async () => {
  const env = fakeEnv();
  const e = "https://fcm.googleapis.com/fcm/send/abc";
  await signUp(env, e);
  assert.equal(env.SUBS.m.size, 1);
  await worker.fetch(post("/unsubscribe", { endpoint: e }), env);
  assert.equal(env.SUBS.m.size, 0);
});
