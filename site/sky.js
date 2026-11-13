/* Which look the app shows: "day" (Daylight) or "night" (Blue Hour), decided by the sun.
 * Rule: Daylight from sunrise until 30 minutes before sunset, Blue Hour from then until the next sunrise,
 * on the shown city's own clock. Sun times use the standard sunrise equation (accurate to a minute or two).
 * Loaded as a plain <script> (window.FoodishSky) and as a CommonJS module by tests/sky.test.mjs.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.FoodishSky = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const rad = (d) => (d * Math.PI) / 180;
  const deg = (r) => (r * 180) / Math.PI;
  const DUSK_LEAD_MINS = 30; // Blue Hour starts this long before sunset

  // UTC time (Date) of sunrise or sunset at lat/lng on the calendar date "YYYY-MM-DD".
  function sunEvent(lat, lng, ymd, rising) {
    const [y, m, d] = ymd.split("-").map(Number);
    const N = Math.round((Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 0)) / 864e5);
    const lngHour = lng / 15;
    const t = N + ((rising ? 6 : 18) - lngHour) / 24;
    const M = 0.9856 * t - 3.289;
    let L = M + 1.916 * Math.sin(rad(M)) + 0.02 * Math.sin(rad(2 * M)) + 282.634;
    L = ((L % 360) + 360) % 360;
    let RA = ((deg(Math.atan(0.91764 * Math.tan(rad(L)))) % 360) + 360) % 360;
    RA = (RA + (Math.floor(L / 90) * 90 - Math.floor(RA / 90) * 90)) / 15;
    const sinDec = 0.39782 * Math.sin(rad(L));
    const cosDec = Math.cos(Math.asin(sinDec));
    const cosH = (Math.cos(rad(90.833)) - sinDec * Math.sin(rad(lat))) / (cosDec * Math.cos(rad(lat)));
    if (cosH > 1 || cosH < -1) return null; // polar day or night
    const H = (rising ? 360 - deg(Math.acos(cosH)) : deg(Math.acos(cosH))) / 15;
    const T = H + RA - 0.06571 * t - 6.622;
    let UT = T - lngHour;
    // Put the event on the right UTC day: nearest to its expected solar time for this date.
    const expected = (rising ? 6 : 18) - lngHour;
    UT = ((UT % 24) + 24) % 24;
    while (UT - expected > 12) UT -= 24;
    while (expected - UT > 12) UT += 24;
    return new Date(Date.UTC(y, m - 1, d) + UT * 36e5);
  }

  // { rise, set } as minutes after midnight on the city's clock. `clock(date)` returns { mins } for that city
  // (FoodishDealState.zonedNow bound to the city's time zone).
  function sunTimes(lat, lng, ymd, clock) {
    const r = sunEvent(lat, lng, ymd, true), s = sunEvent(lat, lng, ymd, false);
    if (!r || !s) return null;
    return { rise: clock(r).mins, set: clock(s).mins };
  }

  // "day" or "night" at `mins` (city clock) given that day's sun times.
  function lookAt(mins, sun) {
    if (!sun) return "night";
    return mins >= sun.rise && mins < sun.set - DUSK_LEAD_MINS ? "day" : "night";
  }

  return { sunEvent, sunTimes, lookAt, DUSK_LEAD_MINS };
});
