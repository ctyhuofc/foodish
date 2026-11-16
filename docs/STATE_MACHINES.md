# Foodish state machines

Every part of Foodish that moves through stages, with its states named, the one place that
changes it, and the test that pins it down. If you're changing behaviour, change it here first.

| # | Machine | Lives in | Tested by |
|---|---------|----------|-----------|
| 1 | Deal timing (is it on now?) | `spec/deal-states.json` → `pipeline/deal_state.py` + `site/deal-state.js` | `tests/test_deal_state.py`, `tests/deal-state.test.mjs` |
| 2 | Website lifecycle (one site, one daily run) | `pipeline/site_state.py`, used by `pipeline/scrape.py` | `tests/test_site_state.py` |
| 3 | Run plan (what a workflow run does) | `pipeline/plan.py`, used by `.github/workflows/daily.yml` | `tests/test_plan.py` |
| 4 | Robot health (is the robot broken?) | `pipeline/health.py --gate` (last workflow step) | `tests/test_plan.py` |
| 5 | App data loading | `site/app.js` `setLoad()` / `load()` | browser check (see below) |
| 6 | App location | `site/app.js` `setLocation()` | browser check |
| 7 | Alerts | rule: `site/app-state.js` `alertsState()`; applied by `site/app.js` `syncAlertsBadge()`; `worker/worker.js` | `tests/app-state.test.mjs`, browser check |
| 8 | Filters ↔ address bar | `site/app-state.js` `parseLink()` / `buildLink()`, used by `site/app.js` `readLinkFilters()`, `onPopState()`, `writeLinkFilters()` | `tests/app-state.test.mjs`, browser check |
| 9 | City (which city's deals are showing) | `site/app.js` `setCity()`; list in `cities.json` | `tests/test_cities.py`, browser check |
| 10 | Look (Daylight or Blue Hour) | `site/sky.js` `lookAt()`, applied by `site/app.js` `applyLook()` | `tests/sky.test.mjs`, browser check |
| 11 | Status line (the one line under the count) | `site/app-state.js` `statusLine()`, shown by `site/app.js` `renderStatus()` | `tests/app-state.test.mjs`, browser check |
| 12 | Smaller app machines: chosen day, panels, map card, list expanders, submit form, daily-alert sign-up | `site/app-state.js` + `site/app.js` (see below) | `tests/app-state.test.mjs`, browser check |

The app's page-free rules live in `site/app-state.js` (like `deal-state.js` and `sky.js`: a plain script in the
page, and a module for `node --test`). `site/app.js` applies them to the page. `tests/test_app_shell.py` checks that
every script the page loads is also in the offline copy (`SHELL` in `site/sw.js`).

Run everything: `python -m unittest discover -s tests && node --test tests/*.test.mjs`

---

## 1. Deal timing

The rule every screen, page and alert uses to decide whether a deal counts. **The rules are data**:
`spec/deal-states.json` lists the states and 33 worked examples; the Python and browser versions must
both pass every one, so they can't drift. Any other app that reads `data/deals.json` (such as a
partner app) should follow the same file. `deals.json` links to it under `rules`.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> expired: valid_until < today
    [*] --> upcoming: valid_from > today
    [*] --> on_now: yesterday's late deal still running (e.g. 9 pm–2 am)
    [*] --> not_today: doesn't run this weekday
    [*] --> scheduled: another day, no time given
    [*] --> all_day: runs today, no start/end time
    [*] --> later: before start
    later --> on_now: start time
    on_now --> ended: end time (or midnight)
```

- A deal with only an end time ("lunch until 4 pm") is taken to start 4 hours before it ends, no later than 11:00 (`start_of()` / `startOf()`). Before that it is `later`, not `on_now`.
- `all_day` is **never** "on now": we don't know the restaurant's hours. (Before this change the
  app showed all-day deals as "on now" at 2 am.) On a picked day with no time, a deal with no times is also
  `all_day`, so it lands under "All day", not as a blank-time "Daytime" row.
- **Each city's clock:** deal times are in the deal's own city. Alberta has stayed on UTC-6 and British
  Columbia on UTC-7 year-round since 2026. Many devices' time-zone data still expect a November clock
  change, which would put every "on now" an hour off, so both versions use the fixed offsets
  (`now_in(zone)` / `zonedNow(zone)`).
- Display order on today's list: `on_now`, `later`, `all_day`, `ended`.

## 2. Website lifecycle

What happens to one restaurant website on a daily run. `site_state.next_step()` decides; `scrape.py`
does it and writes the outcome to `data/cache/report.json` (`sites.<domain>.outcome`, totals in
`outcome_counts`). The health summary shows the totals.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> blocked: robots.txt blocks FoodishBot, or listed in data/removed.json (deals removed at once)
    [*] --> unreachable: can't load
    unreachable --> gone: still unreachable after 14 days (deals removed)
    [*] --> unchanged: same text, same AI instructions (no AI call)
    [*] --> no_deals: no deal words (deals cleared)
    [*] --> deferred_ai_stopped: AI already failed 3× the same way this run
    [*] --> deferred_budget: AI call limit used up
    [*] --> extracted: AI read it (deals replaced)
    [*] --> ai_error: AI call failed
    deferred_budget --> [*]: retried tomorrow
    ai_error --> [*]: retried tomorrow
    deferred_ai_stopped --> [*]: retried tomorrow
```

