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
  // The app's states, named. Each machine has one function that changes it (setLoad, setLocation,
  // syncAlertsBadge, setView) so the screen always matches the state. Documented in docs/STATE_MACHINES.md.
  const LOAD = Object.freeze({ LOADING: "loading", READY: "ready", ERROR: "error" });
  // "away": we know where the visitor is, but it's far from the city being shown (browsing another
  // city, or somewhere Foodish doesn't cover yet), so distances are measured from that city's centre.
  const LOC = Object.freeze({ LOCATING: "locating", PRECISE: "precise", LAST_KNOWN: "last_known", DEFAULT: "default", AWAY: "away" });
  const STALE_AFTER_H = 36; // deals older than this get a gentle "last checked" note
  const CITIES = CFG.CITIES; // the cities Foodish covers (config.js, kept in step with cities.json)
  const NEAR_KM = 100;       // a visitor within this distance of a city's centre counts as "in" that city
  const cityById = (id) => CITIES.find((c) => c.id === id);

  const state = {
    city: CFG.DEFAULT_CITY, // which city's deals are showing; changed only by setCity()
    cityLocked: false,      // true when the link (?city=) or the picker chose it: location won't switch it
    gps: null,              // the visitor's last real position {lat, lng}, if they shared it
    load: LOAD.LOADING,     // loading -> ready | error; error -> loading (retry)
    data: null,
    byId: new Map(),
    loc: null,              // locating | precise | last_known | default | away
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

  // ---------- time + deal status: shared rules in deal-state.js (same as pipeline/deal_state.py) ----------
  const DS = window.FoodishDealState;
  const ST = DS.STATES;
  const city = () => cityById(state.city);
  const cityCentre = () => ({ lat: city().center[0], lng: city().center[1] });
  const nowLocal = () => DS.zonedNow(city().timezone); // the shown city's own clock
  function fmtTime(hhmm) {
    const m = DS.toMins(hhmm); if (m == null) return null;
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
  // Display order within today: on now, then later today, then all day, then ended.
  const RANK = { [ST.ON_NOW]: 0, [ST.LATER]: 1, [ST.ALL_DAY]: 2, [ST.SCHEDULED]: 2, [ST.ENDED]: 3 };

  // ---------- geo ----------
  function km(a, b) {
    const R = 6371, toR = (x) => (x * Math.PI) / 180;
    const dLat = toR(b.lat - a.lat), dLng = toR(b.lng - a.lng);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(toR(a.lat)) * Math.cos(toR(b.lat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const fmtKm = (d) => (d < 1 ? `${Math.round(d * 1000 / 50) * 50} m` : `${d.toFixed(1)} km`);

  // City machine: which city's deals are showing. Chosen by, in order: the link (?city=vancouver),
  // the city picker, the visitor's location (nearest city within NEAR_KM), the last city used, the default.
  function nearestCity(pt) {
    return CITIES.map((c) => ({ city: c, km: km(pt, { lat: c.center[0], lng: c.center[1] }) })).sort((a, b) => a.km - b.km)[0];
  }
  function setCity(id, { locked = state.cityLocked } = {}) {
    if (!cityById(id)) return;
    const changed = id !== state.city;
    state.city = id; state.cityLocked = locked;
    store.set("city", id);
    $("cityPick").value = id;
    $("radius").querySelector('option[value="999"]').textContent = `All of ${city().name}`;
    document.title = `Foodish — Restaurant deals near you in ${city().name}`;
    if (changed) { // another city's deals: forget this one's and load the new file
      state.data = null; state.byId = new Map(); state.cat = null;
      $("chips").innerHTML = ""; $("list").innerHTML = ""; $("updated").textContent = "";
      load();
    }
  }

  // Location machine: locating -> precise (GPS) | last_known (saved spot) | default (city centre) | away.
  function setLocation(loc, coords, note) {
    state.loc = loc;
    if (loc === LOC.PRECISE) {
      state.me = { lat: coords.lat, lng: coords.lng, label: "Near you" };
      store.set("lastLoc", { lat: coords.lat, lng: coords.lng });
    } else if (loc === LOC.LAST_KNOWN) {
      state.me = { ...coords, label: "Your last spot" };
    } else if (loc === LOC.DEFAULT || loc === LOC.AWAY) {
      state.me = { ...cityCentre(), label: city().label };
    }
    $("locText").textContent = loc === LOC.LOCATING ? "Finding you…"
      : loc === LOC.PRECISE ? "Showing deals near you"
      : note || `Showing ${state.me.label} — tap to use my location`;
    if (loc !== LOC.LOCATING) render();
  }

  // A real position arrived: follow the visitor to their city (unless the city was chosen on purpose),
  // then measure from them if they're in the shown city, or from its centre if they're away.
  function applyPosition(coords) {
    state.gps = coords;
    const near = nearestCity(coords);
    if (near.km <= NEAR_KM && near.city.id !== state.city && !state.cityLocked) setCity(near.city.id);
    if (km(coords, cityCentre()) <= NEAR_KM) setLocation(LOC.PRECISE, coords);
    else setLocation(LOC.AWAY, null, near.km > NEAR_KM
      ? `Foodish isn't in your area yet — showing ${city().label}`
      : `Showing ${city().label}`);
  }

  function locate({ quiet = false } = {}) {
    if (!("geolocation" in navigator)) { setFallbackLocation("Location isn't available on this device"); return; }
    setLocation(LOC.LOCATING);
    navigator.geolocation.getCurrentPosition(
      (p) => applyPosition({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => setFallbackLocation(quiet ? null : `Location off — showing ${city().label}. Tap to try again.`),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 5 * 60 * 1000 }
    );
  }
  function setFallbackLocation(note) {
    const last = store.get("lastLoc", null);
    if (last && km(last, cityCentre()) <= NEAR_KM) setLocation(LOC.LAST_KNOWN, last, note); // only if it's in this city
    else setLocation(LOC.DEFAULT, null, note);
  }
  // After the city changes: place the visitor again relative to the new city.
  function relocate() {
    if (state.gps) applyPosition(state.gps);
    else setFallbackLocation();
  }

  // ---------- data ----------
  // Data machine: loading -> ready | error. error -> loading on Retry, when the device comes back
  // online, or when the tab is reopened. A failed refresh keeps the deals already on screen.
  function setLoad(next, detail) {
    state.load = next;
    const status = $("status");
    if (next === LOAD.LOADING && !state.data) status.textContent = "Loading today's deals…";
    if (next === LOAD.ERROR && !state.data) {
      status.innerHTML = `Couldn't load deals right now (${esc(detail)}). <button type="button" class="link-btn" id="retryLoad">Try again</button>`;
      $("retryLoad").onclick = () => load();
    }
    if (next === LOAD.READY) {
      const g = state.data.generated_at ? new Date(state.data.generated_at) : null;
      if (g && !isNaN(g)) {
        const ageH = (Date.now() - g.getTime()) / 36e5;
        const when = g.toLocaleString("en-CA", { timeZone: DS.zoneFor(city().timezone, g), dateStyle: "medium", timeStyle: "short" });
        $("updated").textContent = ageH > STALE_AFTER_H
          ? `Deals last checked ${when}, over a day ago — some may have changed.`
          : `Last updated ${when}`;
      }
      render();
    }
  }

  async function load() {
    if (state.load === LOAD.LOADING && state.data) return; // a refresh is already running
    const forCity = state.city;
    setLoad(LOAD.LOADING);
    try {
      const res = await fetch(city().dataUrl, { cache: "no-cache" });
      if (forCity !== state.city) return; // the visitor switched city while this was loading
      if (res.status === 404) throw new Error(`${city().name} deals aren't ready yet`);
      if (!res.ok) throw new Error(`error ${res.status}`);
      const data = await res.json();
      if (forCity !== state.city) return;
      if (!Array.isArray(data.deals) || !Array.isArray(data.restaurants)) throw new Error("unexpected file");
      state.data = data;
    } catch (e) {
      if (forCity !== state.city) return;
      setLoad(LOAD.ERROR, navigator.onLine === false ? "you're offline" : e.message || "network error");
      return;
    }
    state.byId = new Map(state.data.restaurants.map((r) => [r.id, r]));
    buildChips();
    setLoad(LOAD.READY);
  }

  function buildChips() {
    const counts = new Map();
    state.data.deals.forEach((d) => (d.categories || []).forEach((c) => counts.set(c, (counts.get(c) || 0) + 1)));
    const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([c]) => c);
    if (state.cat && !top.includes(state.cat)) top.unshift(state.cat); // e.g. arrived from /deals/sushi.html
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
    const now = nowLocal();
    const viewingToday = state.day == null;
    // Another day: no time of day, and "today's date" is still today's (valid_until/valid_from are checked against it).
    const at = viewingToday ? now : { date: now.date, day: state.day, mins: null };
    const q = state.q.trim().toLowerCase();
    const out = [];
    for (const d of state.data.deals) {
      if (state.cat && !(d.categories || []).includes(state.cat)) continue;
      const status = DS.dealState(d, at);
      if (!(status in RANK)) continue; // expired, upcoming, not_today
      if (state.onNow && viewingToday && status !== ST.ON_NOW) continue;
      const branches = (d.restaurant_ids || []).map((id) => state.byId.get(id)).filter(Boolean)
        .map((r) => ({ r, dist: km(state.me, r) })).filter((x) => x.dist <= state.radius).sort((a, b) => a.dist - b.dist);
      if (!branches.length) continue;
      if (q) {
        const hay = `${d.title} ${d.description || ""} ${(d.categories || []).join(" ")} ${branches[0].r.name}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      out.push({ d, status, nearest: branches[0], branches });
    }
    out.sort((a, b) => RANK[a.status] - RANK[b.status] || a.nearest.dist - b.nearest.dist);
    return out;
  }

  // ---------- render ----------
  function render() {
    buildDays();
    writeLinkFilters();
    const items = results();
    const viewingToday = state.day == null;
    $("headline").textContent = viewingToday ? (state.onNow ? "Deals on right now" : "Deals near you today") : `${DAY_LONG[state.day]}'s deals`;
    if (!state.data) return;
    const nowCount = items.filter((i) => i.status === ST.ON_NOW).length;
    $("status").textContent = items.length
      ? `${items.length} deal${items.length === 1 ? "" : "s"}${viewingToday && nowCount ? ` · ${nowCount} on now` : ""}${state.radius < 999 ? ` within ${state.radius} km` : ""}`
      : "";
    renderList(items, viewingToday);
    if (state.view === "map" && state.me) renderMap(items); // the map needs a location to centre on
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
        : status === ST.ON_NOW ? `<span class="badge now">● On now · ${timeLabel(d)}</span>`
        : status === ST.LATER ? `<span class="badge later">Later today · ${timeLabel(d)}</span>`
        : status === ST.ALL_DAY ? `<span class="badge">All day</span>`
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
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
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
      p.deals.push(d); p.now = p.now || status === ST.ON_NOW; pins.set(r.id, p);
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

  // Alerts state, derived (never stored twice): "on" only if the visitor asked for alerts AND the
  // browser still allows notifications. If permission was revoked in settings, the bell says so.
  function alertsState() {
    const wanted = store.get("nearbyWatch", false) || store.get("pushDaily", false);
    const perm = "Notification" in window ? Notification.permission : "unsupported";
    if (!wanted) return "off";
    return perm === "granted" ? "on" : "blocked";
  }
  function syncAlertsBadge() {
    const st = alertsState();
    const btn = $("alertsBtn");
    btn.textContent = st === "on" ? "🔔" : "🔕";
    btn.title = btn.ariaLabel = st === "on" ? "Deal alerts: on" : st === "blocked" ? "Deal alerts: blocked in browser settings" : "Deal alerts: off";
    if (st !== "on") stopWatch();
  }

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
          const spot = state.me || cityCentre();
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
    syncAlertsBadge();
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
      state.gps = { lat: p.coords.latitude, lng: p.coords.longitude };
      if (km(state.gps, cityCentre()) > NEAR_KM) return; // not in the city being shown
      state.me = { ...state.gps, label: "Near you" };
      checkNearby();
    }, () => {}, { enableHighAccuracy: false, maximumAge: 60000 });
  }
  function stopWatch() { if (state.watchId != null) navigator.geolocation.clearWatch(state.watchId); state.watchId = null; }
  async function checkNearby() {
    if (!state.data || !("Notification" in window) || Notification.permission !== "granted") return;
    const now = nowLocal();
    const todayKey = now.date; // the city's local date, so "once per deal per day" follows the local day
    const seen = store.get("seen", {});
    for (const d of state.data.deals) {
      if (DS.dealState(d, now) !== ST.ON_NOW) continue;
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
    const btn = $("sendSubmit");
    if (btn.disabled) return; // already sending
    btn.disabled = true; $("submitMsg").textContent = "Sending…";
    try {
      const r = await fetch(CFG.API_URL + "/submit", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw 0;
      $("submitMsg").textContent = "Thanks! We'll check it and add it soon.";
      f.reset(); setTimeout(() => $("submitDlg").close(), 1400);
    } catch { $("submitMsg").textContent = "Couldn't send that — please try again."; }
    finally { btn.disabled = false; }
  }

  // ---------- wire up ----------
  // The filters live in the address bar, so any view can be shared, bookmarked or opened by a link:
  //   /?city=vancouver  /?cat=wings  /?day=3 (0 = Sunday)  /?now=1 (on right now)  /?view=map  /?km=2  /?q=tacos
  // The /deals/ pages link in this way too.
  function readLinkFilters() {
    const p = new URLSearchParams(location.search);
    const linkCity = (p.get("city") || "").trim().toLowerCase();
    if (cityById(linkCity)) { state.city = linkCity; state.cityLocked = true; } // a link to a city always opens that city
    const cat = (p.get("cat") || "").trim().toLowerCase();
    if (cat) state.cat = cat;
    const day = parseInt(p.get("day"), 10);
    if (day >= 0 && day <= 6) state.day = day === nowLocal().day ? null : day;
    if (p.get("now") === "1") state.onNow = true;
    if (p.get("view") === "map") state.view = "map";
    const radius = parseFloat(p.get("km"));
    if ([2, 5, 10, 999].includes(radius)) state.radius = radius;
    if (p.get("q")) state.q = p.get("q");
  }
  function writeLinkFilters() {
    const p = new URLSearchParams();
    if (state.cityLocked || state.city !== CFG.DEFAULT_CITY) p.set("city", state.city);
    if (state.cat) p.set("cat", state.cat);
    if (state.day != null) p.set("day", state.day);
    if (state.onNow) p.set("now", "1");
    if (state.view === "map") p.set("view", "map");
    if (state.radius !== store.get("radius", 5)) p.set("km", state.radius);
    if (state.q.trim()) p.set("q", state.q.trim());
    const url = location.pathname + (p.toString() ? "?" + p : "") + location.hash;
    if (url !== location.pathname + location.search + location.hash) history.replaceState(null, "", url);
  }

  function init() {
    const lastCity = store.get("city", null); // start where the visitor was last time; location may correct it
    if (cityById(lastCity)) state.city = lastCity;
    readLinkFilters();
    $("cityPick").innerHTML = CITIES.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
    $("cityPick").onchange = (e) => { setCity(e.target.value, { locked: true }); relocate(); };
    setCity(state.city);
    $("radius").value = String(state.radius);
    $("onNow").checked = state.onNow;
    $("search").value = state.q;
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
    syncAlertsBadge();
    registerSW();
    // Data recovery: retry after an error when the connection returns; refresh a tab left open overnight.
    window.addEventListener("online", () => { if (state.load === LOAD.ERROR) load(); });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      syncAlertsBadge(); // permission may have changed in settings
      const g = state.data && Date.parse(state.data.generated_at);
      if (state.load === LOAD.ERROR || (g && Date.now() - g > 20 * 36e5)) load();
    });

    // Ask for location straight away if previously granted; otherwise show the city centre until they tap.
    if (navigator.permissions?.query) {
      navigator.permissions.query({ name: "geolocation" }).then((p) => (p.state === "granted" ? locate({ quiet: true }) : setFallbackLocation()), () => setFallbackLocation());
    } else setFallbackLocation();
    if (state.view === "map") setView("map");
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
  window.__foodish = { dealState: DS.dealState, km, state, LOAD, LOC, alertsState, setCity, nearestCity };
  init();
})();
