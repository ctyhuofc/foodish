/* Foodish — front-end app ("Blue Hour" design). No build step: plain JS, works on any static host.
 * The rule everywhere: a deal that is on right now is lit (yellow); later today is an outline; all day sits on
 * the blue band; deals that have ended aren't shown. Deal timing comes only from deal-state.js; the look (Daylight or
 * Blue Hour) only from sky.js. State machines are documented in docs/STATE_MACHINES.md. */
(() => {
  "use strict";
  const CFG = window.FOODISH_CONFIG;
  const DS = window.FoodishDealState;
  const SKY = window.FoodishSky;
  const ST = DS.STATES;
  const DAY_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const $ = (id) => document.getElementById(id);

  // ---------- safe localStorage ----------
  const store = {
    get(k, d) { try { const v = localStorage.getItem("foodish:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("foodish:" + k, JSON.stringify(v)); } catch {} },
  };

  // ---------- state ----------
  const LOAD = Object.freeze({ LOADING: "loading", READY: "ready", ERROR: "error" });
  // "away": we know where the visitor is, but it's far from the city being shown, so distances are measured
  // from that city's centre.
  const LOC = Object.freeze({ LOCATING: "locating", PRECISE: "precise", LAST_KNOWN: "last_known", DEFAULT: "default", AWAY: "away" });
  const STALE_AFTER_H = 36;            // the whole file is older than this: a gentle "last checked" note
  const STALE_DEAL_DAYS = 30;          // one deal not re-checked for this long: "may be out of date"
  const RADII = [1, 2, 5, 10, 999];
  const CITIES = CFG.CITIES;
  const NEAR_KM = 100;
  const cityById = (id) => CITIES.find((c) => c.id === id);
  // Big chains, for the "Include big chains" switch (matched against restaurant names).
  const CHAINS = ["domino", "papa john", "panago", "pizza 73", "pizza hut", "little caesar", "boston pizza", "earls",
    "carl s jr", "east side mario", "freshii", "wok box", "chuck e", "blaze pizza", "smashburger", "mcdonald", "a w ",
    "wendy", "burger king", "subway", "kfc", "popeyes", "starbucks", "tim horton", "booster juice", "dairy queen",
    "denny", "ihop", "applebee", "montana s", "kelseys", "swiss chalet", "harvey s", "taco bell", "five guys", "mary brown",
    "the keg", "cactus club", "joey", "moxie", "white spot", "milestones", "chipotle", "osmow", "mucho burrito", "quesada"];

  const state = {
    city: CFG.DEFAULT_CITY, cityLocked: false, gps: null,
    load: LOAD.LOADING, data: null, byId: new Map(),
    loc: null, me: null,
    day: null,            // 0-6, null = today
    onNow: false,         // ?now=1: only what's on right now
    radius: store.get("radius", 5),
    cat: null, q: "",
    view: "list",
    chains: store.get("chains", true),
    lookPref: store.get("look", "auto"),  // auto | day | night (the menu setting)
    lookForced: null,                      // ?look=day|night, for testing; never saved
    look: document.documentElement.dataset.look || "night",
    histStep: null,       // view|day|category last written to the address (for the back button)
    open: new Set(),      // place blocks showing "what else is on"
    allDayAll: false,
    nowAll: false,        // show every place on now (the list starts with the first 8)
    laterAll: false,      // show every start time later today (the list starts with the first 5)
    schedAll: new Set(),  // other-day sections opened in full, as "day|section"
    sel: null,            // place selected on the map
    map: null, layer: null, meMarker: null, mapKey: "",
    watchId: null, lastScroll: 0,
  };

  // ---------- time ----------
  const city = () => cityById(state.city);
  const cityCentre = () => ({ lat: city().center[0], lng: city().center[1] });
  const nowLocal = () => DS.zonedNow(city().timezone);
  const clock = (m) => { m = ((m % 1440) + 1440) % 1440; const h = Math.floor(m / 60), mm = m % 60; return `${((h + 11) % 12) + 1}:${String(mm).padStart(2, "0")}`; };
  const ampm = (m) => (((m % 1440) + 1440) % 1440 >= 720 ? "pm" : "am");
  const clockAP = (m) => `${clock(m)} ${ampm(m)}`;
  const shortAP = (m) => { const c = clock(m); return (c.endsWith(":00") ? c.slice(0, -3) : c) + " " + ampm(m); };
  function hoursText(d) {
    const s = DS.toMins(d.start), e = DS.toMins(d.end);
    if (s != null && e != null) return `${shortAP(s)} to ${shortAP(e)}`;
    if (s != null) return `from ${shortAP(s)} to close`;
    if (e != null) return `until ${shortAP(e)}`;
    return "all day";
  }
  function daysText(d) {
    const ds = d.days && d.days.length && d.days.length < 7 ? d.days : null;
    if (!ds) return "Every day";
    if (ds.join() === "1,2,3,4,5") return "Weekdays";
    if (ds.join() === "0,6") return "Weekends";
    return ds.map((x) => DAY_LONG[x].slice(0, 3)).join(", ");
  }
  const leftText = (m) => (m < 60 ? `${m} MIN` : `${Math.floor(m / 60)} H ${String(m % 60).padStart(2, "0")}`);
  // When an "on now" deal ends, in minutes on today's clock (may pass 1440 for after-midnight endings). null = until close.
  function endsAt(d, now) {
    const s = DS.toMins(d.start), e = DS.toMins(d.end);
    if (e == null) return null;
    if (s != null && e <= s) return now.mins < e ? e : e + 1440; // overnight: tail after midnight, or tonight's end
    return e;
  }
  // The city's UTC offset in minutes right now (for calendar files).
  function utcOffsetMins() {
    const n = new Date(), z = DS.zonedNow(city().timezone, n);
    const local = Date.UTC(+z.date.slice(0, 4), +z.date.slice(5, 7) - 1, +z.date.slice(8, 10), Math.floor(z.mins / 60), z.mins % 60);
    return Math.round((local - Math.floor(n.getTime() / 60000) * 60000) / 60000);
  }

  // ---------- geo ----------
  function km(a, b) {
    const R = 6371, toR = (x) => (x * Math.PI) / 180;
    const dLat = toR(b.lat - a.lat), dLng = toR(b.lng - a.lng);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(toR(a.lat)) * Math.cos(toR(b.lat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const kmShort = (d) => (d < 1 ? d.toFixed(2) : d < 10 ? d.toFixed(1) : String(Math.round(d)));
  const kmCaps = (d) => `${kmShort(d)} KM`;
  const radiusText = (r) => (r >= 999 ? `all of ${city().name}` : `${r} km`);

  // ---------- text helpers ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const isChain = (name) => { const k = " " + String(name || "").toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ") + " "; return CHAINS.some((c) => k.includes(" " + c)); };
  function priceValue(p) {
    if (!p) return null;
    const cents = /(\d+(?:\.\d+)?)\s*¢/.exec(p); if (cents) return +cents[1] / 100;
    const m = /\$\s*(\d+(?:\.\d+)?)/.exec(p) || /^(\d+(?:\.\d+)?)$/.exec(p.trim());
    return m ? +m[1] : null;
  }
  const percentOff = (p) => { const m = /(\d{1,2})\s*%/.exec(p || ""); return m ? +m[1] : null; };
  // The big figure for a deal: its price, or (no price) a short word from its title.
  function figure(d) {
    if (d.price) return { text: d.price, word: !/\d/.test(d.price) || d.price.length > 9 };
    return { text: shortTitle(d.title), word: true };
  }
  const shortTitle = (t) => { const s = String(t || "Deal"); return s.length > 22 ? s.slice(0, 21).trimEnd() + "…" : s; };
  // The small caps line under a big figure: what the price buys. A deal with no price already shows its title big,
  // so it gets a short piece of its description instead (descLine).
  const what = (d) => (d.price ? d.price_unit || d.title : "");
  const descLine = (d) => { const s = String(d.description || "").trim(); return s.length > 90 ? s.slice(0, 88).replace(/\s+\S*$/, "") + "…" : s; };
  const under = (d) => (d.price ? `<span class="unit">${esc(what(d))}</span>` : descLine(d) ? `<span class="desc-s">${esc(descLine(d))}</span>` : "");
  const fig = (d, extra = "") => { const f = figure(d); return `<span class="num${f.word ? " word" : ""}${extra}">${esc(f.text)}</span>`; };
  const directions = (r) => `https://www.google.com/maps/dir/?api=1&destination=${r.lat},${r.lng}`;
  function staleNote(d) {
    if (!d.verified_at) return "";
    const t = Date.parse(d.verified_at.slice(0, 10) + "T12:00:00Z");
    if (isNaN(t) || Date.now() - t < STALE_DEAL_DAYS * 864e5) return "";
    return `May be out of date: last seen on their site ${new Date(t).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}.`;
  }

  // ---------- city ----------
  // First visit, before any location permission: guess the city from the phone's time zone, so a
  // visitor in Vancouver or Toronto doesn't open on Edmonton. Only the zone's name is read, not the time.
  const ZONE_CITY = { "America/Vancouver": "vancouver", "America/Toronto": "toronto", "America/Montreal": "toronto",
    "America/Nipigon": "toronto", "America/Thunder_Bay": "toronto", "America/Edmonton": "edmonton" };
  function cityFromClock() {
    let zone = "";
    try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* old browser */ }
    const id = ZONE_CITY[zone] || (CITIES.find((c) => c.timezone === zone) || {}).id;
    return cityById(id) ? id : CFG.DEFAULT_CITY;
  }
  function nearestCity(pt) {
    return CITIES.map((c) => ({ city: c, km: km(pt, { lat: c.center[0], lng: c.center[1] }) })).sort((a, b) => a.km - b.km)[0];
  }
  function setCity(id, { locked = state.cityLocked } = {}) {
    if (!cityById(id)) return;
    const changed = id !== state.city;
    state.city = id; state.cityLocked = locked;
    store.set("city", id);
    $("cityName").textContent = city().name;
    document.title = `Foodish — Restaurant deals near you in ${city().name}`;
    $("browseLink").href = city().id === CFG.DEFAULT_CITY ? "deals/" : `${city().id}/deals/`;
    buildCityPanel();
    if (changed) {
      state.data = null; state.byId = new Map(); state.cat = null; state.sel = null; state.open.clear(); state.allDayAll = false; state.nowAll = false; state.laterAll = false; state.schedAll.clear();
      $("updated").textContent = "";
      applyLook();
      load();
    }
  }
  function buildCityPanel() {
    $("cityPanel").innerHTML = CITIES.map((c) => `<button type="button" data-city="${esc(c.id)}" aria-pressed="${c.id === state.city}">
      <span class="nm">${esc(c.name)}</span><span class="tz">${esc(c.label.replace(/^Downtown /, "").toUpperCase())}</span></button>`).join("");
  }

  // ---------- location ----------
  function setLocation(loc, coords, note) {
    state.loc = loc;
    if (loc === LOC.PRECISE) {
      state.me = { lat: coords.lat, lng: coords.lng, label: "Near you" };
      store.set("lastLoc", { lat: coords.lat, lng: coords.lng });
    } else if (loc === LOC.LAST_KNOWN) state.me = { ...coords, label: "Your last spot" };
    else if (loc === LOC.DEFAULT || loc === LOC.AWAY) state.me = { ...cityCentre(), label: city().label };
    $("locText").textContent = loc === LOC.LOCATING ? "Finding you…" : state.me.label;
    $("locBtn").title = loc === LOC.PRECISE ? "Showing deals near you" : note || "Tap to use my location";
    $("locBtn").setAttribute("aria-label", loc === LOC.PRECISE ? "Near you. Tap to update your location" : `${state.me ? state.me.label : ""}. ${note || "Tap to use my location"}`);
    if (note) $("status").textContent = note;
    if (loc !== LOC.LOCATING) render();
  }
  function applyPosition(coords) {
    state.gps = coords;
    const near = nearestCity(coords);
    if (near.km <= NEAR_KM && near.city.id !== state.city && !state.cityLocked) setCity(near.city.id);
    if (km(coords, cityCentre()) <= NEAR_KM) setLocation(LOC.PRECISE, coords);
    else setLocation(LOC.AWAY, null, near.km > NEAR_KM ? `Foodish isn't in your area yet, so this is ${city().label}.` : `Showing ${city().label}.`);
  }
  function locate({ quiet = false } = {}) {
    if (!("geolocation" in navigator)) { setFallbackLocation("Location isn't available on this device."); return; }
    setLocation(LOC.LOCATING);
    navigator.geolocation.getCurrentPosition(
      (p) => applyPosition({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => setFallbackLocation(quiet ? null : `Location is off, so this is ${city().label}. Tap the place name to try again.`),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 5 * 60 * 1000 }
    );
  }
  function setFallbackLocation(note) {
    const last = store.get("lastLoc", null);
    if (last && km(last, cityCentre()) <= NEAR_KM) setLocation(LOC.LAST_KNOWN, last, note);
    else setLocation(LOC.DEFAULT, null, note);
  }
  function relocate() { if (state.gps) applyPosition(state.gps); else setFallbackLocation(); }

  // ---------- look (Daylight / Blue Hour) ----------
  // Decided by the sun in the shown city, unless the visitor picked one in Settings (or ?look= for testing).
  // Applied on load, on city change and when the tab comes back, never while someone is scrolling.
  function sunToday() {
    const c = city(), now = nowLocal();
    return SKY.sunTimes(c.center[0], c.center[1], now.date, (d) => DS.zonedNow(c.timezone, d));
  }
  function wantedLook() {
    if (state.lookForced) return state.lookForced;
    if (state.lookPref === "day" || state.lookPref === "night") return state.lookPref;
    return SKY.lookAt(nowLocal().mins, sunToday());
  }
  function applyLook() {
    const look = wantedLook();
    state.look = look;
    document.documentElement.dataset.look = look;
    if (!state.lookForced && state.lookPref === "auto") store.set("lastLook", look);
    document.querySelector('meta[name="theme-color"]').setAttribute("content", look === "day" ? "#E6E3DA" : "#121A3A");
    document.querySelectorAll("[data-look-pick]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lookPick === state.lookPref)));
    const sun = sunToday();
    if (state.lookPref === "auto" && sun) {
      const now = nowLocal().mins, dusk = sun.set - SKY.DUSK_LEAD_MINS;
      $("lookNote").textContent = look === "day"
        ? `Follows the sky in ${city().name}: Blue Hour starts at ${clockAP(dusk)} today.`
        : now < sun.rise ? `Follows the sky in ${city().name}: Daylight from ${clockAP(sun.rise)}.`
        : `Follows the sky in ${city().name}: Daylight again from about ${clockAP(sun.rise)} tomorrow.`;
    } else $("lookNote").textContent = state.lookPref === "auto" ? "" : "Fixed on this device. Pick Auto to follow the sky.";
    if (state.map) state.mapKey = ""; // re-style markers
  }

  // ---------- data ----------
  function setLoad(next, detail) {
    state.load = next;
    const status = $("status");
    if (next === LOAD.LOADING && !state.data) { status.textContent = "Loading today's deals…"; $("count").textContent = "–"; }
    if (next === LOAD.ERROR && !state.data) {
      status.innerHTML = `Couldn't load deals right now (${esc(detail)}). <button type="button" class="link-btn" id="retryLoad">Try again</button>`;
      $("retryLoad").onclick = () => load();
    }
    if (next === LOAD.READY) {
      status.textContent = "";
      const g = state.data.generated_at ? new Date(state.data.generated_at) : null;
      if (g && !isNaN(g)) {
        const ageH = (Date.now() - g.getTime()) / 36e5;
        const when = g.toLocaleString("en-CA", { timeZone: DS.zoneFor(city().timezone, g), dateStyle: "medium", timeStyle: "short" });
        $("updated").textContent = ageH > STALE_AFTER_H ? `Deals last checked ${when.replace(/\.$/, "")}, over a day ago, so some may have changed.` : `Last updated ${when.replace(/\.$/, "")}.`;
        if (ageH > STALE_AFTER_H) status.textContent = "These deals were last checked over a day ago.";
      }
      render();
    }
  }
  async function load() {
    if (state.load === LOAD.LOADING && state.data) return;
    const forCity = state.city;
    setLoad(LOAD.LOADING);
    try {
      const res = await fetch(city().dataUrl, { cache: "no-cache" });
      if (forCity !== state.city) return;
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
    setLoad(LOAD.READY);
  }

  // ---------- compute ----------
  // Deals that have ended today are left out on purpose: a visitor can't do anything with them.
  const RANK = { [ST.ON_NOW]: 0, [ST.LATER]: 1, [ST.ALL_DAY]: 2, [ST.SCHEDULED]: 1 };
  const inCat = (it) => !state.cat || (it.d.categories || []).includes(state.cat);
  // Every deal relevant to the chosen day within the radius (category not applied: the street and the
  // category counts need all of them). Search and the chains switch apply everywhere.
  function results() {
    if (!state.data || !state.me) return [];
    const now = nowLocal();
    // another day's tab is checked against that day's date, so a deal ending Nov 14 isn't on Tuesday the 17th
    const at = state.day == null ? now : { date: isoForDay(state.day), day: state.day, mins: null };
    const q = state.q.trim().toLowerCase();
    const out = [];
    for (const d of state.data.deals) {
      const status = DS.dealState(d, at);
      if (!(status in RANK)) continue;
      const branches = (d.restaurant_ids || []).map((id) => state.byId.get(id)).filter(Boolean)
        .map((r) => ({ r, dist: km(state.me, r) })).filter((x) => x.dist <= state.radius).sort((a, b) => a.dist - b.dist);
      if (!branches.length) continue;
      if (!state.chains && branches.every((b) => isChain(b.r.name))) continue;
      if (q) {
        const hay = `${d.title} ${d.description || ""} ${(d.categories || []).join(" ")} ${branches.map((b) => b.r.name).join(" ")}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      out.push({ d, status, nearest: branches[0], branches });
    }
    out.sort((a, b) => RANK[a.status] - RANK[b.status] || a.nearest.dist - b.nearest.dist);
    return out;
  }
  // Group deals by the place they're nearest at.
  function byPlace(items) {
    const m = new Map();
    for (const it of items) {
      const r = it.nearest.r;
      if (!m.has(r.id)) m.set(r.id, { r, dist: it.nearest.dist, items: [] });
      m.get(r.id).items.push(it);
    }
    return [...m.values()].sort((a, b) => a.dist - b.dist);
  }
  // Deals that aren't on the chosen day, by the soonest day they run (for "coming up this week").
  function comingUp(limit = 6) {
    if (!state.data || !state.me) return [];
    const now = nowLocal(), base = state.day == null ? now.day : state.day;
    const baseDate = state.day == null ? now.date : isoForDay(state.day);
    const seen = new Map(), hits = [];
    for (const d of state.data.deals) {
      if (state.cat && !(d.categories || []).includes(state.cat)) continue;
      const r = (d.restaurant_ids || []).map((id) => state.byId.get(id)).filter(Boolean)
        .map((x) => ({ r: x, dist: km(state.me, x) })).filter((x) => x.dist <= state.radius).sort((a, b) => a.dist - b.dist)[0];
      if (!r || (!state.chains && isChain(r.r.name))) continue;
      for (let i = 1; i < 7; i++) {
        const day = (base + i) % 7;
        const st = DS.dealState(d, { date: addDays(baseDate, i), day, mins: null });
        if (st === ST.SCHEDULED || st === ST.ALL_DAY) {
          const n = seen.get(r.r.id) || 0;
          if (n < 2) { hits.push({ i, day, d, r: r.r, dist: r.dist }); seen.set(r.r.id, n + 1); }
          break;
        }
      }
    }
    return hits.sort((a, b) => a.i - b.i || a.dist - b.dist).slice(0, limit);
  }

  // ---------- render ----------
  function render() {
    buildDays();
    writeLinkFilters();
    renderRadius();
    if (!state.data || !state.me) { renderHeroEmpty(); return; }
    const all = results();
    const today = state.day == null;
    const now = nowLocal();
    renderHero(all, today, now);
    renderCats(all, today);
    if (today) renderToday(all, now); else renderOtherDay(all);
    renderTimetable(all, today, now);
    if (state.map || state.view === "map" || isDesktop()) renderMap(all, today, now);
    syncAlertsUI();
  }
  const isDesktop = () => matchMedia("(min-width: 1024px)").matches;

  function renderHeroEmpty() {
    $("kickerTime").textContent = `${DAY_LONG[nowLocal().day]} · ${clockAP(nowLocal().mins)}`;
  }
  function renderRadius() {
    $("radiusBtn").textContent = radiusText(state.radius);
    $("radiusGroup").innerHTML = RADII.map((r) => `<button type="button" data-radius="${r}" aria-pressed="${r === state.radius}">${r >= 999 ? "ALL" : r === RADII[0] ? r + " KM" : r}</button>`).join("");
  }

  function renderHero(all, today, now) {
    const sel = all.filter(inCat);
    let n, word;
    if (today) {
      n = sel.filter((i) => i.status === ST.ON_NOW).length;
      word = n === 1 ? "deal on now" : "deals on now";
      $("kickerTime").textContent = `${DAY_LONG[now.day]} · ${clockAP(now.mins)}`;
    } else {
      n = sel.length;
      word = `${n === 1 ? "deal" : "deals"} on ${DAY_LONG[state.day]}`;
      $("kickerTime").textContent = `${DAY_LONG[state.day]} ${dateForDay(state.day).getUTCDate()}`;
    }
    $("count").textContent = String(n);
    $("countWord").textContent = word;
    // the street: 8 nearby places, those with something on now (then later today) first, drawn nearest first
    const score = (p) => (p.items.some((i) => inCat(i) && (!today || i.status === ST.ON_NOW)) ? 0 : p.items.some((i) => inCat(i) && i.status === ST.LATER) ? 1 : 2);
    const places = byPlace(all).sort((a, b) => score(a) - score(b) || a.dist - b.dist).slice(0, 8).sort((a, b) => a.dist - b.dist);
    const f = 0.6;  // the phone's street sits beside the count (styles.css "phone first screen"), so it's 60% height
    $("street").innerHTML = places.map((p, i) => {
      const its = p.items.slice().sort((a, b) => RANK[a.status] - RANK[b.status]).slice(0, 12);
      const rows = Math.ceil(its.length / 2);
      const h = Math.round(f * Math.min(150, Math.max(70, 54 + its.length * 16, 9 + rows * 22 + 6)));
      const slots = Math.max(2, Math.floor((h - 9 * f) / (22 * f)) * 2);
      let lit = 0;
      const wins = [];
      for (let k = 0; k < slots; k++) {
        const it = its[k];
        let cls = "";
        if (it && inCat(it)) {
          if (!today || it.status === ST.ON_NOW) { cls = "lit"; lit++; }
          else if (it.status === ST.LATER) cls = "w-later";
        }
        wins.push(`<span class="win ${cls}"${cls === "lit" ? ` style="animation-delay:${(0.15 + i * 0.12 + lit * 0.06).toFixed(2)}s"` : ""}></span>`);
      }
      const label = `${p.r.name}, ${kmShort(p.dist)} km: ${lit ? `${lit} ${lit === 1 ? "deal" : "deals"} ${today ? "on now" : ""}` : today ? "nothing on right now" : "no matching deals"}`;
      return `<a class="bldg" href="#p-${esc(p.r.id)}" style="height:${h}px" aria-label="${esc(label)}">${wins.join("")}</a>`;
    }).join("");
    $("streetKm").innerHTML = places.map((p) => `<span>${kmShort(p.dist)}</span>`).join("");
    // the sun over the street (Daylight only)
    const sun = sunToday();
    if (sun) {
      const f = Math.min(1, Math.max(0, (now.mins - sun.rise) / (sun.set - sun.rise)));
      const x = 6 + f * 346, y = (1 - f) ** 2 * 66 + 2 * (1 - f) * f * -40 + f ** 2 * 66;
      $("sun").style.left = `${(x / 358) * 100}%`; $("sun").style.top = `${Math.max(0, y - 15)}px`;
    }
    // one plain line
    const placesCount = byPlace(all).length;
    if (today) {
      const later = all.filter((i) => i.status === ST.LATER);
      const first = later.reduce((m, i) => Math.min(m, DS.startOf(i.d) ?? 1e9), 1e9);
      $("subline").textContent = all.length
        ? `${all.length} ${all.length === 1 ? "deal" : "deals"} left today at ${placesCount} ${placesCount === 1 ? "place" : "places"}.` +
          (later.length ? ` ${later.length} more switch on from ${shortAP(first)}.` : "")
        : `Nothing left today within ${radiusText(state.radius)}.`;
    } else {
      $("subline").textContent = `${placesCount} ${placesCount === 1 ? "place has" : "places have"} something on ${DAY_LONG[state.day]}.`;
    }
  }

  function renderCats(all, today) {
    const now = new Map(), total = new Map();
    for (const it of all) for (const c of it.d.categories || []) {
      total.set(c, (total.get(c) || 0) + 1);
      if (!today || it.status === ST.ON_NOW) now.set(c, (now.get(c) || 0) + 1);
    }
    let cats = [...total.keys()].sort((a, b) => (now.get(b) || 0) - (now.get(a) || 0) || total.get(b) - total.get(a)).slice(0, 10);
    if (state.cat && !cats.includes(state.cat)) cats.unshift(state.cat);
    const allN = today ? all.filter((i) => i.status === ST.ON_NOW).length : all.length;
    $("cats").innerHTML = [null, ...cats].map((c, i) => {
      const n = c ? now.get(c) || 0 : allN;
      return `${i ? '<span class="sep" aria-hidden="true">/</span>' : ""}<button type="button" data-cat="${esc(c || "")}" aria-pressed="${state.cat === c}" class="${n ? "" : "none"}">${esc(c ? c : "All")}${n ? `<sup>${n}</sup>` : ""}</button>`;
    }).join("");
  }

  // ----- today -----
  function renderToday(all, now) {
    const sel = all.filter(inCat);
    const on = sel.filter((i) => i.status === ST.ON_NOW);
    const later = sel.filter((i) => i.status === ST.LATER);
    const allDay = sel.filter((i) => i.status === ST.ALL_DAY);
    let top = "";
    if (!on.length) top += emptyNow(sel, later, allDay);
    if (on.length) top += onNowGroups(on, now);
    if (later.length && !state.onNow) top += laterSection(later, now);
    $("listTop").innerHTML = top;
    let bottom = "";
    if (allDay.length && !state.onNow) bottom += allDaySection(allDay);
    if (!sel.length) bottom += comingUpSection();
    $("listBottom").innerHTML = bottom;
  }
  function emptyNow(sel, later, allDay) {
    const what = state.cat ? `${esc(state.cat)} deals` : "deals";
    const big = sel.length ? `No ${what} on right now within ${esc(radiusText(state.radius))}.` : `Nothing left today within ${esc(radiusText(state.radius))}.`;
    const hint = later.length ? "See what switches on later, below." : allDay.length ? "The all-day deals below run whenever the restaurant is open." : "Try a wider distance, another day, or clear the search.";
    return `<div class="empty${later.length ? " has-later" : ""}"><p class="big">${big}</p><p>${hint}</p></div>`;
  }

  function onNowGroups(on, now) {
    const places = byPlace(on).map((p) => {
      const ends = p.items.map((i) => endsAt(i.d, now));
      const end = ends.every((e) => e == null) ? null : Math.min(...ends.filter((e) => e != null));
      return { ...p, end, left: end == null ? null : end - now.mins };
    });
    places.sort((a, b) => (a.end ?? 1e9) - (b.end ?? 1e9) || a.dist - b.dist);
    const groups = [];
    for (const p of places) {
      const key = p.end == null ? "close" : String(p.end);
      let g = groups[groups.length - 1];
      if (!g || g.key !== key) { g = { key, end: p.end, left: p.left, places: [] }; groups.push(g); }
      g.places.push(p);
    }
    let html = "", shown = 0;
    const cap = state.nowAll ? Infinity : 12;
    for (const g of groups) {
      if (shown >= cap) break;
      const title = g.end == null ? "Until close" : g.end % 1440 === 0 ? "Until midnight" : `Until ${clock(g.end)} ${ampm(g.end)}`;
      const meta = (g.left != null ? leftText(g.left) + " · " : "") + `${g.places.length} ${g.places.length === 1 ? "place" : "places"}`;
      const hot = g.left != null && g.left < 45;
      let blocks = "";
      for (let i = 0; i < g.places.length && shown < cap; i++) {
        const p = g.places[i];
        shown++;
        blocks += placeRow(p);
      }
      html += `<section class="group" aria-label="${esc(title)}"><div class="ghead"><h2>${esc(title)}</h2><span class="meta${hot ? " hot" : ""}">${esc(meta)}</span></div><div class="rows">${blocks}</div></section>`;
    }
    if (places.length > shown) html += `<button type="button" class="show-more" data-nowall>Show ${places.length - shown} more ${places.length - shown === 1 ? "place" : "places"} on now</button>`;
    return html;
  }
  // Pick a treatment from the shape of the place's deals; never the same one twice in a row.
  // "Something wrong?" link for a place: an email to Foodish with the listing filled in.
  const CONTACT = CFG.CONTACT_EMAIL || "yates@foodish.ca";
  function fixLink(r) {
    const subject = `Foodish listing: ${r.name}${r.address ? ` (${r.address})` : ""}`;
    const body = `Restaurant: ${r.name}\nAddress: ${r.address || ""}\nCity: ${city().name}\n\nWhat's wrong (or "please remove us"):\n`;
    return `mailto:${CONTACT}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  }
  function details(p) {
    const id = p.r.id, open = state.open.has(id);
    const more = p.items.length > 1 || p.items.some((i) => i.d.description);
    const srcs = [...new Set(p.items.map((i) => i.d.source_url || p.r.website).filter(Boolean))];
    const body = open ? `<div class="more-text">${p.items.map((i) => {
      const bits = [esc(i.d.title) + (i.d.price ? `, ${esc(i.d.price)}` : "") + ".", i.d.description ? esc(i.d.description) : "",
        `${esc(daysText(i.d))}, ${esc(hoursText(i.d))}.`, i.d.dine_in_only ? "Dine-in only." : "", esc(staleNote(i.d))].filter(Boolean).join(" ");
      return `<p>${bits}</p>`;
    }).join("")}${p.items[0].branches.length > 1 ? `<p>Also at ${p.items[0].branches.length - 1} more ${p.items[0].branches.length === 2 ? "location" : "locations"} within ${esc(radiusText(state.radius))}.</p>` : ""}${p.r.address ? `<p>${esc(p.r.address)}</p>` : ""}<p class="fix"><a href="${esc(fixLink(p.r))}">Something wrong, or own this restaurant? Tell us</a></p></div>` : "";
    return `${body}<div class="blk-foot">
      ${more ? `<button type="button" data-open="${esc(id)}" aria-expanded="${open}">${open ? "Less" : "What's included"}</button>` : "<span></span>"}
      <span class="acts"><button type="button" data-mapsel="${esc(id)}">On the map</button><a href="${directions(p.r)}" target="_blank" rel="noopener">Directions</a>${srcs[0] ? `<a href="${esc(srcs[0])}" target="_blank" rel="noopener">Their site</a>` : ""}</span></div>`;
  }
  // Every place on now gets the same plain row, nearest first within each end time: the list is for
  // comparing quickly, not for promoting one restaurant over another.
  function placeRow(p) {
    const deals = p.items.slice(0, 3).map((i) => (i.d.price ? `${i.d.price} ${what(i.d)}` : i.d.title)).join(" · ")
      + (p.items.length > 3 ? ` · +${p.items.length - 3} more` : "");
    return `<article class="nrow" id="p-${esc(p.r.id)}"><div class="nrow-main"><div class="txt"><h3 class="nm">${esc(p.r.name)}</h3>
      <p class="deals">${esc(deals)}</p></div><span class="km">${esc(kmShort(p.dist))} km</span></div>${details(p)}</article>`;
  }

  function laterSection(later, now) {
    const groups = new Map();
    for (const it of later) {
      const s = DS.startOf(it.d);
      if (!groups.has(s)) groups.set(s, []);
      groups.get(s).push(it);
    }
    const starts = [...groups.keys()].sort((a, b) => a - b);
    const lo = state.look === "day" ? 600 : 960;
    let from = Math.min(lo, Math.floor(now.mins / 60) * 60), to = from + (state.look === "day" ? 720 : 480);
    if (now.mins >= to) { from = Math.floor(now.mins / 60) * 60 - 60; to = from + 480; }
    const span = to - from, pos = (m) => Math.min(100, Math.max(0, ((m - from) / span) * 100));
    const railItems = byPlace(later).slice(0, 8);
    const bars = railItems.map((p, k) => {
      const s = Math.min(...p.items.map((i) => DS.startOf(i.d)));
      const ends = p.items.map((i) => { const st = DS.startOf(i.d), e = DS.toMins(i.d.end); return e == null ? to : e <= st ? e + 1440 : e; });
      const e = Math.max(...ends);
      return `<span class="r" style="left:${pos(s)}%;width:${Math.max(1, pos(e) - pos(s))}%;top:${4 + k * 18}px"></span>`;
    }).join("");
    const ticks = [];
    for (let t = from; t <= to; t += 120) ticks.push(`<span style="left:${pos(t)}%">${t === from || t === to ? shortAP(t).toUpperCase() : clock(t).replace(":00", "")}</span>`);
    const first = starts[0], inMins = first - now.mins;
    const rows = (its) => byPlace(its).map((p) => {
      const d = p.items[0].d;
      const sub = [p.items.map((i) => i.d.price ? `${i.d.price} ${what(i.d)}` : i.d.title).slice(0, 2).join(", "), hoursText(d), kmShort(p.dist) + " km", d.dine_in_only ? "dine-in" : ""].filter(Boolean).join(" · ");
      return { p, sub };
    });
    const hero = rows(groups.get(first)).map(({ p, sub }) => `<div class="lrow" id="p-${esc(p.r.id)}"><div class="txt"><p class="nm">${esc(p.r.name)}</p><p class="sub">${esc(sub)}</p></div>
      <button type="button" class="outline-btn" data-cal="${esc(p.items[0].d.id)}" data-place="${esc(p.r.id)}"><svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16v14H4zM4 10h16M8 3v4M16 3v4"/></svg>Add to calendar</button></div>`).join("");
    const cap = state.laterAll ? Infinity : 5;
    const rest = starts.slice(1, cap).map((s) => `<div class="start-group"><span class="outline-num" aria-hidden="true">${clock(s)}</span><div class="places">
      <span class="when">${clock(s)} ${ampm(s).toUpperCase()} · IN ${leftText(s - now.mins)}</span>
      ${rows(groups.get(s)).slice(0, cap).map(({ p, sub }) => `<div class="place" id="p-${esc(p.r.id)}"><p class="nm">${esc(p.r.name)}</p><p class="sub">${esc(sub)}</p><button type="button" class="cal" data-cal="${esc(p.items[0].d.id)}" data-place="${esc(p.r.id)}">Add to calendar</button></div>`).join("")}
      </div></div>`).join("");
    const hidden = starts.slice(1, 5).reduce((n, s) => n + Math.max(0, rows(groups.get(s)).length - 5), 0);
    const more = !state.laterAll && (starts.length > 5 || hidden)
      ? `<button type="button" class="show-all" data-laterall>Show everything later today${starts.length > 5 ? ` (${starts.length - 5} more start times)` : ""}</button>` : "";
    return `<section class="later" aria-label="Later today"><p class="eyebrow">${now.mins < 720 ? "LATER TODAY" : state.look === "day" ? "SWITCHING ON LATER" : "LATER TONIGHT"}</p>
      <div class="later-hero"><p class="outline-num${clock(first).length > 4 ? " long" : ""}"><span class="t">${clock(first)}</span><span class="ap">${ampm(first).toUpperCase()}</span></p><p class="in">switches on<br>in ${leftText(inMins).toLowerCase()}</p></div>
      <div class="rail-wrap" aria-hidden="true"><div class="rail" style="height:${8 + railItems.length * 18}px">${bars}<span class="needle" style="left:${pos(now.mins)}%"></span></div><div class="rail-axis">${ticks.join("")}</div></div>
      <div>${hero}</div>${rest}${more}</section>`;
  }

  function allDayRows(items) {
    return byPlace(items).flatMap((p) => p.items.map((i) => ({ i, p })))
      .sort((a, b) => a.p.dist - b.p.dist)
      .map(({ i, p }) => {
        const f = figure(i.d);
        const where = p.r.name + (i.branches.length > 1 ? ` · ${i.branches.length} nearby` : "") + (i.d.dine_in_only ? " · dine-in" : "");
        return `<div class="arow" id="p-${esc(p.r.id)}"><span class="num${f.word ? " word" : ""}">${esc(f.word && f.text.length > 12 ? "DEAL" : f.text)}</span>
          <span class="txt"><span class="what">${esc(i.d.price ? i.d.title : i.d.title)}${i.d.price_unit ? ` <small>(${esc(i.d.price_unit)})</small>` : ""}</span><span class="where">${esc(where)}</span></span><span class="d">${kmShort(p.dist)}</span></div>`;
      });
  }
  function allDaySection(items) {
    const rows = allDayRows(items);
    const shown = state.allDayAll ? rows : rows.slice(0, 6);
    return `<section class="allday" aria-label="All day"><div class="ghead2"><h2>ALL DAY</h2><span>${rows.length} ${rows.length === 1 ? "deal" : "deals"}, no clock to watch</span></div>
      <div class="rows">${shown.join("")}</div>${rows.length > 6 && !state.allDayAll ? `<button type="button" class="show-all" data-allday>Show all ${rows.length}</button>` : ""}</section>`;
  }
  function comingUpSection() {
    const hits = comingUp();
    if (!hits.length) return "";
    return `<section class="later" aria-label="Coming up this week"><p class="eyebrow">COMING UP THIS WEEK</p><ul class="soon">${hits.map((h) =>
      `<li><button type="button" data-day="${h.day}"><span class="when">${DAY_LONG[h.day].slice(0, 3)}</span><span class="what">${esc(h.d.title)}${h.d.price ? " · " + esc(h.d.price) : ""}<small>${esc(h.r.name)} · ${kmShort(h.dist)} km</small></span></button></li>`).join("")}</ul></section>`;
  }

  // ----- another day: a schedule, not countdowns -----
  function renderOtherDay(all) {
    const sel = all.filter(inCat);
    const timed = sel.filter((i) => i.status === ST.SCHEDULED).sort((a, b) => (DS.startOf(a.d) ?? -1) - (DS.startOf(b.d) ?? -1) || a.nearest.dist - b.nearest.dist);
    const untimed = sel.filter((i) => i.status === ST.ALL_DAY);
    const row = (i) => {
      const s = DS.toMins(i.d.start), e = DS.toMins(i.d.end);
      const t = s != null ? shortAP(s).toUpperCase() : e != null ? "TILL " + shortAP(e).toUpperCase() : "";
      return `<div class="srow" id="p-${esc(i.nearest.r.id)}"><span class="t">${esc(t)}</span><div class="txt"><p class="nm">${esc(i.nearest.r.name)}</p><p class="sub">${esc(i.d.title)} · ${esc(hoursText(i.d))} · ${kmShort(i.nearest.dist)} km${i.d.dine_in_only ? " · dine-in" : ""}</p></div>${i.d.price ? `<span class="p">${esc(i.d.price)}</span>` : ""}</div>`;
    };
    const part = (title, its) => {
      if (!its.length) return "";
      const key = `${state.day}|${title}`, all = state.schedAll.has(key);
      const more = its.length > 40 && !all ? `<button type="button" class="show-all" data-schedall="${esc(key)}">Show all ${its.length}</button>` : "";
      return `<section class="group"><div class="ghead"><h2>${title}</h2><span class="meta">${its.length} ${its.length === 1 ? "deal" : "deals"}</span></div><div class="sched">${(all ? its : its.slice(0, 40)).map(row).join("")}</div>${more}</section>`;
    };
    const aft = timed.filter((i) => (DS.startOf(i.d) ?? 0) < 17 * 60), eve = timed.filter((i) => (DS.startOf(i.d) ?? 0) >= 17 * 60);
    $("listTop").innerHTML = (sel.length ? "" : `<div class="empty"><p class="big">Nothing posted for ${DAY_LONG[state.day]} within ${esc(radiusText(state.radius))}.</p><p>Try a wider distance or another day.</p></div>`)
      + part("Daytime", aft) + part("Evening", eve);
    $("listBottom").innerHTML = (untimed.length ? allDaySection(untimed) : "") + (sel.length ? "" : comingUpSection());
  }

  // ----- desktop: the evening (or day) as a timetable -----
  function renderTimetable(all, today, now) {
    const el = $("timetable");
    if (!isDesktop()) { el.innerHTML = ""; return; }
    const sel = all.filter(inCat).filter((i) => today ? i.status === ST.ON_NOW || i.status === ST.LATER : i.status === ST.SCHEDULED);
    const day = state.look === "day";
    let from = day ? 600 : 960, to = day ? 1320 : 1440;
    if (today && now.mins < from) { from = Math.floor(now.mins / 60) * 60; to = from + (day ? 720 : 480); }
    if (today && now.mins > to) { from = Math.floor(now.mins / 60) * 60 - 120; to = from + 480; }
    const span = to - from, pos = (m) => Math.min(100, Math.max(0, ((m - from) / span) * 100));
    const places = byPlace(sel).map((p) => {
      const on = p.items.some((i) => i.status === ST.ON_NOW);
      const s = Math.min(...p.items.map((i) => DS.startOf(i.d) ?? from));
      return { ...p, on, s };
    }).sort((a, b) => (b.on - a.on) || a.s - b.s || a.dist - b.dist).slice(0, 10);
    if (!places.length) { el.innerHTML = `<p class="tt-empty">${today ? "Nothing on now or later today" : "Nothing timed on this day"} within ${esc(radiusText(state.radius))}.</p>`; return; }
    let k = 0;
    const rows = places.map((p) => {
      const spans = new Map();
      for (const i of p.items) {
        const s = DS.startOf(i.d) ?? 0, e0 = DS.toMins(i.d.end);
        const e = e0 == null ? to : e0 <= s ? e0 + 1440 : e0;
        const key = s + "-" + e;
        if (!spans.has(key)) spans.set(key, { s, e, items: [], open: e0 == null, on: i.status === ST.ON_NOW });
        spans.get(key).items.push(i);
      }
      const bars = [...spans.values()].map((b, idx, arr) => {
        const prices = b.items.map((i) => priceValue(i.d.price)).filter((x) => x != null);
        const label = b.items.length > 1
          ? (prices.length > 1 ? `${b.items.find((i) => priceValue(i.d.price) === Math.min(...prices)).d.price} to ${b.items.find((i) => priceValue(i.d.price) === Math.max(...prices)).d.price} · ` : "") + `${b.items.length} deals`
          : b.items[0].d.price ? `${b.items[0].d.price} ${what(b.items[0].d)}` : b.items[0].d.title;
        const h = arr.length > 1 ? 12 : 24, top = arr.length > 1 ? 2 + idx * 14 : 3;
        const left = Math.max(1, (b.e - b.s) / 60 > 0 ? 0 : 0);
        const cls = !b.on ? " is-later" : (b.e - now.mins > 60 ? " glow" : "");
        return `<span class="tt-bar${cls}" style="left:${pos(b.s)}%;width:${Math.max(left, pos(b.e) - pos(b.s))}%;top:${top}px;height:${h}px;${arr.length > 1 ? "font-size:11px;" : ""}animation-delay:${(0.15 + k++ * 0.06).toFixed(2)}s">${esc(label)}${b.open ? " →" : ""}</span>`;
      }).join("");
      return `<div class="tt-row${p.on ? "" : " is-later"}"><span class="nm">${esc(p.r.name)}</span><div class="tt-track">${bars}</div></div>`;
    }).join("");
    const ticks = [];
    for (let t = from; t <= to; t += 120) ticks.push(`<span style="left:${pos(t)}%">${t === from || t === to ? shortAP(t).toUpperCase() : clock(t).replace(":00", "")}</span>`);
    const sun = sunToday();
    const sunset = today && sun && sun.set > from && sun.set < to ? `<div class="sunset" style="left:${pos(sun.set)}%"></div><span class="sunset-flag" style="left:calc(${pos(sun.set)}% + 6px)">SUNSET ${clock(sun.set)}</span>` : "";
    const needle = today ? `<div class="needle" style="left:${pos(now.mins)}%"></div><span class="flag" style="left:calc(${pos(now.mins)}% + 6px)">${clock(now.mins)} NOW</span>` : "";
    el.innerHTML = `<div class="tt-rows" role="img" aria-label="Timetable of ${places.length} places from ${shortAP(from)} to ${shortAP(to)}">${rows}<div class="tt-marks" aria-hidden="true">${needle}${sunset}</div></div><div class="tt-axis" aria-hidden="true">${ticks.join("")}</div>`;
  }

  // ---------- days ----------
  function dateForDay(d) {
    const now = nowLocal();
    const base = new Date(now.date + "T12:00:00Z");
    return new Date(base.getTime() + (((d - now.day) + 7) % 7) * 864e5);
  }
  const addDays = (iso, n) => new Date(Date.parse(iso + "T12:00:00Z") + n * 864e5).toISOString().slice(0, 10);
  const isoForDay = (d) => dateForDay(d).toISOString().slice(0, 10); // "2026-11-17" for the coming Tuesday
  function buildDays() {
    const { day: today } = nowLocal();
    const html = [];
    for (let i = 0; i < 7; i++) {
      const d = (today + i) % 7, sel = state.day == null ? i === 0 : state.day === d;
      const dt = dateForDay(d);
      html.push(`<button type="button" data-day="${d}" aria-pressed="${sel}" aria-label="${i === 0 ? "Today, " : i === 1 ? "Tomorrow, " : ""}${DAY_LONG[d]} ${dt.getUTCDate()}"><span class="l">${DAY_LONG[d][0]}</span><span class="n">${dt.getUTCDate()}</span></button>`);
    }
    $("days").innerHTML = html.join("");
    $("daysDesk").innerHTML = html.join("");
  }

  // ---------- map ----------
  // The map library (Leaflet, in vendor/) is only fetched when a map is first shown, so a slow
  // connection can never hold up the deals list.
  let leaflet = null;
  function loadLeaflet() {
    if (leaflet) return leaflet;
    leaflet = new Promise((resolve, reject) => {
      const css = document.createElement("link");
      css.rel = "stylesheet"; css.href = "vendor/leaflet/leaflet.css";
      document.head.insertBefore(css, document.querySelector('link[href="styles.css"]')); // ours still win
      const js = document.createElement("script");
      js.src = "vendor/leaflet/leaflet.js"; js.async = true;
      js.onload = () => resolve(); js.onerror = () => { leaflet = null; reject(new Error("map library didn't load")); };
      document.head.appendChild(js);
    });
    return leaflet;
  }
  function renderMap(all, today, now) {
    if (!window.L) {
      loadLeaflet().then(render, () => { $("map").innerHTML = '<p class="map-note">The map couldn\'t load. Check your connection and reload.</p>'; });
      return;
    }
    if (!state.me) return;
    if (!state.map) {
      state.map = L.map("map", { zoomControl: true, attributionControl: true }).setView([state.me.lat, state.me.lng], 14);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(state.map);
      state.layer = L.layerGroup().addTo(state.map);
      state.map.on("click", () => { if (state.sel) { state.sel = null; render(); } });
    }
    state.layer.clearLayers();
    if (state.meMarker) state.meMarker.remove();
    state.meMarker = L.marker([state.me.lat, state.me.lng], { icon: L.divIcon({ className: "me-dot", iconSize: [14, 14] }), title: "You", keyboard: false, interactive: false }).addTo(state.map);
    if (state.radius < 999) L.circle([state.me.lat, state.me.lng], { radius: state.radius * 1000, color: state.look === "day" ? "#4B5580" : "#8E9AC9", weight: 1.25, dashArray: "4 6", fill: false, interactive: false }).addTo(state.layer);
    const sel = all.filter(inCat);
    const places = byPlace(sel);
    if (isDesktop() && !state.sel) {
      const firstOn = places.find((p) => p.items.some((i) => i.status === ST.ON_NOW));
      if (firstOn) state.sel = firstOn.r.id;
    }
    const bounds = [[state.me.lat, state.me.lng]];
    for (const p of places) {
      const onN = p.items.filter((i) => i.status === ST.ON_NOW).length;
      const later = p.items.filter((i) => i.status === ST.LATER);
      const kind = !today ? "now" : onN ? "now" : later.length ? "later" : "all";
      const isSel = state.sel === p.r.id;
      const size = kind === "now" ? (isSel ? 38 : 30) : kind === "later" ? (isSel ? 30 : 24) : 12;
      const glyph = kind === "now" ? String(today ? onN : p.items.length) : kind === "later" ? String(((Math.floor(Math.min(...later.map((i) => DS.startOf(i.d))) / 60) + 11) % 12) + 1) : "";
      const icon = L.divIcon({ className: `mk mk-${kind}${isSel ? " mk-sel" : ""}`, html: glyph, iconSize: [size, size] });
      const label = `${p.r.name}, ${kmShort(p.dist)} km: ${kind === "now" ? `${glyph} ${today ? "on now" : "deals"}` : kind === "later" ? "starts later" : "all-day deals"}`;
      const m = L.marker([p.r.lat, p.r.lng], { icon, title: label, alt: label, keyboard: kind === "now" || kind === "later", zIndexOffset: isSel ? 1000 : kind === "now" ? 500 : kind === "later" ? 200 : 0 }).addTo(state.layer);
      m.on("click", (e) => { L.DomEvent.stopPropagation(e); state.sel = p.r.id; render(); });
      if (kind === "now" || kind === "later") bounds.push([p.r.lat, p.r.lng]);
    }
    const key = `${state.city}|${state.radius}|${state.me.lat.toFixed(3)},${state.me.lng.toFixed(3)}|${state.cat}|${state.day}`;
    if (key !== state.mapKey) {
      state.mapKey = key;
      if (bounds.length > 1) state.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
      else state.map.setView([state.me.lat, state.me.lng], 14);
    }
    renderSheet(places, today, now);
    setTimeout(() => state.map && state.map.invalidateSize(), 60);
  }
  function renderSheet(places, today, now) {
    const sheet = $("sheet");
    const p = places.find((x) => x.r.id === state.sel);
    if (!p) { sheet.hidden = true; return; }
    const on = p.items.filter((i) => i.status === ST.ON_NOW);
    const show = (today && on.length ? on : p.items).slice(0, 4);
    const ends = on.map((i) => endsAt(i.d, now)).filter((e) => e != null);
    const left = ends.length ? Math.min(...ends) - now.mins : null;
    const later = today && !on.length && p.items.some((i) => i.status === ST.LATER);
    const meta = `${kmCaps(p.dist)} · ` + (today ? (on.length ? (left == null ? "ON UNTIL CLOSE" : left < 180 ? leftText(left) + " LEFT" : "UNTIL " + shortAP(left + now.mins).toUpperCase()) : later ? "STARTS " + shortAP(Math.min(...p.items.filter((i) => i.status === ST.LATER).map((i) => DS.startOf(i.d)))).toUpperCase() : "ALL DAY") : DAY_LONG[state.day].toUpperCase());
    const src = show.map((i) => i.d.source_url).find(Boolean) || p.r.website;
    sheet.className = "sheet" + (later ? " is-later" : left != null && left > 60 ? " glow" : "");
    sheet.innerHTML = `<span class="grab" aria-hidden="true"></span><button type="button" class="close" data-closesheet aria-label="Close"><svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
      <h2>${esc(p.r.name)}</h2><p class="meta">${esc(meta)}</p>
      <div class="lines${show.length > 2 ? " many" : ""}">${show.map((i) => `<p class="line">${fig(i.d)}${under(i.d)}</p>`).join("")}</div>
      <p class="when">${esc(daysText(show[0].d))}, ${esc(hoursText(show[0].d))}${show[0].d.dine_in_only ? ". Dine-in only" : ""}${p.items.length > show.length ? `. ${p.items.length - show.length} more here` : ""}.</p>
      <div class="btns"><a class="solid" href="${directions(p.r)}" target="_blank" rel="noopener">Directions</a>${src ? `<a class="ghost" href="${esc(src)}" target="_blank" rel="noopener">Their website</a>` : ""}</div>
      <p class="fix"><a href="${esc(fixLink(p.r))}">Something wrong? Tell us</a></p>`;
    sheet.hidden = false;
  }

  // ---------- calendar ----------
  function addToCalendar(dealId, placeId) {
    const d = state.data.deals.find((x) => x.id === dealId), r = state.byId.get(placeId);
    if (!d || !r) return;
    const now = nowLocal(), s = DS.startOf(d), e0 = DS.toMins(d.end);
    const e = e0 == null ? s + 120 : e0 <= s ? e0 + 1440 : e0;
    const off = utcOffsetMins();
    const y = +now.date.slice(0, 4), mo = +now.date.slice(5, 7) - 1, da = +now.date.slice(8, 10);
    const stamp = (mins) => new Date(Date.UTC(y, mo, da, 0, mins - off)).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
    const text = (t) => String(t || "").replace(/[\\,;]/g, (c) => "\\" + c).replace(/\n/g, " ");
    const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Foodish//EN", "BEGIN:VEVENT", `UID:${d.id}-${now.date}@foodish.ca`, `DTSTAMP:${stamp(now.mins)}`,
      `DTSTART:${stamp(s)}`, `DTEND:${stamp(e)}`, `SUMMARY:${text(`${d.title} at ${r.name}`)}`,
      `DESCRIPTION:${text([d.price, d.description, d.source_url].filter(Boolean).join(" · "))}`, `LOCATION:${text(r.address || r.name)}`,
      "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY", `DESCRIPTION:${text(d.title)}`, "END:VALARM", "END:VEVENT", "END:VCALENDAR"].join("\r\n");
    const url = URL.createObjectURL(new Blob([ics], { type: "text/calendar" }));
    const a = document.createElement("a");
    a.href = url; a.download = `foodish-${d.id}.ics`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  // ---------- alerts ----------
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isStandalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const pushSupported = "serviceWorker" in navigator && "PushManager" in window && CFG.API_URL && CFG.VAPID_PUBLIC_KEY;
  // Alerts state, derived: "on" only if the visitor asked for alerts AND the browser still allows notifications.
  function alertsState() {
    const wanted = store.get("nearbyWatch", false) || store.get("pushDaily", false);
    const perm = "Notification" in window ? Notification.permission : "unsupported";
    if (!wanted) return "off";
    return perm === "granted" ? "on" : "blocked";
  }
  function syncAlertsBadge() {
    const st = alertsState();
    $("alertsDot").hidden = st !== "on";
    $("alertsBtn").setAttribute("aria-label", st === "on" ? "Deal alerts: on" : st === "blocked" ? "Deal alerts: blocked in browser settings" : "Deal alerts: off");
    if (st !== "on") stopWatch();
  }
  function syncAlertsUI() {
    const st = alertsState();
    $("nearbyWatch").setAttribute("aria-pressed", String(store.get("nearbyWatch", false) && st === "on"));
    $("pushDaily").setAttribute("aria-pressed", String(store.get("pushDaily", false) && st === "on"));
    $("pushBlock").hidden = !(pushSupported || (isIOS && !isStandalone && CFG.VAPID_PUBLIC_KEY));
    $("iosHint").hidden = !(isIOS && !isStandalone && CFG.VAPID_PUBLIC_KEY);
    $("pushDaily").disabled = isIOS && !isStandalone;
    if (st === "blocked" && !$("alertsMsg").textContent) $("alertsMsg").textContent = "Notifications are blocked for this site. You can allow them in your browser settings.";
  }
  async function registerSW() {
    if (!("serviceWorker" in navigator)) return null;
    try { return await navigator.serviceWorker.register("sw.js"); } catch { return null; }
  }
  async function setAlerts(change) {
    const msg = $("alertsMsg");
    msg.textContent = "";
    const want = { nearbyWatch: store.get("nearbyWatch", false), pushDaily: store.get("pushDaily", false), ...change };
    if ((want.nearbyWatch || want.pushDaily) && "Notification" in window && Notification.permission !== "granted") {
      const p = await Notification.requestPermission();
      if (p !== "granted") { msg.textContent = "Notifications are blocked for this site. You can allow them in your browser settings."; syncAlertsUI(); return; }
    }
    if (!("Notification" in window) && (want.nearbyWatch || want.pushDaily)) { msg.textContent = "This browser can't show notifications."; return; }
    const cats = $("pushCats").value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
    store.set("nearbyWatch", want.nearbyWatch); store.set("pushDaily", want.pushDaily); store.set("pushCats", cats.join(", "));
    want.nearbyWatch ? startWatch() : stopWatch();
    if (pushSupported && "pushDaily" in change) {
      try {
        const reg = await registerSW(); await navigator.serviceWorker.ready;
        let sub = await reg.pushManager.getSubscription();
        if (want.pushDaily) {
          if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(CFG.VAPID_PUBLIC_KEY) });
          const spot = state.me || cityCentre();
          await fetch(CFG.API_URL + "/subscribe", { method: "POST", headers: { "content-type": "application/json" },
            body: JSON.stringify({ subscription: sub, lat: +spot.lat.toFixed(3), lng: +spot.lng.toFixed(3), radius_km: Math.min(state.radius, 25), categories: cats }) });
        } else if (sub) {
          await fetch(CFG.API_URL + "/unsubscribe", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ endpoint: sub.endpoint }) });
          await sub.unsubscribe();
        }
      } catch { msg.textContent = "Couldn't set up daily alerts right now. Please try again later."; store.set("pushDaily", false); }
    }
    syncAlertsBadge(); syncAlertsUI();
  }
  function b64ToU8(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }
  function startWatch() {
    if (state.watchId != null || !("geolocation" in navigator)) return;
    state.watchId = navigator.geolocation.watchPosition((p) => {
      state.gps = { lat: p.coords.latitude, lng: p.coords.longitude };
      if (km(state.gps, cityCentre()) > NEAR_KM) return;
      state.me = { ...state.gps, label: "Near you" };
      checkNearby();
    }, () => {}, { enableHighAccuracy: false, maximumAge: 60000 });
  }
  function stopWatch() { if (state.watchId != null) navigator.geolocation.clearWatch(state.watchId); state.watchId = null; }
  async function checkNearby() {
    if (!state.data || !("Notification" in window) || Notification.permission !== "granted") return;
    const now = nowLocal(), todayKey = now.date, seen = store.get("seen", {});
    for (const d of state.data.deals) {
      if (DS.dealState(d, now) !== ST.ON_NOW) continue;
      for (const id of d.restaurant_ids || []) {
        const r = state.byId.get(id); if (!r || km(state.me, r) > 0.4) continue;
        const key = `${todayKey}:${d.id}`;
        if (seen[key]) continue;
        seen[key] = 1;
        const reg = await navigator.serviceWorker?.getRegistration();
        const body = `${r.name} is ${Math.round(km(state.me, r) * 1000)} m away${d.price ? " · " + d.price : ""}`;
        if (reg) reg.showNotification(`Deal nearby: ${d.title}`, { body, icon: "icon-192.png", tag: key, data: { url: location.href } });
        else new Notification(`Deal nearby: ${d.title}`, { body });
      }
    }
    Object.keys(seen).forEach((k) => { if (!k.startsWith(todayKey)) delete seen[k]; });
    store.set("seen", seen);
  }

  // ---------- submit ----------
  async function submitDeal(ev) {
    ev.preventDefault();
    const f = $("submitForm");
    if (!f.reportValidity()) return;
    const body = Object.fromEntries(new FormData(f).entries());
    if (CFG.TIPS !== "service" || !CFG.API_URL) {
      location.href = `mailto:${CONTACT}?subject=${encodeURIComponent("Deal for Foodish: " + body.restaurant)}&body=${encodeURIComponent(`${body.deal}\n${body.url || ""}`)}`;
      $("submitMsg").textContent = "Your email app should open with the deal filled in.";
      return;
    }
    const btn = $("sendSubmit");
    if (btn.disabled) return;
    btn.disabled = true; $("submitMsg").textContent = "Sending…";
    try {
      const r = await fetch(CFG.API_URL + "/submit", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw 0;
      $("submitMsg").textContent = "Thanks! We'll check it and add it soon.";
      f.reset();
    } catch { $("submitMsg").textContent = "Couldn't send that. Please try again."; }
    finally { btn.disabled = false; }
  }

  // ---------- address bar ----------
  //   /?city=vancouver  /?cat=wings  /?day=3 (0 = Sunday)  /?now=1  /?view=map  /?km=2  /?q=tacos  /?look=day (testing)
  function readLinkFilters() {
    const p = new URLSearchParams(location.search);
    const linkCity = (p.get("city") || "").trim().toLowerCase();
    if (cityById(linkCity)) { state.city = linkCity; state.cityLocked = true; }
    const cat = (p.get("cat") || "").trim().toLowerCase();
    if (cat) state.cat = cat;
    const day = parseInt(p.get("day"), 10);
    if (day >= 0 && day <= 6) state.day = day === nowLocal().day ? null : day;
    if (p.get("now") === "1") state.onNow = true;
    if (p.get("view") === "map") state.view = "map";
    const radius = parseFloat(p.get("km"));
    if (RADII.includes(radius)) state.radius = radius;
    if (p.get("q")) state.q = p.get("q");
    const look = p.get("look");
    if (look === "day" || look === "night") state.lookForced = look;
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
    if (state.lookForced) p.set("look", state.lookForced);
    const url = location.pathname + (p.toString() ? "?" + p : "") + location.hash;
    if (url === location.pathname + location.search + location.hash) return;
    // Switching to the map, another day or a category is a step the back button should undo;
    // typing a search or changing the distance just updates the address.
    const step = `${state.view}|${state.day}|${state.cat}`;
    if (state.histStep != null && step !== state.histStep) history.pushState({ foodish: step }, "", url);
    else history.replaceState(history.state, "", url);
    state.histStep = step;
  }
  // Back / forward inside the app: put the view, day and category back the way the address says.
  function onPopState() {
    const p = new URLSearchParams(location.search);
    const day = parseInt(p.get("day"), 10);
    state.day = day >= 0 && day <= 6 && day !== nowLocal().day ? day : null;
    state.cat = (p.get("cat") || "").trim().toLowerCase() || null;
    state.sel = null; state.nowAll = false;
    state.histStep = `${p.get("view") === "map" ? "map" : "list"}|${state.day}|${state.cat}`;
    setView(p.get("view") === "map" ? "map" : "list");
  }

  // ---------- view ----------
  function setView(v) {
    state.view = v;
    document.body.classList.toggle("view-map", v === "map");
    document.querySelector(".cats-row").classList.toggle("on-map", v === "map");
    $("listViewBtn").setAttribute("aria-pressed", String(v === "list"));
    $("mapViewBtn").setAttribute("aria-pressed", String(v === "map"));
    if (v === "list" && !isDesktop()) state.sel = null;
    render();
    if (v === "map") window.scrollTo(0, 0);
  }

  // ---------- wire up ----------
  function init() {
    const lastCity = store.get("city", null);
    if (cityById(lastCity)) state.city = lastCity;
    readLinkFilters();
    if (!cityById(lastCity) && !state.cityLocked) state.city = cityFromClock();
    window.addEventListener("popstate", onPopState);
    $("cityName").textContent = city().name;
    setCity(state.city);
    applyLook();
    $("search").value = state.q; $("searchDesk").value = state.q;
    if (state.q) $("searchRow").hidden = false;
    $("chainsToggle").setAttribute("aria-pressed", String(state.chains));
    $("pushCats").value = store.get("pushCats", "");

    $("cityBtn").onclick = () => { const open = $("cityPanel").hidden; $("cityPanel").hidden = !open; $("cityBtn").setAttribute("aria-expanded", String(open)); };
    $("cityPanel").onclick = (e) => {
      const b = e.target.closest("[data-city]"); if (!b) return;
      $("cityPanel").hidden = true; $("cityBtn").setAttribute("aria-expanded", "false");
      setCity(b.dataset.city, { locked: true }); relocate();
    };
    $("searchBtn").onclick = () => { const open = $("searchRow").hidden; $("searchRow").hidden = !open; $("searchBtn").setAttribute("aria-expanded", String(open)); if (open) $("search").focus(); };
    const onSearch = (e) => { state.q = e.target.value; ($("search") === e.target ? $("searchDesk") : $("search")).value = state.q; render(); };
    $("search").oninput = onSearch; $("searchDesk").oninput = onSearch;
    $("locBtn").onclick = () => locate();
    $("radiusBtn").onclick = () => { state.radius = RADII[(RADII.indexOf(state.radius) + 1) % RADII.length]; store.set("radius", state.radius); state.mapKey = ""; render(); };
    $("radiusGroup").onclick = (e) => { const b = e.target.closest("[data-radius]"); if (!b) return; state.radius = +b.dataset.radius; store.set("radius", state.radius); state.mapKey = ""; render(); };
    $("cats").onclick = (e) => { const b = e.target.closest("[data-cat]"); if (!b) return; state.cat = b.dataset.cat || null; state.sel = null; state.nowAll = false; render(); };
    const onDay = (e) => { const b = e.target.closest("[data-day]"); if (!b) return; const d = +b.dataset.day; state.day = d === nowLocal().day ? null : d; state.sel = null; render(); };
    $("days").onclick = onDay; $("daysDesk").onclick = onDay;
    $("listViewBtn").onclick = () => setView("list");
    $("mapViewBtn").onclick = () => setView("map");
    $("alertsBtn").onclick = () => { if (state.view === "map") setView("list"); $("alerts").scrollIntoView({ behavior: "smooth", block: "center" }); $("nearbyWatch").focus({ preventScroll: true }); };
    $("nearbyWatch").onclick = () => setAlerts({ nearbyWatch: $("nearbyWatch").getAttribute("aria-pressed") !== "true" });
    $("pushDaily").onclick = () => setAlerts({ pushDaily: $("pushDaily").getAttribute("aria-pressed") !== "true" });
    $("pushCats").onchange = () => { if (store.get("pushDaily", false)) setAlerts({ pushDaily: true }); else store.set("pushCats", $("pushCats").value); };
    $("submitForm").onsubmit = submitDeal;
    $("chainsToggle").onclick = () => { state.chains = !state.chains; store.set("chains", state.chains); $("chainsToggle").setAttribute("aria-pressed", String(state.chains)); render(); };
    $("lookSeg").onclick = (e) => { const b = e.target.closest("[data-look-pick]"); if (!b) return; state.lookPref = b.dataset.lookPick; state.lookForced = null; store.set("look", state.lookPref); applyLook(); render(); };
    // Everything drawn by render() uses data-* buttons, handled here once.
    document.addEventListener("click", (e) => {
      const t = e.target.closest("[data-open],[data-mapsel],[data-allday],[data-nowall],[data-laterall],[data-schedall],[data-cal],[data-closesheet],.soon [data-day]");
      if (!t) return;
      if (t.dataset.open) { const id = t.dataset.open; state.open.has(id) ? state.open.delete(id) : state.open.add(id); render(); }
      else if (t.dataset.mapsel) { state.sel = t.dataset.mapsel; if (isDesktop()) { render(); $("mapPane").scrollIntoView({ behavior: "smooth", block: "nearest" }); } else setView("map"); }
      else if ("allday" in t.dataset) { state.allDayAll = true; render(); }
      else if ("nowall" in t.dataset) { state.nowAll = true; render(); }
      else if ("laterall" in t.dataset) { state.laterAll = true; render(); }
      else if (t.dataset.schedall) { state.schedAll.add(t.dataset.schedall); render(); }
      else if (t.dataset.cal) addToCalendar(t.dataset.cal, t.dataset.place);
      else if ("closesheet" in t.dataset) { state.sel = null; render(); }
      else if (t.dataset.day) { const d = +t.dataset.day; state.day = d === nowLocal().day ? null : d; window.scrollTo({ top: 0, behavior: "smooth" }); render(); }
    });
    window.addEventListener("scroll", () => { state.lastScroll = Date.now(); }, { passive: true });
    matchMedia("(min-width: 1024px)").addEventListener("change", () => { state.mapKey = ""; render(); });

    if (store.get("nearbyWatch", false)) startWatch();
    syncAlertsBadge();
    registerSW();
    window.addEventListener("online", () => { if (state.load === LOAD.ERROR) load(); });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      syncAlertsBadge();
      applyLook();
      const g = state.data && Date.parse(state.data.generated_at);
      if (state.load === LOAD.ERROR || (g && Date.now() - g > 20 * 36e5)) load(); else render();
    });
    if (navigator.permissions?.query) {
      navigator.permissions.query({ name: "geolocation" }).then((p) => (p.state === "granted" ? locate({ quiet: true }) : setFallbackLocation()), () => setFallbackLocation());
    } else setFallbackLocation();
    if (state.view === "map") setView("map");
    load();
    // Keep countdowns fresh; switch the look only if the visitor isn't in the middle of scrolling.
    setInterval(() => {
      if (document.hidden) return;
      if (Date.now() - state.lastScroll > 60000 && wantedLook() !== state.look) applyLook();
      render();
    }, 60 * 1000);
  }
  window.__foodish = { dealState: DS.dealState, km, state, LOAD, LOC, alertsState, setCity, nearestCity, wantedLook, isChain, priceValue };
  init();
})();
