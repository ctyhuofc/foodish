# Foodish — Edmonton restaurant deals near you

A location-aware web app for **www.foodish.ca**. Open it on a Wednesday downtown and it shows the wing night 1.6 km away that's on right now, with directions. Deals refresh automatically every day.

```
foodish/
├── site/                  ← the web app. Upload this folder to foodish.ca
│   ├── index.html, app.js, styles.css, config.js
│   ├── sw.js, manifest.webmanifest, icons  (installable app + notifications)
│   └── data/deals.json    (starter data; replaced daily)
├── pipeline/              ← the daily robot (runs free on GitHub)
│   ├── discover.py        weekly: finds every restaurant/bar/café in Edmonton (OpenStreetMap)
│   ├── scrape.py          daily: reads their websites, extracts deals, publishes deals.json
│   ├── llm.py             the AI step (Claude or Grok API — your choice)
│   ├── pages.py           daily: search-friendly deal pages + sitemap.xml
│   ├── health.py          daily: health check shown on each GitHub run's Summary
│   └── notify.py          daily ~3:45 pm: sends "deals near you today" alerts
├── worker/                ← tiny free Cloudflare service that stores alert sign-ups + submissions
├── data/
│   ├── manual_deals.json  deals you add by hand (seeded with real Edmonton wing nights)
│   ├── extra_restaurants.json (optional) places missing from OpenStreetMap
│   └── brand_sites.json   (optional) "Brand name": "https://website" fixes for chains
└── .github/workflows/daily.yml   the schedule
```

## How it stays cheap and current

| Step | How | Cost |
|---|---|---|
| Find restaurants | OpenStreetMap, re-checked every Sunday. Chains are tagged with a brand, so every Boston Pizza / Ricky's / Brewhouse branch is found and linked to one website. | $0 |
| Read websites | One visit per **website**, not per location. Robots.txt is respected; the bot identifies itself. | $0 (GitHub Actions) |
| Spot changes | Each site's deal text is fingerprinted. Most deals ("Wing Wednesday") don't change, so most days a site costs nothing. Pages with no prices or deal words are skipped. | $0 |
| Understand deals | Only *changed* sites go to a small AI model, which turns the text into days, times, prices and categories. Hard caps: `MAX_LLM_CALLS` per run (400) and `MONTHLY_LLM_CALLS` (6,000). | Usually a few dollars/month* |
| Alerts + submissions | Cloudflare Worker + KV free plan | $0 |
| Map | Leaflet + CARTO/OpenStreetMap tiles | $0 at pilot traffic** |

\* The first full run is the biggest (every site is new). After that, only changed sites are sent. Set a monthly spending limit in your AI account's console as a backstop, and the caps above stop the robot before it overspends. Note: a Grok/X subscription doesn't include API usage — the API is billed separately at console.x.ai.
\** If Foodish takes off, check the tile provider's usage terms; paid tile plans are cheap and fit the budget.

**Realistic total: well under your $50/month**, with nearly all of it being AI usage.

## What "every restaurant" really means

- ✅ Restaurants with their own website that posts specials (most chains and many independents).
- ✅ Chain deals are applied to every Edmonton branch automatically; location-only deals stay at that location.
- ⚠️ OpenStreetMap doesn't list a website for every place. After the first run, open `data/restaurants.json` to see coverage, and add fixes in `data/brand_sites.json` or `data/extra_restaurants.json`.
- ❌ Deals posted **only** on Instagram/Facebook can't be collected automatically (login walls and their terms forbid scraping). That's what the in-app **"Know a deal we're missing?"** form is for — and later, a free "list your deals" page for restaurants is the strongest way to close the gap.
- Sites built entirely in JavaScript show little text to a simple reader. Set the GitHub variable `RENDER_JS` = `1` to read those too (slower, still free).

---

## Setup (about an hour, once)

### 1. Put the code on GitHub (free)
1. Create a free account at github.com → **New repository** → name it `foodish` → **Public** (public repos get unlimited free Action minutes; private gets 2,000 min/month, which is also enough).
2. **Add file → Upload files** → drag in everything from this folder (including the hidden `.github` folder — on a Mac press ⌘⇧. to show it).

### 2. Connect an AI key
Pick one:
- **Claude (default):** create a key at console.anthropic.com, set a monthly limit.
- **Grok API:** create a key at console.x.ai. Then in GitHub → Settings → Secrets and variables → Actions → **Variables**, add `LLM_PROVIDER` = `openai-compatible`, `LLM_BASE_URL` = `https://api.x.ai/v1`, and `LLM_MODEL` = the cheapest/fastest model listed in your xAI console.

Either way, add the key under **Secrets** as `LLM_API_KEY`.

### 3. Run it the first time
GitHub → **Actions** → *Foodish daily update* → **Run workflow** → task `all` (now the default). The log ends with lines like `2,400 places found…` and `Published 310 deals at 780 locations`. It then runs by itself every morning.

Tip: for a cheap trial, run `python pipeline/scrape.py --limit 30` on your computer first.

### 4. Point the app at the live data
In `site/config.js` set:
```js
DATA_URL: "https://raw.githubusercontent.com/YOUR-GITHUB-NAME/foodish/main/data/deals.json",
```

