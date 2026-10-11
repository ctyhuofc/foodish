"""Step 2 (daily): visit each restaurant website, pull out its deals, publish data/deals.json.

Why it's cheap:
  * One visit per *website*, not per location — a chain like Boston Pizza is read once and
    the deals are applied to every Edmonton branch.
  * Change detection — each site's text is fingerprinted. If nothing changed since yesterday
    (true for most "Wing Wednesday"-type deals), the saved deals are reused and no AI call is made.
  * Keyword pre-filter — pages with no price/deal words never reach the AI.
  * Hard caps — MAX_LLM_CALLS per run and MONTHLY_LLM_CALLS per month.

Usage:  python pipeline/scrape.py            (needs LLM_API_KEY in the environment)
        python pipeline/scrape.py --limit 30 (try it on 30 sites first)
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from llm import extract_deals

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESTAURANTS = DATA / "restaurants.json"
CACHE = DATA / "cache" / "sites.json"
MANUAL = DATA / "manual_deals.json"
OUT = DATA / "deals.json"
SITE_COPY = ROOT / "site" / "data" / "deals.json"

BOT = "FoodishBot"  # the name robots.txt rules are matched against
UA = "Mozilla/5.0 (compatible; FoodishBot/1.0; +https://www.foodish.ca/bot)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-CA,en;q=0.8"}
REASONS = {}  # url -> why it couldn't be read (for the diagnostics report)
DEAL_LINK = re.compile(r"special|deal|promo|happy[-_ ]?hour|offer|feature|daily|weekly|wing|taco[-_ ]?tue|events?|what'?s[-_ ]on|lto|limited", re.I)
DEAL_WORDS = re.compile(r"(\$\s?\d|\d\s?¢|half[- ]?price|1/2 price|happy hour|% off|bogo|buy one|two for|2 for|special|wing (night|wednesday)|kids eat free|daily deal)", re.I)
MAX_PAGES_PER_SITE = 5
MAX_CHARS = 14000
STALE_DAYS = 14  # keep a site's last known deals this long if the site is temporarily down
TODAY = dt.date.today().isoformat()


def domain(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


# ---------------- fetching ----------------
_robots = {}


def allowed(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=10)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:  # noqa: BLE001
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(BOT, url)


def get(url):
    if not allowed(url):
        REASONS[url] = "blocked by robots.txt"
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200:
            REASONS[url] = f"HTTP {r.status_code}"
            return None
        if "html" not in ctype:
            REASONS[url] = f"not a web page ({ctype.split(';')[0] or 'unknown type'})"
            return None
        if "charset" not in ctype.lower():
            m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', r.content[:3000], re.I)
            r.encoding = m.group(1).decode() if m else (r.apparent_encoding or "utf-8")
        return r.text
    except requests.exceptions.SSLError:
        REASONS[url] = "SSL error"
    except requests.exceptions.Timeout:
        REASONS[url] = "timeout"
    except requests.exceptions.ConnectionError:
        REASONS[url] = "can't connect (site down or domain gone)"
    except Exception as e:  # noqa: BLE001
        REASONS[url] = f"error: {type(e).__name__}"
    return None


def render_js(url):
    """Optional: for sites that build their page with JavaScript. Enabled with RENDER_JS=1."""
    if os.environ.get("RENDER_JS") != "1" or not allowed(url):
        return None
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page(user_agent=UA)
            pg.goto(url, timeout=25000, wait_until="networkidle")
            html = pg.content()
            b.close()
            return html
    except Exception:  # noqa: BLE001
        return None


def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        t.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)


def candidate_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"].split("#")[0])
        if domain(href) != domain(base) or not href.startswith("http"):
            continue
        label = (a.get_text(" ", strip=True) + " " + href)
        if DEAL_LINK.search(label) and href not in out and not re.search(r"\.(pdf|jpg|png|zip)$", href, re.I):
            out.append(href)
    # prefer the most deal-like links
    out.sort(key=lambda h: (0 if re.search(r"special|deal|happy", h, re.I) else 1, len(h)))
    return out[: MAX_PAGES_PER_SITE - 1]


def read_site(site, info):
    home = get(site)
    if not home:  # OpenStreetMap links often point at an old location page: fall back to the home page
        p = urlparse(site)
        root = f"{p.scheme}://{p.netloc}/"
        if root != site:
            info["fallback"] = REASONS.get(site)
            site = root
            home = get(site)
    if home and len(visible_text(home)) < 300:
        info["js_page"] = True
        home = render_js(site) or home
    if not home:
        info["status"] = REASONS.get(site, "unknown error")
        return None
    info["status"] = "ok"
    info["home_chars"] = len(visible_text(home))
    pages = [(site, visible_text(home))]
    for link in candidate_links(home, site):
        html = get(link)
        if html:
            pages.append((link, visible_text(html)))
        time.sleep(0.5)  # be polite
    # keep only the deal-relevant chunks of each page
    blocks = []
    for url, text in pages:
        lines = text.splitlines()
        keep = set()
        for i, line in enumerate(lines):
            if DEAL_WORDS.search(line) or re.search(r"\b(mon|tues|wednes|thurs|fri|satur|sun)day", line, re.I):
                keep.update(range(max(0, i - 2), min(len(lines), i + 3)))
        if keep:
            blocks.append(f"### PAGE: {url}\n" + "\n".join(lines[i] for i in sorted(keep)))
    info["pages"] = len(pages)
    info["deal_chars"] = sum(len(b) for b in blocks)
    return "\n\n".join(blocks)[:MAX_CHARS]


# ---------------- main ----------------
DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def clean_deal(d, site_url):
    title = (d.get("title") or "").strip()
    if not title or len(title) > 90:
        return None
    days = sorted({DAYS[x[:3].lower()] for x in (d.get("days") or []) if x and x[:3].lower() in DAYS})
    t = lambda v: v if isinstance(v, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v) else None  # noqa: E731
    vu = d.get("valid_until")
    if vu and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(vu)):
        vu = None
    if vu and vu < TODAY:
        return None
    return {
        "title": title,
        "description": (d.get("description") or "").strip()[:240] or None,
        "price": (d.get("price") or "").strip()[:30] or None,
        "days": days,
        "start": t(d.get("start")),
        "end": t(d.get("end")),
        "categories": [c.strip().lower() for c in (d.get("categories") or []) if c][:4],
        "valid_until": vu,
        "dine_in_only": bool(d.get("dine_in_only")),
        "applies_to": d.get("applies_to") or "all",
        "source_url": d.get("source_url") or site_url,
    }


def match_branches(applies_to, branches):
    if applies_to in (None, "all") or not isinstance(applies_to, list):
        return [b["id"] for b in branches]
    hits = []
    for b in branches:
        hay = f"{b['name']} {b.get('address') or ''}".lower()
        if any(str(a).lower() in hay or (b.get("address") and b["address"].lower() in str(a).lower()) for a in applies_to):
            hits.append(b["id"])
    return hits or [b["id"] for b in branches]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only process N sites (for testing)")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    max_calls = int(os.environ.get("MAX_LLM_CALLS", "400"))
    monthly_cap = int(os.environ.get("MONTHLY_LLM_CALLS", "6000"))

    restaurants = json.loads(RESTAURANTS.read_text())
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    month = TODAY[:7]
    usage = cache.setdefault("_usage", {})
    used_this_month = usage.get(month, 0)

    sites = {}
    for r in restaurants:
        if r.get("website"):
            sites.setdefault(domain(r["website"]), {"url": r["website"], "branches": []})["branches"].append(r)
    keys = sorted(sites, key=lambda k: -len(sites[k]["branches"]))  # chains first: most value per call
    if args.limit:
        keys = keys[: args.limit]
    print(f"{len(keys)} sites to check · AI calls used this month: {used_this_month}/{monthly_cap}")

    # 1) fetch all sites in parallel (free)
    texts, report = {}, {}
    def work(k):
        info = {"url": sites[k]["url"], "locations": len(sites[k]["branches"])}
        report[k] = info
        return read_site(sites[k]["url"], info)
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for k, text in zip(keys, ex.map(work, keys)):
            texts[k] = text

    # 2) only changed, deal-looking sites go to the AI
    calls = changed = failed = 0
    ai_errors, ai_stopped = [], None
    for k in keys:
        text, entry = texts[k], cache.get(k, {})
        if text is None:
            failed += 1
            if entry and entry.get("ok_at", TODAY) < (dt.date.today() - dt.timedelta(days=STALE_DAYS)).isoformat():
                entry["deals"] = []  # site gone too long: drop its deals
            continue
        h = hashlib.sha256(text.encode()).hexdigest()
        entry["ok_at"] = TODAY
        if entry.get("hash") == h:
            cache[k] = entry
            continue
        if not text or not DEAL_WORDS.search(text):
            report[k]["result"] = "no deal words found"
            entry.update(hash=h, deals=[], checked_at=TODAY)
            cache[k] = entry
            continue
        if calls >= max_calls or used_this_month + calls >= monthly_cap:
            continue  # budget reached: try again tomorrow (hash not saved)
        names = sorted({b["name"] for b in sites[k]["branches"]})
        try:
            raw = extract_deals(names[0], sites[k]["url"], text)
            calls += 1
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:300]
            print(f"  ! AI error on {k}: {msg}")
            report[k]["result"] = f"AI error: {msg}"
            ai_errors.append(msg)
            if len(ai_errors) >= 3 and len(set(ai_errors[-3:])) == 1:
                print(f"Stopping AI step: the same error 3 times in a row -> {msg}")
                ai_stopped = msg
                break
            continue
        entry.update(hash=h, checked_at=TODAY, deals=[x for x in (clean_deal(d, sites[k]["url"]) for d in raw) if x])
        report[k]["result"] = f"{len(entry['deals'])} deals"
        cache[k] = entry
        changed += 1

    usage[month] = used_this_month + calls

    # diagnostics report: why each site did or didn't produce deals
    import collections
    status = collections.Counter(v.get("status", "?") for v in report.values())
    print("Site status:", ", ".join(f"{n}× {s}" for s, n in status.most_common()))
    results = collections.Counter(v.get("result", "unchanged/skipped") for v in report.values() if v.get("status") == "ok")
    print("Readable sites:", ", ".join(f"{n}× {s}" for s, n in results.most_common(8)))
    (DATA / "cache").mkdir(parents=True, exist_ok=True)
    (DATA / "cache" / "report.json").write_text(json.dumps(
        {"date": TODAY, "ai_calls": calls, "ai_stopped": ai_stopped, "status_counts": dict(status), "sites": report}, indent=1, ensure_ascii=False))
    print(f"AI calls this run: {calls} · sites with new content: {changed} · unreachable: {failed}")

    # 3) build the published file
    by_id = {r["id"]: r for r in restaurants}
    deals, used = [], set()
    for k, info in sites.items():
        for i, d in enumerate(cache.get(k, {}).get("deals", [])):
            ids = match_branches(d.get("applies_to"), info["branches"])
            d = {kk: vv for kk, vv in d.items() if kk != "applies_to"}
            d.update(id=f"{k}-{i}", restaurant_ids=ids, verified_at=cache[k].get("ok_at"))
            deals.append(d)
            used.update(ids)

    if MANUAL.exists():
        for m in json.loads(MANUAL.read_text()):
            if m.get("valid_until") and m["valid_until"] < TODAY:
                continue
            ids = m.get("restaurant_ids") or [
                r["id"] for r in restaurants
                if (m.get("brand") and (r.get("brand") or r["name"]).lower().startswith(m["brand"].lower()))
            ]
            ids = [i for i in ids if i in by_id] or []
            if ids:
                deals.append({**{k2: v for k2, v in m.items() if k2 not in ("locations", "brand", "restaurant")}, "restaurant_ids": ids})
                used.update(ids)
            elif m.get("locations"):  # places not (yet) matched in OSM: carry their own coordinates
                rids = []
                for n, loc in enumerate(m["locations"]):
                    rid = f"manual-{m['id']}-{n}"
                    by_id[rid] = {"id": rid, "name": m["restaurant"], "lat": loc["lat"], "lng": loc["lng"],
                                  "address": loc.get("address", ""), "website": m.get("website")}
                    rids.append(rid)
                deals.append({**{k2: v for k2, v in m.items() if k2 not in ("locations", "brand", "restaurant")}, "restaurant_ids": rids})
                used.update(rids)

    pub_restaurants = [{k2: by_id[i].get(k2) for k2 in ("id", "name", "lat", "lng", "address", "website")} for i in sorted(used)]
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "city": "Edmonton",
           "restaurants": pub_restaurants, "deals": deals}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    SITE_COPY.parent.mkdir(parents=True, exist_ok=True)
    SITE_COPY.write_text(OUT.read_text())
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    print(f"Published {len(deals)} deals at {len(pub_restaurants)} locations -> {OUT}")


if __name__ == "__main__":
    main()