Before the outcome, each site gets a read status (`sites.<domain>.status` in the report): `ok`, `removed by request`,
`blocked by robots.txt`, or an error. The names are `READ_OK`, `READ_REMOVED`, `READ_ROBOTS` and `BLOCKED_READS` in
`site_state.py`; `scrape.py` and `health.py` use them, never the words.

"Retried tomorrow" works because a site's text fingerprint is only saved after the AI has read it.
Deferred and failed sites keep their old deals. A website or an AI deal the robot can't make sense of is
skipped for the day (the site counts as unreachable, the deal is dropped); it doesn't stop the city. Cached deals whose `valid_until` has passed are now
dropped when publishing; they used to linger until the website changed.

## 3. Run plan

`pipeline/plan.py` turns *(event, cron, manual task, is the restaurant list empty, how old are the
deals)* into three yes/no jobs: `discover`, `scrape`, `notify`. Workflow steps only read
`steps.plan.outputs.*`; no cron strings are repeated in `if:` lines.

| Trigger | discover | scrape | notify |
|---|---|---|---|
| Manual `scrape` from the exact 4 am trigger (`worker/cron`) | if list empty | yes | no |
| `17 10 * * *` daily (4:17 am) | if list empty | yes, unless refreshed in the last 3 h (the 4 am trigger ran) | no |
| `47 11 * * *`, `23 13 * * *` backups | if list empty | only if deals are 12 h+ old | no |
| `45 21 * * *` alerts (3:45 pm) | if list empty | no | yes |
| `5 9 * * 0` weekly (Sun 3:05 am) | yes | yes | no |
| Manual `all` | yes | yes | yes |
| Manual `discover` / `scrape` / `notify` / `pages` | that job only (`scrape` also discovers if the list is empty) | | |

Search pages and the health summary run on every run. An unknown cron or task is an error, not a
silent no-op. `tests/test_plan.py` checks the table against a copy of the old workflow logic and that
`daily.yml` and `SCHEDULES` list the same crons.

## 4. Robot health

`healthy` → `broken`. After everything has saved and published, the last workflow step runs
`health.py --gate`. If today's refresh didn't finish, or the AI step stopped (bad key, no credit), the
run turns red and GitHub emails the owner, with the fix in the error. Before this, a run with a dead
AI key still showed a green tick (that happened every day from 22 Oct to 8 Nov 2026).

## 5–8, 11–12. The app (`site/app.js`, rules in `site/app-state.js`)

