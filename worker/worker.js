// Foodish API — a tiny Cloudflare Worker (free plan) that stores:
//   * alert sign-ups (push subscriptions + rough location)  -> KV namespace SUBS
//   * "share a deal" submissions                             -> KV namespace SUBMISSIONS
// Secrets: ADMIN_TOKEN (wrangler secret put ADMIN_TOKEN)

// Generous boxes around each city Foodish covers (see cities.json), with the city centre as fallback.
// When you add a city to cities.json, add it here too (tests/test_cities.py checks).
const CITY_AREAS = [
  { id: "edmonton", lat: 53.544, lng: -113.491, south: 53, north: 54, west: -114.2, east: -113 },
  { id: "vancouver", lat: 49.283, lng: -123.121, south: 48.9, north: 49.6, west: -123.6, east: -122.4 },
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

async function sha(s) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("").slice(0, 32);
}
async function body(req) {
  const t = await req.text();
  if (t.length > 4000) throw new Error("too large");
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
      if (req.method === "POST" && url.pathname === "/subscribe") {
        const b = await body(req);
        const sub = b.subscription;
        if (!sub?.endpoint?.startsWith("https://") || !sub.keys?.p256dh || !sub.keys?.auth) return json(req, { error: "bad subscription" }, 400);
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
      if (req.method === "POST" && url.pathname === "/submit") {
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
