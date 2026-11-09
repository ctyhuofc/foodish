/* When is a deal "on"? The single rulebook, browser side.
 * Mirrors pipeline/deal_state.py; both must pass every case in spec/deal-states.json
 * (run: node --test tests/*.test.mjs). Loaded as a plain <script> before app.js (window.FoodishDealState),
 * and as a CommonJS module by the tests.
 *
 * States: expired · upcoming · not_today · all_day · later · on_now · ended · scheduled
 * (meanings in spec/deal-states.json). "all_day" is never "on now": we don't know opening hours.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FoodishDealState = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6];
  const MIDNIGHT = 24 * 60;
  const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const S = Object.freeze({
    EXPIRED: "expired", UPCOMING: "upcoming", NOT_TODAY: "not_today", ALL_DAY: "all_day",
    LATER: "later", ON_NOW: "on_now", ENDED: "ended", SCHEDULED: "scheduled",
  });

  // Alberta stopped changing its clocks on 1 Nov 2026 and stays on UTC-6 year-round. Many phones' and
  // browsers' time-zone data still expect standard time (UTC-7) from November, which would put every
  // "on now" an hour off, so for Edmonton after that date we use the fixed offset instead.
  const PERMANENT_UTC6_FROM = Date.UTC(2026, 10, 1, 8, 0); // 2 am MDT, the old fall-back moment
  const FIXED = { "America/Edmonton": { from: PERMANENT_UTC6_FROM, zone: "Etc/GMT+6" } }; // Etc/GMT+6 = UTC-6

  // An Edmonton wall-clock moment: { date: "YYYY-MM-DD", day: 0-6 (0 = Sunday), mins: minutes since midnight or null }
  // The zone to format a moment with (handles Alberta's switch to permanent UTC-6).
  function zoneFor(timeZone, when) {
    const fixed = FIXED[timeZone];
    return fixed && when.getTime() >= fixed.from ? fixed.zone : timeZone;
  }

  function zonedNow(timeZone, now) {
    const when = now || new Date();
    const zone = zoneFor(timeZone, when);
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(when);
    const get = (t) => parts.find((p) => p.type === t).value;
    return {
      date: `${get("year")}-${get("month")}-${get("day")}`,
      day: DAY_NAMES.indexOf(get("weekday")),
      mins: (parseInt(get("hour"), 10) % 24) * 60 + parseInt(get("minute"), 10),
    };
  }

  // "2026-11-11T17:40" or "2026-11-11" (wall clock) -> moment. Used by the shared test cases.
  function momentFromLocal(text) {
    const [date, clock] = text.split("T");
    const day = new Date(date + "T12:00:00Z").getUTCDay();
    let mins = null;
    if (clock) { const [h, m] = clock.split(":").map(Number); mins = h * 60 + m; }
    return { date, day, mins };
  }

  function toMins(hhmm) {
    if (typeof hhmm !== "string") return null;
    const m = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(hhmm.trim());
    return m ? Number(m[1]) * 60 + Number(m[2]) : null;
  }
  function daysOf(deal) {
    const days = (deal.days || []).filter((d) => Number.isInteger(d) && d >= 0 && d <= 6);
    return days.length ? days : EVERY_DAY;
  }
  const runsOn = (deal, day) => daysOf(deal).includes(day);
  function isLive(deal, date) {
    if (deal.valid_until && deal.valid_until < date) return false;
    if (deal.valid_from && deal.valid_from > date) return false;
    return true;
  }

  function dealState(deal, at) {
    if (deal.valid_until && deal.valid_until < at.date) return S.EXPIRED;
    if (deal.valid_from && deal.valid_from > at.date) return S.UPCOMING;
    const days = daysOf(deal);
    const start = toMins(deal.start), end = toMins(deal.end);
    const overnight = start != null && end != null && end <= start;
    if (at.mins == null) return days.includes(at.day) ? S.SCHEDULED : S.NOT_TODAY;
    if (overnight && days.includes((at.day + 6) % 7) && at.mins < end) return S.ON_NOW; // yesterday's late deal
    if (!days.includes(at.day)) return S.NOT_TODAY;
    if (start == null && end == null) return S.ALL_DAY;
    const winStart = start != null ? start : 0;
    const winEnd = overnight || end == null ? MIDNIGHT : end;
    if (at.mins < winStart) return S.LATER;
    if (at.mins < winEnd) return S.ON_NOW;
    return S.ENDED;
  }

  return { STATES: S, dealState, zonedNow, zoneFor, momentFromLocal, toMins, daysOf, runsOn, isLive };
});
