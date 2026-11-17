// Foodish API — a tiny Cloudflare Worker (free plan) that stores:
//   * alert sign-ups (push subscriptions + rough location)  -> KV namespace SUBS
//   * "share a deal" submissions                             -> KV namespace SUBMISSIONS
// Secrets: ADMIN_TOKEN (wrangler secret put ADMIN_TOKEN)
// Optional: TIPS_ENABLED = "1" opens /submit (deal tips by API; README step 6). Without it /submit answers 404.

// Generous boxes around each city Foodish covers (see cities.json), with the city centre as fallback.
// When you add a city to cities.json, add it here too (tests/test_cities.py checks).
const CITY_AREAS = [
  { id: "edmonton", lat: 53.544, lng: -113.491, south: 53, north: 54, west: -114.2, east: -113 },
  { id: "vancouver", lat: 49.283, lng: -123.121, south: 48.9, north: 49.6, west: -123.6, east: -122.4 },
  { id: "toronto", lat: 43.653, lng: -79.383, south: 43.3, north: 44.2, west: -80.0, east: -78.8 },
];

const ALLOWED_ORIGINS = ["https://www.foodish.ca", "https://foodish.ca", "http://localhost:8000"];

function cors(req) {
  const o = req.headers.get("origin") || "";
  return {
    "access-control-allow-origin": ALLOWED_ORIGINS.includes(o) ? o : ALLOWED_ORIGINS[0],
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,authorization",
    vary: "origin",
  };
}
const json = (req, body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...cors(req) } });

// The push services real browsers use. A sign-up must point at one of these, so nobody can make the daily
// alert job call their own server (or a server that never answers). Keep in step with PUSH_HOSTS in pipeline/notify.py.
//   fcm.googleapis.com         Chrome, Android, Samsung Internet, Opera, Brave and other Chromium browsers
//   *.push.services.mozilla.com  Firefox (desktop and Android): updates.push.services.mozilla.com
//   *.notify.windows.com       Microsoft Edge on Windows (e.g. wns2-par02p.notify.windows.com)
//   *.push.apple.com           Safari on Mac, and iPhone/iPad Home Screen apps: web.push.apple.com
const PUSH_HOSTS = ["fcm.googleapis.com"];
const PUSH_HOST_ENDINGS = [".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com"];

// A browser key as bytes (base64url, or plain base64; padding optional), or null if it isn't one.
function keyBytes(s) {
  if (typeof s !== "string" || s.length > 200 || !/^[A-Za-z0-9_+/-]+={0,2}$/.test(s)) return null;
  const t = s.replace(/=+$/, "").replace(/-/g, "+").replace(/_/g, "/");
  try { return Uint8Array.from(atob(t + "===".slice((t.length + 3) % 4)), (c) => c.charCodeAt(0)); } catch { return null; }
}
// Is this a real browser push sign-up? Only looks at the data: no storage, no network.
async function goodSubscription(sub) {
  const e = sub?.endpoint;
  if (typeof e !== "string" || e.length > 1000 || !/^[\x21-\x7e]+$/.test(e)) return false; // no spaces or odd characters
  let u;
  try { u = new URL(e); } catch { return false; }
  const h = u.hostname;
  if (u.protocol !== "https:" || u.username || u.password || u.port) return false;
  if (!PUSH_HOSTS.includes(h) && !PUSH_HOST_ENDINGS.some((end) => h.endsWith(end))) return false;
  if (!e.startsWith(`https://${h}/`)) return false; // the address as written must name that same host (no backslash or "@" tricks)
  const p256dh = keyBytes(sub.keys?.p256dh), auth = keyBytes(sub.keys?.auth);
  if (!(p256dh && p256dh.length === 65 && p256dh[0] === 4 && auth && auth.length === 16)) return false;
  // The browser's key must also be a real point on the P-256 curve, or the alert can't be encrypted for it.
  try { await crypto.subtle.importKey("raw", p256dh, { name: "ECDH", namedCurve: "P-256" }, false, []); return true; } catch { return false; }
}