| Machine | States | Changes via | Notes |
|---|---|---|---|
| Data | `loading` → `ready` \| `error`; `error` → `loading` | `setLoad()` | Retry button; retries when back online or when the tab reopens; a failed refresh keeps the deals already shown; "last checked … over a day ago" when deals are 36 h+ old. Each `load()` is numbered and only the newest may show its result. While there are no deals for the shown city (switching city, or a failed first load) the list, street, categories and map card are empty: another city's deals are never shown |
| Location | `locating` → `precise` \| `last_known` \| `default` \| `away` | `setLocation()` | The location label is derived from the state. `away` = we know where the visitor is, but it's far from the shown city, so distances are from its centre. A second tap while `locating` does nothing. The nearby-watch goes through `applyPosition()` too, but only for the first precise fix or a move of more than 150 m |
| Alerts | `off` \| `on` \| `blocked` (derived, never stored twice) | `AS.alertsState()` → `syncAlertsBadge()` | `blocked` = the visitor asked for alerts but the browser now refuses; nearby-watch stops. The "blocked" note under the switches comes and goes with the browser setting (`AS.alertsMessage()`); other notes stay. One change at a time: a double tap doesn't start a second sign-up |
| Submit | `idle` → `sending` → `sent` \| `error`; or `email` (tips go by email) | `setSubmit()` | Send is off only while `sending`; the words are in `AS.submitView()` |
| Filters | in the address bar | `AS.parseLink()` (one reader, used on opening and on back/forward) / `AS.buildLink()` (one writer) | `?city=toronto&cat=wings&day=3&now=1&view=map&km=1&q=tacos`: shareable, and opens the app in that state. Back/forward puts all of it back (city, category, day, on-now-only, search, distance, view). Map, day and category are back-button steps; search and distance just update the address. `?look=day` / `?look=night` previews a look and is never saved. Unknown values are ignored |
| Status line | `error` > `loading` > `note` > `stale` > `none` | `AS.statusLine()` → `renderStatus()` (the only writer of `#status`) | Most important wins: a failed load with nothing to show (with Try again), then "Loading…" with nothing to show, then a location note ("Location is off, so…"), then "last checked over a day ago". A location note lasts until the next locate or a precise fix |
| Chosen day | `null` (today, live) \| 0–6 (a schedule) | `AS.normalizeDay()`, at the top of `render()` | A chosen day that has become today (open past midnight, or another city's clock) turns back into the live today view |
| On now only | `?now=1` hides later and all-day deals | `state.onNow` | A "Showing only what's on now. Show everything" button turns it off |
| Panels | `null` \| `city` \| `search` | `setPanel()` | One open at a time. Escape closes it and returns to its button; a click elsewhere closes the city picker. The search row opens when a search is in the address |
| Map card | a place selected \| none \| dismissed | `state.sel`, `state.selDismissed` | Desktop opens the card for the first place on now, unless the visitor closed it (close button or a click on the map). Choosing a place, or changing city, category, day or distance, lets it open again |
| List expanders | "Details" and "Show more" choices | `resetList()` | Reset together on a new city, category or day, and on back/forward (not on an in-page jump such as a street building). Tapping a building whose row isn't shown yet opens the shortened lists first |
| Daily-alert sign-up | switch on + browser signed up \| switch on + sign-up lost | `reconcilePush()` on opening | If the browser lost its sign-up (site data cleared, expired) while alerts are allowed, it signs up again quietly; if that fails nothing is shown and the next open tries again |

Debug in the browser console: `__foodish.state`, `__foodish.alertsState()`, `__foodish.statusLine({...})`, `__foodish.setPanel("city")`.

Alert subscriptions (worker): `subscribed` → `unsubscribed` (visitor turns it off) or `expired`
(`notify.py` removes it when the push service answers 404/410). Admin endpoints stay locked unless
`ADMIN_TOKEN` is set.

## 9. City

Which city's deals are showing. One value (`state.city`), changed only by `setCity()`, which swaps the
data file, the centre, the clock and the labels together. The cities are data: `cities.json`.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> from_link: ?city=vancouver (locked)
    [*] --> last_used: the city used last time
    [*] --> default_city: first visit
    last_used --> by_location: visitor is within 100 km of another city
    default_city --> by_location: visitor is within 100 km of another city
    from_link --> picked: picker
    last_used --> picked: picker (locked)
    by_location --> picked: picker (locked)
```

- **Locked** (link or picker): the visitor's location no longer switches the city. If they're far from
  it, the location state is `away` and distances are measured from that city's centre.
- Visitors more than 100 km from every city stay on the current one and are told Foodish isn't in
  their area yet.
- The robot treats cities independently: the run plan (machine 3) is decided per city from that city's
  own files, each city has its own website lifecycle cache (machine 2), and the health gate
  (machine 4) checks every city that was refreshed. One city failing never blocks another.

## 10. Look

`day` (Daylight) or `night` (Blue Hour), on `<html data-look>`. One function decides it (`wantedLook()`), one applies it (`applyLook()`).

```mermaid
stateDiagram-v2
    direction LR
    night --> day: sunrise in the shown city
    day --> night: 30 minutes before sunset
    note right of day: Settings can fix either look; ?look= previews one without saving
```

- Sun times come from `sky.js` for the shown city's centre, on that city's clock (`deal_state` fixed offsets for Alberta and B.C.).
- Checked when the page opens, when the city changes, when the tab comes back, and each minute, but never applied within a minute of the visitor scrolling. Colours fade over one second (off with reduced motion).
- `index.html` sets the last look before anything draws, so the page doesn't flash the wrong colours.
- The look never changes what a deal *is*: lit = on now, outline = later today, blue band = all day, in both looks. Deals that have ended aren't shown.