### 5. Put the app on foodish.ca
The app is plain files — no server needed.
- **If your host lets you upload files** (file manager or FTP): upload the contents of `site/` to your site's root folder. Done. (Optional: add GitHub secrets `FTP_HOST`, `FTP_USER`, `FTP_PASS` and the robot will also upload `deals.json` there daily.)
- **If your current site builder only does drag-and-drop pages** and won't accept uploaded files: host the app free on **Cloudflare Pages** (connect it to your GitHub repo, build output folder `site`) and point `foodish.ca` / `www.foodish.ca` at it in your domain's DNS settings. Your domain stays yours.

Location needs **https** (all the options above provide it).

### 6. Turn on push alerts (optional, ~15 min)
1. Free account at cloudflare.com, then in a terminal:
   ```bash
   cd worker
   npx wrangler login
   npx wrangler kv namespace create SUBS
   npx wrangler kv namespace create SUBMISSIONS     # paste both ids into wrangler.toml
   npx wrangler secret put ADMIN_TOKEN               # make up a long random password
   npx wrangler deploy                               # prints your Worker URL
   npx web-push generate-vapid-keys                  # prints a public + private key
   ```
2. In `site/config.js`: `API_URL` = the Worker URL, `VAPID_PUBLIC_KEY` = the public key.
3. GitHub secrets: `ADMIN_TOKEN`, `VAPID_PRIVATE_KEY`. GitHub variables: `API_URL`, `VAPID_SUBJECT` = `mailto:you@foodish.ca`.

**How alerts work:** the 🔔 button offers (a) "while Foodish is open, ping me when I walk near a deal that's on now" and (b) a daily ~4 pm "deals near you today" push. On iPhone, push only works after the visitor taps Share → **Add to Home Screen** (an Apple rule); the app tells them this.

### Reviewing submitted deals
```bash
curl -H "Authorization: Bearer YOUR_ADMIN_TOKEN" https://foodish-api.YOURNAME.workers.dev/submissions
```
Add the good ones to `data/manual_deals.json` (copy an existing entry). Use `"brand"` to match all of a chain's branches, or `"locations"` with coordinates for a single spot.

## Testing on your computer
```bash
cd site && python3 -m http.server 8000      # open http://localhost:8000
```

## Exact 4 am updates (recommended, free, ~10 minutes)
GitHub's built-in scheduler often runs hours late, so "daily" deals could arrive at lunchtime. `worker/cron` is a tiny free Cloudflare Worker that tells GitHub to run the update at exactly 4:00 am Edmonton time (it handles daylight saving). GitHub's own schedules stay on as backups and skip themselves if the update already ran.

1. **GitHub token (limited to this one repo):** github.com → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate. Repository access: *Only select repositories* → `foodish`. Permissions → Repository → **Actions: Read and write** (nothing else). Copy the token.
2. **Cloudflare (free account):** in a terminal:
   ```bash
   cd worker/cron
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN     # paste the token when asked
   ```
3. Open the worker URL that `deploy` printed. It should say *"Foodish trigger is set up."* Tomorrow, Actions should show a run started by **workflow_dispatch** at 4:00 am.

The token can only start this repo's workflows; it can't read or change your code. If you ever revoke it, the GitHub backups keep things running.

## Search pages and the daily health check
- Every run also writes plain HTML pages Google can read: **foodish.ca/deals/** (one page per day and per deal type, e.g. `/deals/wings.html`, `/deals/wednesday.html`) plus `sitemap.xml` and `robots.txt`. The app itself draws deals with JavaScript, so search engines would otherwise see only "Loading today's deals…". Each page links into the app pre-filtered (`/?cat=wings`, `/?day=3`).
- **Getting them onto foodish.ca:** your live site reads deals straight from GitHub, but its pages are files on your web host. To publish the `/deals/` pages automatically every day, add your host's FTP details as GitHub secrets (`FTP_HOST`, `FTP_USER`, `FTP_PASS`) and, if your site isn't in `/public_html`, a variable `FTP_DIR` = `<your site folder>/data`. Otherwise, upload `site/deals/`, `site/sitemap.xml` and `site/robots.txt` by hand (they'll show the deals from the day you upload).
- After they're live, add the site in [Google Search Console](https://search.google.com/search-console) and submit `https://www.foodish.ca/sitemap.xml`.
- Need to rebuild just the pages? Actions → *Foodish daily update* → Run workflow → task `pages`.
- Each run's **Summary** page on GitHub (Actions → the run) shows a short health check: deals published, % of websites read, the big chains we can't read, AI usage this month, and hand-added deals the robot now finds itself.
- Hosting somewhere other than foodish.ca? Set the GitHub variable `SITE_URL`.

## Notes
- Times are Edmonton time year-round. GitHub's schedule is in UTC, so in winter the alert lands an hour earlier (~2:45 pm); adjust the cron line in `daily.yml` in November if you like.
- Deals the robot can't re-confirm for 14 days (site down/changed) are dropped automatically. Hand-added deals with a `valid_until` date expire on their own.
- The starter data is 4 real Edmonton wing nights reported in August 2026; the Canadian Brewhouse and Brewsters ones appear once step 3 has located their branches.