async function sha(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("").slice(0, 32);
}
const MAX_BODY = 4000;
async function body(req) {
  // Refuse a big body before reading it; check again after, as the header can be missing or wrong.
  if (+req.headers.get("content-length") > MAX_BODY) throw new Error("too large");
  const t = await req.text();
  if (t.length > MAX_BODY) throw new Error("too large");
  return JSON.parse(t);
}
async function listAll(ns) {
  const out = [];
  let cursor;
  do {
    const page = await ns.list({ cursor });
    for (const k of page.keys) { const v = await ns.get(k.name, "json"); if (v) out.push(v); }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  return out;
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") return new Response(null, { headers: cors(req) });
    // Without ADMIN_TOKEN set, "Bearer undefined" would otherwise match: admin stays off until it's configured.
    const admin = Boolean(env.ADMIN_TOKEN) && req.headers.get("authorization") === `Bearer ${env.ADMIN_TOKEN}`;

    try {
      // Sign-ups need no login, by design (anyone can turn on alerts). Limiting sign-ups per IP address would be a possible later addition.
      if (req.method === "POST" && url.pathname === "/subscribe") {
        const b = await body(req);
        const sub = b.subscription;
        if (!(await goodSubscription(sub))) return json(req, { error: "bad subscription" }, 400);
        const rec = {
          subscription: { endpoint: sub.endpoint, keys: { p256dh: sub.keys.p256dh, auth: sub.keys.auth } },
          lat: Math.round(+b.lat * 1000) / 1000, lng: Math.round(+b.lng * 1000) / 1000,
          radius_km: Math.min(Math.max(+b.radius_km || 5, 1), 25),
          categories: (Array.isArray(b.categories) ? b.categories : []).slice(0, 8).map((c) => String(c).slice(0, 30)),
          updated: new Date().toISOString(),
        };
        // A subscriber must be in or near a city Foodish covers; anything else falls back to the nearest
        // city's centre, so a bad or far-away location can't sign up for alerts it will never get.
        const home = CITY_AREAS.find((c) => rec.lat > c.south && rec.lat < c.north && rec.lng > c.west && rec.lng < c.east);
        if (!home) {
          const nearest = CITY_AREAS.map((c) => ({ c, d: Math.hypot(rec.lat - c.lat, rec.lng - c.lng) }))
            .filter((x) => Number.isFinite(x.d)).sort((a, b) => a.d - b.d)[0];
          const c = nearest ? nearest.c : CITY_AREAS[0];
          rec.lat = c.lat; rec.lng = c.lng;
        }
        await env.SUBS.put(await sha(sub.endpoint), JSON.stringify(rec));
        return json(req, { ok: true });
      }
      if (req.method === "POST" && url.pathname === "/unsubscribe") {
        const b = await body(req);
        if (b.endpoint) await env.SUBS.delete(await sha(b.endpoint));
        return json(req, { ok: true });
      }
      // Deal tips by API are off unless TIPS_ENABLED is "1" (the app sends tips by email: TIPS in site/config.js).
      if (req.method === "POST" && url.pathname === "/submit" && env.TIPS_ENABLED === "1") {
        const b = await body(req);
        const rec = {
          restaurant: String(b.restaurant || "").slice(0, 120), deal: String(b.deal || "").slice(0, 300),
          url: String(b.url || "").slice(0, 300), at: new Date().toISOString(),
        };
        if (!rec.restaurant || !rec.deal) return json(req, { error: "missing fields" }, 400);
        await env.SUBMISSIONS.put(`${rec.at}-${crypto.randomUUID().slice(0, 6)}`, JSON.stringify(rec), { expirationTtl: 60 * 60 * 24 * 60 });
        return json(req, { ok: true });
      }
      if (req.method === "GET" && url.pathname === "/subscriptions" && admin) return json(req, await listAll(env.SUBS));
      if (req.method === "GET" && url.pathname === "/submissions" && admin) return json(req, await listAll(env.SUBMISSIONS));
      return json(req, { error: "not found" }, 404);
    } catch (e) {
      return json(req, { error: "bad request" }, 400);
    }
  },
};
