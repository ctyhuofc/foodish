"""Step 3 (daily, after scrape): build plain HTML pages search engines can read.

The app itself draws deals with JavaScript, so Google mostly sees "Loading today's deals…".
This writes simple, fast pages from data/deals.json:

  site/deals/index.html            all days and deal types, with counts
  site/deals/monday.html …         one page per day of the week
  site/deals/wings.html …          one page per deal type with at least MIN_DEALS deals
  site/sitemap.xml, site/robots.txt

Each page links back into the app (e.g. /?cat=wings or /?day=3) so visitors land on the live map.
No extra packages needed.  Run:  python pipeline/pages.py
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "deals.json"
SITE = ROOT / "site"
OUT = SITE / "deals"
SITE_URL = os.environ.get("SITE_URL", "https://www.foodish.ca").rstrip("/")
CITY = "Edmonton"
MIN_DEALS = 3
DAY_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

# Friendlier page titles for common deal types; anything else gets "<Type> deals".
TITLES = {
    "happy hour": "Happy hour", "wings": "Wing deals", "drinks": "Drink specials", "pizza": "Pizza deals",
    "appetizers": "Appetizer specials", "family": "Family meal deals", "lunch": "Lunch specials",
    "late night": "Late-night deals", "tacos": "Taco deals", "burgers": "Burger deals", "kids": "Kids eat free and cheap",
    "brunch": "Brunch deals", "breakfast": "Breakfast deals", "seafood": "Seafood specials", "steak": "Steak deals",
    "pasta": "Pasta deals", "chicken": "Chicken deals", "sushi": "Sushi deals", "dessert": "Dessert deals",
    "vegetarian": "Vegetarian deals", "coffee": "Coffee deals",
}


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fmt_time(hhmm: str | None) -> str:
    if not hhmm:
        return ""
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except ValueError:
        return ""
    suffix = "am" if h % 24 < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}{':%02d' % m if m else ''} {suffix}"


def when(d: dict) -> str:
    days = d.get("days") or list(range(7))
    day_txt = "Every day" if len(days) == 7 else ", ".join(DAY_SHORT[x] for x in sorted(days) if 0 <= x < 7)
    s, e = fmt_time(d.get("start")), fmt_time(d.get("end"))
    if s and e:
        return f"{day_txt}, {s}–{e}"
    if s:
        return f"{day_txt}, from {s}"
    if e:
        return f"{day_txt}, until {e}"
    return day_txt


def live(d: dict, today: str) -> bool:
    if d.get("valid_until") and d["valid_until"] < today:
        return False
    if d.get("valid_from") and d["valid_from"] > today:
        return False
    return True


def deal_card(d: dict, by_id: dict) -> str:
    places = [by_id[i] for i in d.get("restaurant_ids", []) if i in by_id]
    if not places:
        return ""
    # OpenStreetMap names vary ("Brew House" / "Brewhouse", "A;B"): split and de-duplicate them for display
    seen, names = set(), []
    for p in places:
        for n in (p.get("name") or "").split(";"):
            n = n.strip()
            key = re.sub(r"[^a-z0-9]", "", n.lower())
            if n and key not in seen:
                seen.add(key)
                names.append(n)
    names.sort()
    who = names[0] if len(names) == 1 else ", ".join(names[:2]) + (f" and {len(names) - 2} more" if len(names) > 2 else "")
    where = places[0].get("address") or ""
    if len(places) > 1:
        where = f"{len(places)} Edmonton locations"
    price = f'<span class="price">{esc(d["price"])}</span>' if d.get("price") else ""
    src = d.get("source_url") or (places[0].get("website") if places else None)
    link = f' · <a href="{esc(src)}" rel="nofollow noopener">restaurant site</a>' if src else ""
    desc = f"<p>{esc(d['description'])}</p>" if d.get("description") else ""
    return (f'<li class="card"><div class="card-top"><h3>{esc(d.get("title"))}</h3>{price}</div>'
            f'<p class="who"><strong>{esc(who)}</strong> · {esc(where)}</p>{desc}'
            f'<p class="when">{esc(when(d))}{link}</p></li>')


CSS = """
:root{--bg:#fbf6ee;--surface:#fff;--ink:#1f1a17;--muted:#6f655d;--line:#eadfd1;--accent:#d9412b;--accent-soft:#fde3dc}
@media (prefers-color-scheme:dark){:root{--bg:#171311;--surface:#221c19;--ink:#f4ece4;--muted:#b3a79c;--line:#3a302a;--accent:#ff6a50;--accent-soft:#4a2219}}
*{box-sizing:border-box}body{margin:0 auto;max-width:760px;padding:0 16px 40px;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,-apple-system,Segoe UI,sans-serif}
a{color:var(--accent)}header{display:flex;align-items:baseline;gap:8px;padding:14px 0 4px}
.wordmark{font-family:"Bricolage Grotesque",system-ui,sans-serif;font-weight:800;font-size:26px;letter-spacing:-.02em;color:var(--accent);text-decoration:none}
.city{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600}
h1{font-family:"Bricolage Grotesque",system-ui,sans-serif;font-size:clamp(26px,6vw,36px);line-height:1.1;letter-spacing:-.02em;margin:12px 0 6px}
.lead{color:var(--muted);margin:0 0 14px}.cta{display:inline-block;background:var(--accent);color:#fff;padding:10px 16px;border-radius:999px;text-decoration:none;font-weight:600;margin:4px 0 18px}
ul.cards{list-style:none;padding:0;margin:0;display:grid;gap:10px}.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.card-top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}.card h3{margin:0;font-size:17px}
.price{background:var(--accent-soft);border-radius:999px;padding:2px 10px;font-weight:600;white-space:nowrap}
.card p{margin:6px 0 0}.who{color:var(--ink)}.when{color:var(--muted);font-size:14px}
nav.links{display:flex;flex-wrap:wrap;gap:6px;margin:18px 0}nav.links a{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:5px 11px;text-decoration:none;color:var(--ink);font-size:14px}
h2{font-size:18px;margin:26px 0 6px}footer{color:var(--muted);font-size:13px;margin-top:28px}
"""


def page(title: str, desc: str, path: str, body: str, updated: str) -> str:
    url = f"{SITE_URL}/{path}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} | Foodish</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}"><meta property="og:url" content="{esc(url)}">
<link rel="icon" href="/icon.svg" type="image/svg+xml"><meta name="theme-color" content="#d9412b">
<style>{CSS}</style></head>
<body><header><a class="wordmark" href="/">foodish</a><span class="city">{CITY}</span></header>
{body}
<footer>Deals are gathered from restaurants' own websites and checked daily. Always confirm with the restaurant before you go. Updated {esc(updated)}.</footer>
</body></html>
"""


def nav(day_counts: dict, cat_counts: dict, cats: list[str]) -> str:
    days = "".join(f'<a href="/deals/{slug(DAY_LONG[i])}.html">{DAY_LONG[i]} <span>({day_counts[i]})</span></a>' for i in range(7))
    types = "".join(f'<a href="/deals/{slug(c)}.html">{esc(TITLES.get(c, c.capitalize() + " deals"))} <span>({cat_counts[c]})</span></a>' for c in cats)
    return f'<h2>By day</h2><nav class="links">{days}</nav><h2>By type</h2><nav class="links">{types}</nav>'


def main() -> None:
    data = json.loads(DATA.read_text())
    today = dt.datetime.now(ZoneInfo("America/Edmonton")).date().isoformat()
    by_id = {r["id"]: r for r in data.get("restaurants", [])}
    deals = [d for d in data.get("deals", []) if live(d, today) and any(i in by_id for i in d.get("restaurant_ids", []))]
    deals.sort(key=lambda d: (-len(d.get("restaurant_ids", [])), (d.get("title") or "").lower()))
    try:
        updated = dt.datetime.fromisoformat(data["generated_at"]).astimezone(ZoneInfo("America/Edmonton")).strftime("%b %-d, %Y")
    except Exception:  # noqa: BLE001
        updated = today

    day_counts = {i: sum(1 for d in deals if i in (d.get("days") or range(7))) for i in range(7)}
    cat_counts: dict[str, int] = {}
    for d in deals:
        for c in d.get("categories") or []:
            cat_counts[c] = cat_counts.get(c, 0) + 1
    cats = sorted((c for c, n in cat_counts.items() if n >= MIN_DEALS), key=lambda c: -cat_counts[c])
    links = nav(day_counts, cat_counts, cats)

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.html"):  # remove pages for deal types that dropped below the minimum
        old.unlink()
    written = []

    def write(name: str, title: str, desc: str, lead: str, app_link: str, items: list[dict]) -> None:
        cards = "".join(deal_card(d, by_id) for d in items)
        body = (f"<h1>{esc(title)}</h1><p class=\"lead\">{esc(lead)}</p>"
                f'<a class="cta" href="{esc(app_link)}">See these on the map near you →</a>'
                f'<ul class="cards">{cards}</ul>{links}')
        (OUT / name).write_text(page(title, desc, f"deals/{name}", body, updated))
        written.append(f"deals/{name}")

    for i, day in enumerate(DAY_LONG):
        items = [d for d in deals if i in (d.get("days") or range(7))]
        write(f"{slug(day)}.html", f"{day} restaurant deals in {CITY}",
              f"{len(items)} restaurant deals on {day}s in {CITY}: happy hours, wing nights, drink specials and more, checked daily.",
              f"{len(items)} deals on {day}s, from restaurants' own websites.", f"/?day={i}", items)

    for c in cats:
        items = [d for d in deals if c in (d.get("categories") or [])]
        title = f"{TITLES.get(c, c.capitalize() + ' deals')} in {CITY}"
        write(f"{slug(c)}.html", title,
              f"{len(items)} {c} deals at {CITY} restaurants, with days, times and prices, checked daily.",
              f"{len(items)} deals, with the days and times they run.", f"/?cat={c}", items)

    hub_body = (f"<h1>Restaurant deals in {CITY}</h1>"
                f'<p class="lead">{len(deals)} deals at {len({i for d in deals for i in d.get("restaurant_ids", []) if i in by_id})} '
                f'locations, gathered from restaurants\' own websites and checked daily.</p>'
                f'<a class="cta" href="/">Open Foodish near you →</a>{links}')
    (OUT / "index.html").write_text(page(f"Restaurant deals in {CITY}",
        f"Every restaurant deal we know of in {CITY}, by day and by type. Updated daily.", "deals/", hub_body, updated))
    written.insert(0, "deals/")

    urls = [""] + written
    sitemap = "".join(f"<url><loc>{esc(SITE_URL + '/' + u)}</loc><lastmod>{today}</lastmod></url>" for u in urls)
    (SITE / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sitemap}</urlset>\n')
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
    print(f"Wrote {len(written)} pages for {len(deals)} deals ({len(cats)} deal types) + sitemap.xml")


if __name__ == "__main__":
    main()
