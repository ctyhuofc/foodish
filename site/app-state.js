/* The app's small rules that don't touch the page: what the address bar means, which message the status
 * line shows, whether alerts are on, which alert sign-ups still need deleting, what a visit count sends,
 * which day is chosen, and the stages of the "tell us a deal" form.
 * No page, no storage, no clock: everything comes in as arguments, so tests/app-state.test.mjs can check
 * every case. Loaded as a plain <script> before app.js (window.FoodishAppState), and as a CommonJS module
 * by the tests. app.js applies the answers to the page. State machines: docs/STATE_MACHINES.md.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FoodishAppState = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- the address bar ----------
  //   ?city=vancouver  ?cat=wings  ?day=3 (0 = Sunday)  ?now=1  ?view=map  ?km=2  ?q=tacos  ?look=day (testing)
  // Anything we don't recognise is ignored (null), never guessed.
  function normalizeDay(day, today) {
    if (day == null || !(day >= 0 && day <= 6)) return null;
    return day === today ? null : day;  // "today" is the live view, not a schedule for today
  }
  // search: "?cat=wings&day=3" (or URLSearchParams text). today: 0-6 in the shown city; leave out to keep the day as given.
  function parseLink(search, { cityIds = [], radii = [], today } = {}) {
    const p = new URLSearchParams(search || "");
    const city = (p.get("city") || "").trim().toLowerCase();
    const cat = (p.get("cat") || "").trim().toLowerCase();
    const dayN = parseInt(p.get("day"), 10);
    let day = dayN >= 0 && dayN <= 6 ? dayN : null;
    if (today != null) day = normalizeDay(day, today);
    const radius = parseFloat(p.get("km"));
    const look = p.get("look");
    return {
      city: cityIds.includes(city) ? city : null,
      cat: cat || null,
      day,
      onNow: p.get("now") === "1",
      view: p.get("view") === "map" ? "map" : "list",
      radius: radii.includes(radius) ? radius : null,
      q: p.get("q") || "",
      look: look === "day" || look === "night" ? look : null,
    };
  }
  // The address for the current filters, without the "?". The city only when it was chosen (locked) or isn't the
  // default; the distance only when it differs from the visitor's saved one; ?look= only while it is forced.
  function buildLink(f, { defaultCity, savedRadius } = {}) {
    const p = new URLSearchParams();
    if (f.cityLocked || f.city !== defaultCity) p.set("city", f.city);
    if (f.cat) p.set("cat", f.cat);
    if (f.day != null) p.set("day", f.day);
    if (f.onNow) p.set("now", "1");
    if (f.view === "map") p.set("view", "map");
    if (f.radius !== savedRadius) p.set("km", f.radius);
    if ((f.q || "").trim()) p.set("q", f.q.trim());
    if (f.look) p.set("look", f.look);
    return p.toString();
  }
  // Switching view, day or category is a step the back button undoes; anything else just updates the address.
  const historyStep = ({ view, day, cat }) => `${view}|${day}|${cat}`;

  // ---------- the status line under the count ----------
  // One line, several things that want it. Most important first: a failed load with nothing to show (with a
  // Try again button), then "Loading…" with nothing to show, then a note about the visitor's location, then
  // "these deals are old". Deals already on screen are never hidden by a failed refresh.
  const STATUS = Object.freeze({ LOADING: "loading", ERROR: "error", STALE: "stale", NOTE: "note", NONE: "none" });
  function statusLine({ load, hasData, errorDetail, stale, locNote } = {}) {
    if (load === "error" && !hasData) return { kind: STATUS.ERROR, text: `Couldn't load deals right now (${errorDetail || "network error"}).`, retry: true };
    if (load === "loading" && !hasData) return { kind: STATUS.LOADING, text: "Loading today's deals…", retry: false };
    if (locNote) return { kind: STATUS.NOTE, text: locNote, retry: false };
    if (stale) return { kind: STATUS.STALE, text: "These deals were last checked over a day ago.", retry: false };
    return { kind: STATUS.NONE, text: "", retry: false };
  }

  // ---------- alerts ----------
  // "on" only if the visitor asked for alerts AND the browser still allows notifications. Never stored.
  function alertsState({ wanted, permission }) {
    if (!wanted) return "off";
    return permission === "granted" ? "on" : "blocked";
  }
  const BLOCKED_TEXT = "Notifications are blocked for this site. You can allow them in your browser settings.";
  // The message under the alert switches: the "blocked" note while blocked, gone once allowed again.
  // Any other message (such as "couldn't set up") is left alone.
  function alertsMessage(st, current) {
    if (st === "blocked") return current || BLOCKED_TEXT;
    return current === BLOCKED_TEXT ? "" : current;
  }

  // Turning the daily heads-up off asks the alerts service to delete this browser's sign-up (its notification
  // address and rounded location). If that request fails, the address waits in a short list (saved on the phone)
  // and is sent again on later opens until the service says it's deleted. result: "queued" (about to ask),
  // "deleted" (the service said so), "failed" (keep waiting), "signed_up" (on again with that address: the
  // service's copy is current, so it must not be deleted later). Only https addresses are kept, at most 5.
  const UNSUB_MAX = 5;
  function nextUnsubscribes(list, { endpoint, result }) {
    const ok = (e) => typeof e === "string" && /^https:\/\/[^\s]+$/.test(e);
    const kept = (Array.isArray(list) ? list : []).filter(ok);
    const others = kept.filter((e) => e !== endpoint);
    if (result === "queued") return ok(endpoint) ? [...others, endpoint].slice(-UNSUB_MAX) : kept;
    if (result === "deleted" || result === "signed_up") return others;
    return kept;  // "failed": nothing changes
  }

  // ---------- counting visits (GoatCounter) ----------
  // GoatCounter would send the whole address, search included (?q=tacos). We send the page's path only.
  function visitData(data, pathname) {
    const d = { ...(data || {}), p: pathname || "/" };
    delete d.q;
    return d;
  }

  // ---------- "A deal we missed?" form ----------
  const SUBMIT = Object.freeze({ IDLE: "idle", SENDING: "sending", SENT: "sent", ERROR: "error", EMAIL: "email" });
  const SUBMIT_TEXT = {
    idle: "", sending: "Sending…", sent: "Thanks! We'll check it and add it soon.",
    error: "Couldn't send that. Please try again.", email: "Your email app should open with the deal filled in.",
  };
  const submitView = (s) => ({ disabled: s === SUBMIT.SENDING, text: SUBMIT_TEXT[s] ?? "" });

  // ---------- links from the deals file ----------
  // A deal's link comes from a restaurant's website via the AI. Only plain web addresses (http/https) are
  // used; anything else ("javascript:...", "data:...", nonsense) gives "" so no link is shown.
  function safeUrl(u) {
    if (typeof u !== "string" || !u) return "";
    try {
      const url = new URL(u);
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
    } catch { return ""; }
  }

  return { safeUrl, parseLink, buildLink, historyStep, normalizeDay, STATUS, statusLine, alertsState, alertsMessage, BLOCKED_TEXT, nextUnsubscribes, visitData, SUBMIT, submitView };
});
