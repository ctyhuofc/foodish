/* Foodish — front-end app. No build step: plain JS, works on any static host. */
(() => {
  "use strict";
  const CFG = window.FOODISH_CONFIG;
  const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const DAY_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const $ = (id) => document.getElementById(id);

  // ---------- safe localStorage ----------
  const store = {
    get(k, d) { try { const v = localStorage.getItem("foodish:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("foodish:" + k, JSON.stringify(v)); } catch {} },
  };

  // ---------- state ----------
  const state = {
    data: null,
    byId: new Map(),
    me: null,               // {lat, lng, label}
    day: null,              // 0-6, null = today
    onNow: false,
    radius: store.get("radius", 5),
    cat: null,
    q: "",
    view: "list",
    map: null, layer: null, meMarker: null,
    watchId: null,
  };

  // ---------- time in Edmonton ----------
  function nowLocal() {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: CFG.TIMEZONE, weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
    }).formatToParts(new Date());
    const get = (t) => parts.find((p) => p.type === t)?.value;
    const day = DAY_NAMES.indexOf(get("weekday"));
    const mins = (parseInt(get("hour"), 10) % 24) * 60 + parseInt(get("minute"), 10);
    return { day, mins };
  }
  const toMins = (hhmm) => { if (!hhmm) return null; const [h, m] = hhmm.split(":").map(Number); return h * 60 + (m || 0); };
  function fmtTime(hhmm) {
    const m = toMins(hhmm); if (m == null) return null;
    const h = Math.floor(m / 60) % 24, mm = m % 60, ap = h >= 12 ? "pm" : "am", h12 = ((h + 11) % 12) + 1;
    return mm ? `${h12}:${String(mm).padStart(2, "0")} ${ap}` : `${h12} ${ap}`;
  }
  function timeLabel(d) {
    const s = fmtTime(d.start), e = fmtTime(d.end);
    if (s && e) return `${s} – ${e}`;
    if (s) return `${s} – close`;
    if (e) return `until ${e}`;
    return "All day";
  }
  // status: "now" | "later" | "ended" | "other-day"
  function dealStatus(d, day, mins) {
    const days = d.days && d.days.length ? d.days : [0, 1, 2, 3, 4, 5, 6];
    const s = toMins(d.start), e = toMins(d.end);
    const overnight = s != null && e != null && e <= s;
    if (mins != null && overnight && days.includes((day + 6) % 7) && mins < e) return "now"; // after-midnight tail of yesterday's deal
    if (!days.includes(day)) return "other-day";
    if (mins == null) return "later";
    const start = s ?? 0, end = overnight ? 24 * 60 : (e ?? 24 * 60);
    if (mins < start) return "later";
    if (mins < end) return "now";
    return "ended";
  }
  function isLive(d) {
    if (d.valid_from && new Date(d.valid_from) > new Date()) return false;
    if (d.valid_until && new Date(d.valid_until + "T23:59:59") < new Date()) return false;
    return true;
  }

  // ---------- geo ----------
  function km(a, b) {
    const R = 6371, toR = (x) => (x * Math.PI) / 180;
    const dLat = toR(b.lat - a.lat), dLng = toR(b.lng - a.lng);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(toR(a.lat)) * Math.cos(toR(b.lat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const fmtKm = (d) => (d < 1 ? `${Math.round(d * 1000 / 50) * 50} m` : `${d.toFixed(1)} km`);

  function locate({ quiet = false } = {}) {
    if (!("geolocation" in navigator)) { setDefaultLocation("Location isn't available on this device"); return; }
    $("locText").textContent = "Finding you…";
    navigator.geolocation.getCurrentPosition(
      (p) => {
        state.me = { lat: p.coords.latitude, lng: p.coords.longitude, label: "Near you" };
        store.set("lastLoc", { lat: state.me.lat, lng: state.me.lng });
        $("locText").textContent = "Showing deals near you";
        render();
      },
      () => setDefaultLocation(quiet ? null : "Location off — showing Downtown. Tap to try again."),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 5 * 60 * 1000 }
    );
  }
  function setDefaultLocation(msg) {
    const last = store.get("lastLoc", null);
    state.me = last ? { ...last, label: "Your last spot" } : { lat: CFG.DEFAULT_CENTER[0], lng: CFG.DEFAULT_CENTER[1], label: "Downtown Edmonton" };
    $("locText").textContent = msg || `Showing ${state.me.label} — tap to use my location`;
    render();
  }

  // ---------- data ----------
  async function load() {
    $("status").textContent = "Loading today's deals…";
    try {
      const res = await fetch(CFG.DATA_URL, { cache: "no-cache" });
      if (!res.ok) throw new Error(res.status);
      state.data = await res.json();
    } catch (e) {
      $("status").textContent = "Couldn't load deals right now. Check your connection and refresh.";
      return;
    }
    state.byId = new Map(state.data.restaurants.map((r) => [r.id, r]));
    if (state.data.generated_at) {
      const d = new Date(state.data.generated_at);
      $("updated").textContent = `Last updated ${d.toLocaleString("en-CA", { timeZone: CFG.TIMEZONE, dateStyle: "medium", timeStyle: "short" })}`;
    }
    buildChips();
    render();
  }

  function buildChips() {
    const counts = new Map();
    state.data.deals.forEach((d) => (d.categories || []).forEach((c) => counts.set(c, (counts.get(c) || 0) + 1)));
    const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([c]) => c);
    const el = $("chips"); el.innerHTML = "";
    [null, ...top].forEach((c) => {
      const b = document.createElement("button");
      b.textContent = c ? cap(c) : "All deals";
      b.className = state.cat === c ? "active" : "";
      b.onclick = () => { state.cat = c; buildChips(); render(); };
      el.appendChild(b);
    });
  }
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function buildDays() {
    const { day: today } = nowLocal();
    const el = $("days"); el.innerHTML = "";
    for (let i = 0; i < 7; i++) {
      const d = (today + i) % 7;
      const b = document.createElement("button");
      b.textContent = i === 0 ? "Today" : i === 1 ? "Tomorrow" : DAY_LONG[d];
      const sel = state.day == null ? i === 0 : state.day === d;
      b.className = sel ? "active" : "";
      b.onclick = () => { state.day = i === 0 ? null : d; buildDays(); render(); };
      el.appendChild(b);
    }
  }

  // ---------- compute results ----------
  function results() {
    if (!state.data || !state.me) return [];
    const { day: today, mins } = nowLocal();
    const viewingToday = state.day == null;
    const day = viewingToday ? today : state.day;
    const q = state.q.trim().toLowerCase();
    const out = [];
    for (const d of state.data.deals) {
      if (!isLive(d)) continue;
      if (state.cat && !(d.categories || []).includes(state.cat)) continue;
      const status = dealStatus(d, day, viewingToday ? mins : null);
      if (status === "other-day") continue;
      if (viewingToday && status === "ended" && state.onNow) continue;
      if (state.onNow && viewingToday && status !== "now") continue;
      const branches = (d.restaurant_ids || []).map((id) => state.byId.get(id)).filter(Boolean)
        .map((r) => ({ r, dist: km(state.me, r) })).filter((x) => x.dist <= state.radius).sort((a, b) => a.dist - b.dist);
      if (!branches.length) continue;
      if (q) {
        const hay = `${d.title} ${d.description || ""} ${(d.categories || []).join(" ")} ${branches[0].r.name}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      out.push({ d, status: viewingToday ? status : "later", nearest: branches[0], branches });
    }
    const rank = { now: 0, later: 1, ended: 2 };
    out.sort((a, b) => rank[a.status] - rank[b.status] || a.nearest.dist - b.nearest.dist);
    return out;
  }

  // ---------- render ----------
  function render() {
    buildDays();
    const items = results();
    const viewingToday = state.day == null;
    $("headline").textContent = viewingToday ? (state.onNow ? "Deals on right now" : "Deals near you today") : `${DAY_LONG[state.day]}'s deals`;
    if (!state.data) return;
    const nowCount = items.filter((i) => i.status === "now").length;
    $("status").textContent = items.length
      ? `${items.length} deal${items.length === 1 ? "" : "s"}${viewingToday && nowCount ? ` · ${nowCount} on now` : ""}${state.radius < 999 ? ` within ${state.radius} km` : ""}`
      : "";
    renderList(items, viewingToday);
    if (state.view === "map") renderMap(items);
  }

  function renderList(items, viewingToday) {
    const ul = $("list");
    if (!items.length) {
      ul.innerHTML = `<li class="empty"><b>No deals match yet</b>Try a bigger distance, another day, or clear the filters.</li>`;
      return;
    }
    ul.innerHTML = items.map(({ d, status, nearest, branches }) => {
      const r = nearest.r;
      const badge = !viewingToday ? `<span class="badge">${timeLabel(d)}</span>`
        : status === "now" ? `<span class="badge now">● On now · ${timeLabel(d)}</span>`
        : status === "later" ? `<span class="badge later">Later today · ${timeLabel(d)}</span>`
        : `<span class="badge">Ended · ${timeLabel(d)}</span>`;
      const days = d.days && d.days.length && d.days.length < 7 ? d.days.map((x) => DAY_NAMES[x]).join(", ") : "Every day";
      const more = branches.length > 1 ? ` · +${branches.length - 1} more location${branches.length > 2 ? "s" : ""} nearby` : "";
      const dir = `https://www.google.com/maps/dir/?api=1&destination=${r.lat},${r.lng}`;
      const cats = (d.categories || []).slice(0, 3).map((c) => `<span class="badge">${esc(cap(c))}</span>`).join("");
      return `<li class="card">
        <div class="card-top">
          <h3 class="deal-title">${esc(d.title)}</h3>
          ${d.price ? `<span class="price">${esc(d.price)}</span>` : ""}
        </div>
        <p class="venue">${esc(r.name)}</p>
        <p class="meta">${fmtKm(nearest.dist)} away${r.address ? " · " + esc(r.address) : ""}${more}</p>
        ${d.description ? `<p class="desc">${esc(d.description)}</p>` : ""}
        <div class="badges">${badge}<span class="badge">${days}</span>${cats}</div>
        <div class="actions">
          <a href="${dir}" target="_blank" rel="noopener">Directions</a>
          ${d.source_url ? `<a href="${esc(d.source_url)}" target="_blank" rel="noopener">Where we found it</a>` : ""}
          ${r.website && r.website !== d.source_url ? `<a href="${esc(r.website)}" target="_blank" rel="noopener">Website</a>` : ""}
        </div>
      </li>`;
    }).join("");
  }

  function renderMap(items) {
    if (!window.L) { $("map").textContent = "Map couldn't load."; return; }
    if (!state.map) {
      state.map = L.map("map", { zoomControl: true }).setView([state.me.lat, state.me.lng], 13);
      L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
        maxZoom: 19, subdomains: "abcd",
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      }).addTo(state.map);
      state.layer = L.layerGroup().addTo(state.map);
    }
    state.layer.clearLayers();
    if (state.meMarker) state.meMarker.remove();
    state.meMarker = L.marker([state.me.lat, state.me.lng], { icon: L.divIcon({ className: "me-dot", iconSize: [16, 16] }), title: "You" }).addTo(state.map);

    // one pin per restaurant, listing its deals
    const pins = new Map();
    items.forEach(({ d, status, branches }) => branches.forEach(({ r }) => {
      const p = pins.get(r.id) || { r, deals: [], now: false };
      p.deals.push(d); p.now = p.now || status === "now"; pins.set(r.id, p);
    }));
    const bounds = [[state.me.lat, state.me.lng]];
    pins.forEach(({ r, deals, now }) => {
      const icon = L.divIcon({ className: "pin" + (now ? " now" : ""), html: String(deals.length), iconSize: [28, 28] });
      const html = `<b>${esc(r.name)}</b><br>${deals.map((d) => `• ${esc(d.title)}${d.price ? " — " + esc(d.price) : ""} <small>(${timeLabel(d)})</small>`).join("<br>")}
        <br><a href="https://www.google.com/maps/dir/?api=1&destination=${r.lat},${r.lng}" target="_blank" rel="noopener">Directions</a>`;
      L.marker([r.lat, r.lng], { icon }).bindPopup(html).addTo(state.layer);
      bounds.push([r.lat, r.lng]);
    });
    if (bounds.length > 1) state.map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });
    setTimeout(() => state.map.invalidateSize(), 50);
  }

  // ---------- alerts ----------
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isStandalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const pushSupported = "serviceWorker" in navigator && "PushManager" in window && CFG.API_URL && CFG.VAPID_PUBLIC_KEY;

  async function registerSW() {
    if (!("serviceWorker" in navigator)) return null;
    try { return await navigator.serviceWorker.register("sw.js"); } catch { return null; }
  }

  function openAlerts() {
    $("nearbyWatch").checked = store.get("nearbyWatch", false);
    $("pushDaily").checked = store.get("pushDaily", false);
    $("pushCats").value = store.get("pushCats", "");
    $("pushBlock").hidden = !(pushSupported || (isIOS && !isStandalone && CFG.VAPID_PUBLIC_KEY));
    $("iosHint").hidden = !(isIOS && !isStandalone);
    $("pushDaily").disabled = isIOS && !isStandalone;
    $("alertsMsg").textContent = "";
    $("alertsDlg").showModal();
  }

  async function saveAlerts(ev) {
    ev.preventDefault();
    const msg = $("alertsMsg");
    const wantWatch = $("nearbyWatch").checked, wantPush = $("pushDaily").checked;
    const cats = $("pushCats").value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
    if ((wantWatch || wantPush) && "Notification" in window && Notification.permission !== "granted") {
      const p = await Notification.requestPermission();
      if (p !== "granted") { msg.textContent = "Notifications are blocked for this site. You can allow them in your browser settings."; return; }
    }
    store.set("nearbyWatch", wantWatch); store.set("pushDaily", wantPush); store.set("pushCats", cats.join(", "));
    wantWatch ? startWatch() : stopWatch();
    if (pushSupported) {
      try {
        const reg = await registerSW(); await navigator.serviceWorker.ready;
        let sub = await reg.pushManager.getSubscription();
        if (wantPush) {
          if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(CFG.VAPID_PUBLIC_KEY) });
          const spot = state.me || { lat: CFG.DEFAULT_CENTER[0], lng: CFG.DEFAULT_CENTER[1] };
          await fetch(CFG.API_URL + "/subscribe", {
            method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ subscription: sub, lat: +spot.lat.toFixed(3), lng: +spot.lng.toFixed(3), radius_km: Math.min(state.radius, 25), categories: cats }),
          });
        } else if (sub) {
          await fetch(CFG.API_URL + "/unsubscribe", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ endpoint: sub.endpoint }) });
          await sub.unsubscribe();
        }
      } catch (e) { msg.textContent = "Couldn't set up daily alerts right now — please try again later."; return; }
    }
    $("alertsDlg").close();
    $("alertsBtn").textContent = wantWatch || wantPush ? "🔔" : "🔕";
  }

  function b64ToU8(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }

  // While the app is open: notify when within ~400 m of a deal that's on now (once per deal per day).
  function startWatch() {
    if (state.watchId != null || !("geolocation" in navigator)) return;
    state.watchId = navigator.geolocation.watchPosition((p) => {
      state.me = { lat: p.coords.latitude, lng: p.coords.longitude, label: "Near you" };
      checkNearby();
    }, () => {}, { enableHighAccuracy: false, maximumAge: 60000 });
  }
  function stopWatch() { if (state.watchId != null) navigator.geolocation.clearWatch(state.watchId); state.watchId = null; }
  async function checkNearby() {
    if (!state.data || !("Notification" in window) || Notification.permission !== "granted") return;
    const { day, mins } = nowLocal();
    const todayKey = new Date().toISOString().slice(0, 10);
    const seen = store.get("seen", {});
    for (const d of state.data.deals) {
      if (!isLive(d) || dealStatus(d, day, mins) !== "now") continue;
      for (const id of d.restaurant_ids || []) {
        const r = state.byId.get(id); if (!r) continue;
        if (km(state.me, r) > 0.4) continue;
        const key = `${todayKey}:${d.id}`;
        if (seen[key]) continue;
        seen[key] = 1;
        const reg = await navigator.serviceWorker?.getRegistration();
        const body = `${r.name} is ${fmtKm(km(state.me, r))} away${d.price ? " · " + d.price : ""}`;
        if (reg) reg.showNotification(`Deal nearby: ${d.title}`, { body, icon: "icon-192.png", tag: key, data: { url: location.href } });
        else new Notification(`Deal nearby: ${d.title}`, { body });
      }
    }
    // keep only today's keys
    Object.keys(seen).forEach((k) => { if (!k.startsWith(todayKey)) delete seen[k]; });
    store.set("seen", seen);
  }

  // ---------- submit ----------
  async function submitDeal(ev) {
    ev.preventDefault();
    const f = $("submitForm");
    if (!f.reportValidity()) return;
    const body = Object.fromEntries(new FormData(f).entries());
    if (!CFG.API_URL) {
      location.href = `mailto:hello@foodish.ca?subject=${encodeURIComponent("Deal for Foodish: " + body.restaurant)}&body=${encodeURIComponent(`${body.deal}\n${body.url || ""}`)}`;
      $("submitDlg").close(); return;
    }
    try {
      const r = await fetch(CFG.API_URL + "/submit", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw 0;
      $("submitMsg").textContent = "Thanks! We'll check it and add it soon.";
      f.reset(); setTimeout(() => $("submitDlg").close(), 1400);
    } catch { $("submitMsg").textContent = "Couldn't send that — please try again."; }
  }

  // ---------- wire up ----------
  function init() {
    $("radius").value = String(state.radius);
    $("locBtn").onclick = () => locate();
    $("search").oninput = (e) => { state.q = e.target.value; render(); };
    $("onNow").onchange = (e) => { state.onNow = e.target.checked; render(); };
    $("radius").onchange = (e) => { state.radius = +e.target.value; store.set("radius", state.radius); render(); };
    $("listViewBtn").onclick = () => setView("list");
    $("mapViewBtn").onclick = () => setView("map");
    $("alertsBtn").onclick = openAlerts;
    $("saveAlerts").onclick = saveAlerts;
    $("submitBtn").onclick = () => { $("submitMsg").textContent = ""; $("submitDlg").showModal(); };
    $("sendSubmit").onclick = submitDeal;
    if (store.get("nearbyWatch", false)) startWatch();
    registerSW();

    // Ask for location straight away if previously granted; otherwise show Downtown until they tap.
    if (navigator.permissions?.query) {
      navigator.permissions.query({ name: "geolocation" }).then((p) => (p.state === "granted" ? locate({ quiet: true }) : setDefaultLocation()), () => setDefaultLocation());
    } else setDefaultLocation();
    load();
    setInterval(render, 60 * 1000); // keep "on now" badges fresh
  }
  function setView(v) {
    state.view = v;
    $("listViewBtn").classList.toggle("active", v === "list");
    $("mapViewBtn").classList.toggle("active", v === "map");
    $("list").hidden = v !== "list"; $("map").hidden = v !== "map";
    render();
  }
  // exposed for tests
  window.__foodish = { dealStatus, toMins, km };
  init();
})();
