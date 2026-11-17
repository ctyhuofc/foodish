// Foodish service worker: offline shell + push notifications.
const CACHE = "foodish-v9";
const SHELL = ["./", "index.html", "styles.css", "deal-state.js", "sky.js", "app-state.js", "app.js", "config.js", "manifest.webmanifest", "icon.svg", "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

// Network-first for everything (deals change daily); fall back to cache when offline.
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  // The app's own files skip the browser's 10-minute reuse window (GitHub Pages' max-age=600) and ask
  // the server whether they changed, so an update shows on the next open instead of up to 10 min later.
  const u = new URL(req.url);
  const own = u.origin === location.origin;
  // A page address with a "?" part (/?q=tacos, /?city=vancouver) is kept under the plain page address, so
  // searches aren't saved on the phone, and the app still opens offline from any link.
  const key = own && req.mode === "navigate" && u.search ? u.origin + u.pathname : req;
  e.respondWith(
    fetch(own ? new Request(req, { cache: "no-cache" }) : req).then((res) => {
      // the app itself, and each city's deal file (kept so the last deals still show offline)
      if (res.ok && (own || (u.hostname === "raw.githubusercontent.com" && u.pathname.endsWith("/deals.json")))) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(key, copy));
      }
      return res;
    }).catch(() => caches.match(key))
  );
});

self.addEventListener("push", (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { data = { title: "Foodish", body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(data.title || "Deals near you today", {
    body: data.body || "Open Foodish to see what's on.",
    icon: "icon-192.png",
    badge: "icon-192.png",
    tag: data.tag || "foodish-daily",
    data: { url: data.url || "./" },
  }));
});

// Tapping a notification opens a page of this site only. Any other address (or nonsense) opens the app's start page.
function ownPage(url) {
  const start = new URL("./", location.href).href;
  try {
    const u = new URL(url || "./", location.href);
    return u.origin === location.origin ? u.href : start;
  } catch { return start; }
}
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = ownPage(e.notification.data && e.notification.data.url);
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    for (const w of wins) if ("focus" in w) { w.navigate(url); return w.focus(); }
    return self.clients.openWindow(url);
  }));
});
