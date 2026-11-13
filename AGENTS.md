# Working on Foodish (for AI agents and developers)

Foodish finds restaurant deals near you in Edmonton, Vancouver and Toronto (the list is `cities.json`). A GitHub Actions robot reads restaurant websites
daily and publishes `data/deals.json`; the app (`site/`, and the live foodish.ca app built in Grok)
reads that file. The owner isn't a developer: explain changes in plain words.

## Before you change behaviour
Read `docs/STATE_MACHINES.md`. Everything that moves through stages is a named state machine with one
place that changes it. Change the machine, not a copy of its logic.

- **"Is this deal on?"** → edit `spec/deal-states.json` first (add a case), then make
  `pipeline/deal_state.py` and `site/deal-state.js` pass it. Never re-implement day/time/valid-date
  checks elsewhere; import `deal_state` (Python) or use `window.FoodishDealState` (browser).
- **What happens to a website on a run** → `pipeline/site_state.py`.
- **What a workflow run does** → `pipeline/plan.py`. Changing a cron means editing `daily.yml` *and*
  `SCHEDULES` in `plan.py`.
- **Cities** → `cities.json` is the list; `pipeline/cities.py` gives each city's file paths. Never
  hard-code a city name or a `data/...` path: take a `City`. Scripts accept city ids
  (`python pipeline/scrape.py vancouver`; none = every city). Edmonton's paths (`data/deals.json`, …)
  must never move: the live site reads them.
- **The app's look** (Daylight / Blue Hour) → `site/sky.js` decides, `applyLook()` in `site/app.js` applies. Colours are CSS variables under `html[data-look="night"]` and `html[data-look="day"]` in `site/styles.css`; never hard-code a colour for one look only. Lit yellow means on now, everywhere.
- **Local time** → `deal_state.now_in(city.timezone)` / `FoodishDealState.zonedNow(zone)`. Alberta is
  UTC-6 and B.C. is UTC-7 year-round; Ontario (Toronto) still changes clocks and uses normal tz data; don't use raw `ZoneInfo(...)` or `Intl` for "now".

## Check your work
```bash
python -m unittest discover -s tests     # Python: deal rules, site lifecycle, run plan, health gate
node --test tests/*.test.mjs            # Browser rules: same deal cases as Python
python pipeline/plan.py --event schedule --schedule "47 11 * * *"   # what a run would do
python pipeline/health.py                # summary of the latest robot run
```
`.github/workflows/tests.yml` runs both test suites on every push and pull request.

## Don't
- Don't commit API keys. They live in GitHub secrets (`LLM_API_KEY`, …).
- Don't hand-edit `data/deals.json`, `data/cache/*`, `site/deals/*` or `site/sitemap.xml`: the robot
  regenerates them daily. Hand-added deals go in `data/manual_deals.json`.
- Don't remove fields from `data/deals.json`; the live app reads it. Adding fields is fine.
