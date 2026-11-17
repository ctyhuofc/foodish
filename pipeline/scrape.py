"""Step 2 (daily): visit each restaurant website, pull out its deals, publish each city's deals.json
(Edmonton: data/deals.json, others: data/<city>/deals.json; the list of cities is cities.json).

Why it's cheap:
  * One visit per *website*, not per location — a chain like Boston Pizza is read once and
    the deals are applied to every branch in that city.
  * Change detection — each site's text is fingerprinted. If nothing changed since yesterday
    (true for most "Wing Wednesday"-type deals), the saved deals are reused and no AI call is made.
  * Keyword pre-filter — pages with no price/deal words never reach the AI.
  * Hard caps — MAX_LLM_CALLS per run and MONTHLY_LLM_CALLS per month.

Usage:  python pipeline/scrape.py                      (every city; needs LLM_API_KEY in the environment)
        python pipeline/scrape.py vancouver            (one city)
        python pipeline/scrape.py vancouver --limit 30 (try it on 30 sites first)
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import collections
import sys

import requests
from bs4 import BeautifulSoup

import cities
import deal_state as ds  # shared deal-timing rules (spec/deal-states.json)
import site_state as ss  # the per-site lifecycle: unchanged / extracted / deferred_budget / ...
import llm  # llm.requests_made: every request sent to the AI, for the limits below
from llm import PROMPT_VERSION, UnreadableReply, extract_deals

ROOT = Path(__file__).resolve().parent.parent

BOT = "FoodishBot"  # the name robots.txt rules are matched against
UA = "Mozilla/5.0 (compatible; FoodishBot/1.0; +https://www.foodish.ca/bot)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-CA,en;q=0.8"}
REASONS = {}  # url -> why it couldn't be read (for the diagnostics report)
DEAL_LINK = re.compile(r"special|deal|promo|happy[-_ ]?hour|offer|feature|daily|weekly|wing|taco[-_ ]?tue|events?|what'?s[-_ ]on|lto|limited", re.I)
DEAL_WORDS = re.compile(r"(\$\s?\d|\d\s?¢|half[- ]?price|1/2 price|happy hour|% off|bogo|buy one|two for|2 for|special|wing (night|wednesday)|kids eat free|daily deal)", re.I)
MAX_PAGES_PER_SITE = 5
MAX_CHARS = 14000
REMOVED = ROOT / "data" / "removed.json"  # restaurants that asked to be taken off Foodish (shared by every city)
STALE_DAYS = 14  # keep a site's last known deals this long if the site is temporarily down
TODAY = dt.date.today().isoformat()


def removed_sites() -> set[str]:
    """Domains in data/removed.json: never fetched, and their deals are cleared on the next run."""
    try:
        raw = json.loads(REMOVED.read_text())
    except (OSError, ValueError):
        return set()
    return {domain(u if "://" in u else "https://" + u) for u in raw.get("sites", []) if isinstance(u, str) and u.strip()}


def domain(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


# ---------------- fetching ----------------
# Every download has limits, so one broken or hostile website can't stall or crash the whole run
# (a server that sends one byte every few seconds, or a small "zip bomb" that unzips to gigabytes).
PAGE_BYTES = 3_000_000    # largest page we read (after unzipping); restaurant pages are well under 1 MB
ROBOTS_BYTES = 500_000    # largest robots.txt (Google reads at most 500 KB too)
FETCH_SECONDS = 30        # one download, start to finish, redirects included
TIMEOUTS = (10, 15)       # seconds to connect; seconds of silence allowed while reading
HARD_SECONDS = FETCH_SECONDS + TIMEOUTS[1]  # give up on a download after this, whatever it is stuck on
SITE_SECONDS = 90         # after this long on one site, don't open more of its pages
CHUNK = 16_384            # read in small pieces, so an unzipped piece can't be huge before it's counted
TOO_LARGE, TOO_SLOW = "too large", "too slow"  # reasons in report.json


class FetchStopped(Exception):
    """A download went over a limit; the message is TOO_LARGE or TOO_SLOW."""


def download(url, limit, want=lambda r: True, deadline=FETCH_SECONDS, clock=time.monotonic, opener=None):
    """(response, body bytes) for `url`, reading at most `limit` bytes within `deadline` seconds, or
    FetchStopped. The body is only read when want(response) says so (else it is None): a 404 isn't
    downloaded. The response is always closed. `clock` and `opener` are swapped in by the tests."""
    start = clock()
    r = (opener or requests.get)(url, headers=HEADERS, timeout=TIMEOUTS, stream=True, allow_redirects=True)
    try:
        if not want(r):
            return r, None
        size = r.headers.get("content-length", "")
        if size.isdigit() and int(size) > limit:
            raise FetchStopped(TOO_LARGE)
        body = bytearray()
        for chunk in r.iter_content(CHUNK):  # unzipped as it comes
            body += chunk
            if len(body) > limit:
                raise FetchStopped(TOO_LARGE)
            if clock() - start > deadline:
                raise FetchStopped(TOO_SLOW)
        return r, bytes(body)
    finally:
        r.close()


def bounded(fn, *args, seconds=None, **kw):
    """fn(*args, **kw), but give up after `seconds` (HARD_SECONDS). The timeouts above can't cover
    everything (a slow address lookup, headers sent a byte at a time), so the download runs in a helper
    thread; if it isn't back in time we move on and it finishes or fails by itself, unseen."""
    box = {}

    def run():
        try:
            box["ok"] = fn(*args, **kw)
        except BaseException as e:  # noqa: BLE001  handed back to the caller below
            box["err"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(HARD_SECONDS if seconds is None else seconds)
    if t.is_alive():
        raise FetchStopped(TOO_SLOW)
    if "err" in box:
        raise box["err"]
    return box["ok"]


def _guess_encoding(body):
    """What requests' r.apparent_encoding does: guess the text encoding from the bytes."""
    from requests.compat import chardet
    return chardet.detect(body)["encoding"] if chardet else None


def as_text(body, encoding):
    """Bytes -> text, the way requests' r.text does it (guess the encoding if none is given)."""
    try:
        return str(body, encoding or _guess_encoding(body) or "utf-8", errors="replace")
    except (LookupError, TypeError):
        return str(body, errors="replace")


_robots = {}


def allowed(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r, body = bounded(download, base + "/robots.txt", ROBOTS_BYTES, want=lambda r: r.status_code == 200)
            rp.parse(as_text(body, requests.utils.get_encoding_from_headers(r.headers)).splitlines() if r.status_code == 200 else [])
        except Exception:  # noqa: BLE001  as before: a robots.txt we can't read (now also: too big or too slow) blocks nothing
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(BOT, url)


def get(url):
    if not allowed(url):
        REASONS[url] = ss.READ_ROBOTS
        return None
    try:
        page = lambda r: r.status_code == 200 and "html" in r.headers.get("content-type", "")  # noqa: E731
        r, body = bounded(download, url, PAGE_BYTES, want=page)
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200:
            REASONS[url] = f"HTTP {r.status_code}"
            return None
        if "html" not in ctype:
            REASONS[url] = f"not a web page ({ctype.split(';')[0] or 'unknown type'})"
            return None
        if "charset" in ctype.lower():
            encoding = requests.utils.get_encoding_from_headers(r.headers)
        else:  # the page's own <meta charset>, else a guess from the bytes
            m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:3000], re.I)
            encoding = m.group(1).decode() if m else (_guess_encoding(body) or "utf-8")
        return as_text(body, encoding)
    except FetchStopped as e:
        REASONS[url] = str(e)
    except requests.exceptions.SSLError:
        REASONS[url] = "SSL error"
    except requests.exceptions.Timeout:
        REASONS[url] = "timeout"
    except requests.exceptions.ConnectionError:
        REASONS[url] = "can't connect (site down or domain gone)"
    except Exception as e:  # noqa: BLE001
        REASONS[url] = f"error: {type(e).__name__}"
    return None


# ---------------- the browser, for JavaScript-only sites ----------------
# Each page is opened by a separate copy of this script (render_page() below), so that:
#   * it can be stopped for sure after RENDER_SECONDS, even if the page's own code never lets go;
#   * the browser never sees the AI key or any other secret: it gets only BROWSER_ENV;
#   * a browser that crashes or eats memory takes down only that copy, not the run.
RENDER_SECONDS = 60
LAUNCH_FAILED = 3  # exit code: the browser didn't start (so the sandbox, not the page, is the problem)
BROWSER_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TMP", "TEMP", "LD_LIBRARY_PATH",
               "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "SYSTEMROOT")
_sandbox = {"on": True}  # turned off for the rest of the run if the sandboxed browser can't start
_sandbox_lock = threading.Lock()


def browser_env():
    """The few settings the browser needs, and nothing else (no LLM_API_KEY, no GitHub token)."""
    return {k: v for k, v in os.environ.items() if k.upper() in BROWSER_ENV or k.startswith("PLAYWRIGHT_")}


def render_page(url, sandbox):
    """In the separate copy: open `url` in Chromium and write its HTML to stdout (nothing if over PAGE_BYTES).
    The browser runs in Chromium's sandbox, can't download files, and doesn't run the site's service worker."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(chromium_sandbox=sandbox, env=browser_env())
        except Exception:  # noqa: BLE001
            sys.exit(LAUNCH_FAILED)
        try:
            ctx = b.new_context(user_agent=UA, accept_downloads=False, service_workers="block")
            pg = ctx.new_page()
            pg.goto(url, timeout=25000, wait_until="networkidle")
            html = pg.content().encode("utf-8", "replace")
        finally:
            b.close()
    if len(html) <= PAGE_BYTES:
        sys.stdout.buffer.write(html)


def _render_cmd(url, sandbox):
    return [sys.executable, str(Path(__file__).resolve()), "--render-page", url] + ([] if sandbox else ["--no-sandbox"])


def _render(url, sandbox):
    """(html or None, exit code) from one separate copy, stopped after RENDER_SECONDS."""
    try:
        proc = subprocess.Popen(_render_cmd(url, sandbox), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=browser_env(), start_new_session=True)
    except OSError:
        return None, 1
    try:
        out, _ = proc.communicate(timeout=RENDER_SECONDS)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):  # TERM lets Playwright close its browser; then KILL
            try:
                os.killpg(proc.pid, sig)
            except OSError:
                pass
            try:
                proc.communicate(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue
        return None, -1
    if proc.returncode != 0 or not out or len(out) > PAGE_BYTES:
        return None, proc.returncode
    return out.decode("utf-8", "replace"), 0


def render_js(url):
    """For sites that build their page with JavaScript. On in the daily workflow unless RENDER_JS=0."""
    if os.environ.get("RENDER_JS") != "1" or not allowed(url):
        return None
    html, code = _render(url, _sandbox["on"])
    if code == LAUNCH_FAILED and _sandbox["on"]:
        # GitHub's machines sometimes refuse the sandbox. Losing ~450 JavaScript-only sites silently would be
        # worse, so carry on without it, but say so once in the log.
        with _sandbox_lock:
            if _sandbox["on"]:
                _sandbox["on"] = False
                print("::warning title=Browser sandbox off::Chromium couldn't start with its sandbox, so "
                      "JavaScript-only sites are read without it this run. Check the workflow's browser step.")
        html, code = _render(url, False)
    return html


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


_read = {}  # site url -> (text, facts): a chain with branches in two cities is fetched once per run


def read_site_once(site, info):
    if site not in _read:
        facts = {}
        _read[site] = (read_site(site, facts), facts)
    text, facts = _read[site]
    info.update(facts)
    return text


_full_text = {}  # site url -> all visible text read this run (for confirmed_site; never saved)


def read_site(site, info, clock=time.monotonic):
    started = clock()
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
    info["status"] = ss.READ_OK
    info["home_chars"] = len(visible_text(home))
    pages = [(site, visible_text(home))]
    _full_text[site] = pages[0][1][:200_000]
    for link in candidate_links(home, site):
        if clock() - started > SITE_SECONDS:  # a slow site: what we have is enough for today
            info["cut_short"] = True
            break
        html = get(link)
        if html:
            pages.append((link, visible_text(html)))
            _full_text[site] = (_full_text.get(site, "") + "\n" + pages[-1][1])[:400_000]
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


ELSEWHERE = re.compile(r"\b(?:at|in) (?:our |the )?((?:[A-Z][a-zA-Z.'-]+ ?){1,3}(?:(?:,| or| and| &) (?:[A-Z][a-zA-Z.'-]+ ?){1,3})*)"
                       r"(?:location|locations|store|stores|branch|branches|restaurant|restaurants)\b")
# Towns and cities outside the areas Foodish covers. A deal "only at our Brampton or Milton locations" is left
# out; one at a neighbourhood or street we don't know ("the English Bay location") is kept.
OTHER_PLACES = {
    # Ontario
    "Brampton", "Mississauga", "Milton", "Oakville", "Burlington", "Hamilton", "Vaughan", "Markham", "Richmond Hill",
    "Newmarket", "Aurora", "Pickering", "Ajax", "Whitby", "Oshawa", "Barrie", "Kitchener", "Waterloo", "Cambridge",
    "Guelph", "London", "Windsor", "Ottawa", "Kingston", "Niagara Falls", "St. Catharines", "Woodbridge", "Concord",
    "Thornhill", "Stouffville", "Bolton", "Orangeville", "Sudbury", "Peterborough", "Belleville",
    # B.C.
    "Surrey", "Coquitlam", "Port Coquitlam", "Port Moody", "Langley", "Delta", "Ladner", "Tsawwassen", "White Rock",
    "Maple Ridge", "Abbotsford", "Chilliwack", "Squamish", "Whistler", "Victoria", "Nanaimo", "Kelowna", "Kamloops",
    # Alberta
    "Calgary", "Red Deer", "Leduc", "Spruce Grove", "Stony Plain", "Fort Saskatchewan", "Beaumont", "Lethbridge",
    "Grande Prairie", "Fort McMurray", "Airdrie", "Okotoks", "Canmore", "Banff", "Camrose", "Medicine Hat",
    # elsewhere
    "Montreal", "Montréal", "Quebec City", "Laval", "Gatineau", "Winnipeg", "Regina", "Saskatoon", "Halifax",
    "Moncton", "Fredericton", "St. John's", "Charlottetown", "Seattle", "Bellingham",
}
OTHER_LOWER = {p.lower() for p in OTHER_PLACES}


def limited_elsewhere(d: dict, allowed: list[str]) -> bool:
    """True if a deal says it's only at named branches and every one is in a town outside this city's area:
    'Dine in at our Brampton or Milton locations' on a Toronto restaurant. `allowed` (the city and the areas
    it covers) is never treated as outside, so a covered suburb like Burnaby still counts for Vancouver."""
    text = f"{d.get('title') or ''}. {d.get('description') or ''}"
    ok = {a.lower() for a in allowed}
    for m in ELSEWHERE.finditer(text):
        named = [n.strip().lower() for n in re.split(r",| or | and | & ", m.group(1)) if n.strip()]
        if named and all(n in OTHER_LOWER and n not in ok for n in named):
            return True
    return False


def _squash(text) -> str:
    """Lower case, letters and digits only: for checking a quote or price against the page text."""
    return re.sub(r"[^a-z0-9%¢$]+", " ", str(text or "").lower()).strip()


def price_on_page(price, page: str) -> bool:
    """A price or saving the AI states must be written on the page: every number in '$4.75', '59¢' or
    '50% off' has to appear there. Prices with no number ('BOGO', 'Free') aren't checked."""
    nums = re.findall(r"\d+(?:[.,]\d+)?", str(price or ""))
    return all(re.search(rf"(?<![\d.]){re.escape(n)}(?!\d|[.,]\d)", page) for n in nums)


def quote_on_page(quote, page_squashed: str) -> bool:
    """The AI's quote is (nearly) word for word on the page: its first 8 words, squashed, are found there."""
    words = _squash(quote).split()
    return bool(words) and " ".join(words[:8]) in page_squashed


def parse_days(raw) -> list[int]:
    """["Mon","Wed"], "Mon-Fri", ["Fri-Sun"] or "Tuesdays and Thursdays" -> day numbers (Sun=0).
    Words that aren't days are ignored; an empty result means nothing could be read."""
    items = [raw] if isinstance(raw, str) else [x for x in (raw or []) if isinstance(x, str)]
    out = set()
    for item in items:
        for a, b in re.findall(r"([A-Za-z]{3,})\s*(?:-|–|to|through)\s*([A-Za-z]{3,})", item):
            if a[:3].lower() in DAYS and b[:3].lower() in DAYS:
                i, j = DAYS[a[:3].lower()], DAYS[b[:3].lower()]
                out.update((i + n) % 7 for n in range((j - i) % 7 + 1))
        out.update(DAYS[w[:3].lower()] for w in re.findall(r"[A-Za-z]{3,}", item) if w[:3].lower() in DAYS)
    return sorted(out)


def _date(v):
    return v if v and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)) else None


MAX_URL = 500  # longest deal link we publish


def safe_source(url, site_url) -> str:
    """The deal's link, as the AI gave it, only if it is a plain web address on the restaurant's own site
    (same domain, www. or not; not a sub-site). A website can write text that talks the AI into a
    'javascript:' link or a look-alike phishing page, so anything else becomes the restaurant's home page.
    (Checked against every deal in data/ in Nov 2026: no AI link pointed at another host or a sub-domain.)"""
    if not isinstance(url, str) or len(url) > MAX_URL or any(c in url for c in "\r\n\t\\ <>\"'`"):
        return site_url
    try:
        p = urlparse(url)
        ok = p.scheme in ("http", "https") and p.hostname and not p.username and not p.password \
            and "@" not in p.netloc and domain(url) == domain(site_url)
    except ValueError:  # e.g. a broken port or IPv6 address
        ok = False
    return url if ok else site_url


def clean_deal(d, site_url, page_text=None, today=None):
    """Check and tidy one deal from the AI; None drops it. With page_text (what the AI read), a deal is
    also dropped if its price or its quote isn't on the page: the AI made it up or misread it.
    `today` is the date on the city's clock (default: the runner's date)."""
    title = (d.get("title") or "").strip()
    if not title or len(title) > 90:
        return None
    raw_days = d.get("days")
    days = parse_days(raw_days)
    if raw_days and not days:
        return None  # days we can't read: dropping beats showing it every day
    t = lambda v: v if isinstance(v, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v) else None  # noqa: E731
    vu, vf = _date(d.get("valid_until")), _date(d.get("valid_from"))
    if vu and vu < (today or TODAY):
        return None
    if vu and vf and vf > vu:
        vf = None
    price = (d.get("price") or "").strip()[:12] or None
    if page_text is not None:
        if price and not price_on_page(price, page_text):
            return None
        if not quote_on_page(d.get("quote"), _squash(page_text)):
            return None
    return {
        "title": title,
        "description": (d.get("description") or "").strip()[:240] or None,
        "price": price,
        "price_unit": (d.get("price_unit") or "").strip()[:20] or None,
        "days": days,
        "start": t(d.get("start")),
        "end": t(d.get("end")),
        "categories": [c.strip().lower() for c in (d.get("categories") or []) if c][:4],
        "valid_from": vf,
        "valid_until": vu,
        "dine_in_only": bool(d.get("dine_in_only")),
        "applies_to": d.get("applies_to") or "all",
        "source_url": safe_source(d.get("source_url"), site_url),
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


def place_words(city) -> list[str]:
    """Names a restaurant's own site is likely to mention: the city, province and areas it covers."""
    covers = next((c.get("covers", "") for c in cities._config()["cities"] if c["id"] == city.id), "")
    words = {city.name, city.region} | {w for w in re.findall(r"[A-Z][a-zA-Z.'-]{3,}(?: [A-Z][a-z]{3,})?", covers)}
    return sorted(w for w in words if w not in ("The City", "City", "Plus"))


def clean_or_drop(d, site_url, page_text=None, today=None):
    """clean_deal(), but a deal it can't read (e.g. a number where text belongs) is dropped, not a crash."""
    try:
        return clean_deal(d, site_url, page_text, today)
    except Exception:  # noqa: BLE001
        return None


def confirmed_site(text: str, branches: list[dict], places: list[str]) -> bool:
    """A website taken from Overture Maps (not from the restaurant's own OpenStreetMap entry) is only used
    if its pages mention the city, an area it covers, or a branch's street. This stops a name match from
    pinning some unrelated site's deals (e.g. a comics publisher) on a restaurant."""
    if not all(b.get("website_source") == "overture" for b in branches):
        return True
    low = text.lower()
    streets = {m.group(1) for b in branches for m in [re.search(r"\d+[a-z]?\s+([A-Za-z][A-Za-z'.-]{3,})", b.get("address") or "")] if m}
    if not any(w.lower() in low for w in [*places, *streets]):
        return False
    # ...and the restaurant's own name, so a similar-named neighbour's site isn't used
    # ("California Pizza Kitchen" matched to State & Main's site)
    return any(names_on_page(b.get("name") or "", low) for b in branches)


NAME_FILLER = {"restaurant", "restaurants", "bar", "grill", "cafe", "café", "kitchen", "pizza", "pizzeria", "sushi",
               "the", "and", "pub", "bistro", "house", "lounge", "eatery", "diner", "bakery", "tavern", "express",
               "food", "foods", "coffee", "tea", "shop", "co", "ltd", "inc", "of", "on", "at", "in", "de", "la", "le"}


def names_on_page(name: str, low: str) -> bool:
    """True if the page mentions the restaurant: every distinctive word of its name (up to three),
    e.g. 'liberty' for 'Liberty Pizza', or the whole name squashed together when every word is generic."""
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFKD", name.lower()) if not unicodedata.combining(c))
    words = [w for w in re.findall(r"[a-z0-9]+", plain.replace("'", "")) if w not in NAME_FILLER and len(w) >= 3]
    page = "".join(c for c in unicodedata.normalize("NFKD", low) if not unicodedata.combining(c)).replace("'", "")
    if words:
        return all(re.search(rf"\b{re.escape(w)}", page) for w in words[:3])
    squashed = re.sub(r"[^a-z0-9]", "", plain)
    return bool(squashed) and squashed in re.sub(r"[^a-z0-9]", "", page)


def used_elsewhere(city, month):
    """AI calls other cities have made this month: MONTHLY_LLM_CALLS is one budget for all of Foodish."""
    total = 0
    for other in cities.all_cities():
        if other.id != city.id and other.cache.exists():
            try:
                total += json.loads(other.cache.read_text()).get("_usage", {}).get(month, 0)
            except ValueError:
                pass
    return total


def run_city(city, args):
    """Read one city's restaurant websites and publish its deals file."""
    max_calls = int(os.environ.get("MAX_LLM_CALLS", "400"))        # per city, per run
    monthly_cap = int(os.environ.get("MONTHLY_LLM_CALLS", "12000"))  # all cities together, per month

    restaurants = json.loads(city.restaurants.read_text())
    # "Has this deal's last day passed?" uses the city's own date: the runner is on UTC, which is already
    # tomorrow on an evening run (after 6 pm in Edmonton).
    city_today = ds.now_in(city.timezone).date
    cache = json.loads(city.cache.read_text()) if city.cache.exists() else {}
    month = TODAY[:7]
    usage = cache.setdefault("_usage", {})
    own_usage = usage.get(month, 0)
    used_this_month = own_usage + used_elsewhere(city, month)

    sites = {}
    for r in restaurants:
        if r.get("website"):
            sites.setdefault(domain(r["website"]), {"url": r["website"], "branches": []})["branches"].append(r)
    keys = sorted(sites, key=lambda k: -len(sites[k]["branches"]))  # chains first: most value per call
    if args.limit:
        keys = keys[: args.limit]
    print(f"{city.name}: {len(keys)} sites to check · AI calls used this month: {used_this_month}/{monthly_cap}")

    # 1) fetch all sites in parallel (free)
    texts, report = {}, {}
    places = place_words(city)
    removed = removed_sites()
    def work(k):
        info = {"url": sites[k]["url"], "locations": len(sites[k]["branches"])}
        report[k] = info
        if k in removed:
            info["status"] = ss.READ_REMOVED
            return None
        try:
            text = read_site_once(sites[k]["url"], info)
            if text and not confirmed_site(_full_text.get(sites[k]["url"], text), sites[k]["branches"], places):
                info["unconfirmed"] = True  # treated as a site with no deals, so nothing wrong is published
                return ""
            return text
        except Exception as e:  # noqa: BLE001  one odd website (e.g. a broken link) is unreadable today, not a city-wide stop
            info["status"] = f"error: {type(e).__name__}"
            return None
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for k, text in zip(keys, ex.map(work, keys)):
            texts[k] = text

    # 2) only changed, deal-looking sites go to the AI. What happens to each site is decided by
    #    site_state.next_step(); every site ends the run with one named outcome in report.json.
    calls = changed = failed = 0
    breaker = ss.AIBreaker()
    stale_before = (dt.date.today() - dt.timedelta(days=STALE_DAYS)).isoformat()
    for k in keys:
        text, entry = texts[k], cache.get(k, {})
        h = hashlib.sha256(text.encode()).hexdigest() if text is not None else None
        step = ss.next_step(
            readable=text is not None, fingerprint=h, has_deal_words=bool(text and DEAL_WORDS.search(text)),
            entry=entry, prompt_version=PROMPT_VERSION, stale_before=stale_before,
            budget_left=calls < max_calls and used_this_month + calls < monthly_cap, ai_stopped=breaker.stopped,
            blocked=report[k].get("status") in ss.BLOCKED_READS, today=TODAY,
        )
        outcome = step.outcome
        if text is None:
            failed += 1
            if step.action == ss.CLEAR:
                entry["deals"] = []  # site gone too long: drop its deals
        else:
            entry["ok_at"] = TODAY
            if step.action == ss.REUSE:
                cache[k] = entry
            elif step.action == ss.CLEAR:  # readable, but nothing deal-like on it
                report[k]["result"] = "no deal words found"
                entry.update(hash=h, pv=PROMPT_VERSION, deals=[], checked_at=TODAY)
                entry.pop("ai_fail", None)
                cache[k] = entry
            elif step.action == ss.CALL_AI:
                names = sorted({b["name"] for b in sites[k]["branches"]})
                sent_before = llm.requests_made
                try:
                    raw = extract_deals(names[0], sites[k]["url"], text, city=city.name, region=city.region)
                except Exception as e:  # noqa: BLE001
                    msg = str(e)[:300]
                    print(f"  ! AI error on {k}: {msg}")
                    report[k]["result"] = f"AI error: {msg}"
                    outcome = ss.AI_ERROR
                    if isinstance(e, UnreadableReply):  # this text confuses the AI: don't pay to ask again tomorrow
                        entry["ai_fail"] = ss.ai_failed(entry.get("ai_fail"), h, PROMPT_VERSION, TODAY)
                        report[k]["retry_from"] = entry["ai_fail"]["until"]
                        cache[k] = entry
                    breaker.failure(msg)
                    if breaker.stopped:
                        print(f"Stopping AI calls: the same error {ss.SAME_ERROR_LIMIT} times in a row -> {msg}")
                else:
                    breaker.success()
                    kept = [x for x in (clean_or_drop(d, sites[k]["url"], text, city_today) for d in raw) if x]
                    entry.pop("ai_fail", None)
                    entry.update(hash=h, pv=PROMPT_VERSION, checked_at=TODAY, deals=kept[:6])
                    report[k]["result"] = f"{len(entry['deals'])} deals"
                    if len(kept) < len(raw):  # price or quote not on the page, unreadable days, ...
                        report[k]["dropped"] = len(raw) - len(kept)
                    cache[k] = entry
                    changed += 1
                    outcome = ss.EXTRACTED
                finally:
                    # every request sent counts toward both limits, failed ones too (up to 3 per site)
                    calls += llm.requests_made - sent_before
            # KEEP (deferred): the new fingerprint isn't saved, so tomorrow's run tries the AI again
            # (deferred_ai_backoff: once its pause is over)
        report[k]["outcome"] = outcome
    ai_stopped = breaker.stopped_with

    usage[month] = own_usage + calls  # AI requests sent this month, including ones that failed

    # diagnostics report: why each site did or didn't produce deals
    status = collections.Counter(v.get("status", "?") for v in report.values())
    print("Site status:", ", ".join(f"{n}× {s}" for s, n in status.most_common()))
    results = collections.Counter(v.get("result", "unchanged/skipped") for v in report.values() if v.get("status") == ss.READ_OK)
    print("Readable sites:", ", ".join(f"{n}× {s}" for s, n in results.most_common(8)))
    outcomes = collections.Counter(v.get("outcome") for v in report.values())
    print("Outcomes:", ", ".join(f"{n}× {o}" for o, n in outcomes.most_common()))
    city.report.parent.mkdir(parents=True, exist_ok=True)
    city.report.write_text(json.dumps(
        {"date": TODAY, "city": city.id, "ai_calls": calls, "ai_stopped": ai_stopped, "status_counts": dict(status),
         "outcome_counts": {o: outcomes.get(o, 0) for o in ss.OUTCOMES}, "sites": report}, indent=1, ensure_ascii=False))
    dropped = sum(v.get("dropped", 0) for v in report.values())
    print(f"AI calls this run: {calls} · sites with new content: {changed} · unreachable: {failed}"
          f" · AI deals dropped by checks (price or quote not on the page, unreadable days): {dropped}")

    # 3) build the published file
    by_id = {r["id"]: r for r in restaurants}
    deals, used, dropped_elsewhere = [], set(), 0
    for k, info in sites.items():
        for i, d in enumerate(cache.get(k, {}).get("deals", [])):
            if not ds.is_live(d, city_today):
                continue  # its valid_until has passed since the AI read it
            if limited_elsewhere(d, places):
                dropped_elsewhere += 1
                continue  # only at branches outside this city ("at our Brampton or Milton locations")
            ids = match_branches(d.get("applies_to"), info["branches"])
            d = {kk: vv for kk, vv in d.items() if kk != "applies_to"}
            d.update(id=f"{k}-{i}", restaurant_ids=ids, verified_at=cache[k].get("ok_at"))
            deals.append(d)
            used.update(ids)

    if city.manual_deals.exists():
        for m in json.loads(city.manual_deals.read_text()):
            if m.get("valid_until") and m["valid_until"] < city_today:
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
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "city": city.name,
           # this city's clock: deal days and times are in this local time. utc_offset_hours is the offset in
           # effect now; observes_dst says whether it changes in spring and fall (Toronto) or never (AB, BC)
           "city_id": city.id, "timezone": city.timezone, "observes_dst": city.observes_dst,
           "utc_offset_hours": int(ds.local_time(city.timezone).utcoffset().total_seconds() // 3600),
           # how to tell whether a deal is on (days, times, valid dates): same rules for every app reading this file
           "rules": "https://github.com/ctyhuofc/foodish/blob/main/spec/deal-states.json",
           # where the restaurant list comes from (both licences ask for credit when the data is shared)
           "attribution": "Places © OpenStreetMap contributors (ODbL, openstreetmap.org/copyright) and "
                          "Overture Maps Foundation (CDLA-Permissive-2.0 / Apache-2.0, overturemaps.org). "
                          "Deals from each restaurant's own website.",
           "restaurants": pub_restaurants, "deals": deals}
    city.deals.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    city.site_copy.parent.mkdir(parents=True, exist_ok=True)
    city.site_copy.write_text(city.deals.read_text())
    city.cache.parent.mkdir(parents=True, exist_ok=True)
    city.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    print(f"Published {len(deals)} deals at {len(pub_restaurants)} locations -> {city.deals}"
          f" ({dropped_elsewhere} left out: only at branches outside {city.name})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cities", nargs="*", help="city ids from cities.json (default: every city)")
    ap.add_argument("--limit", type=int, default=0, help="only process N sites per city (for testing)")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    failed = []
    for city in cities.pick(args.cities):
        if not city.restaurants.exists():
            # discover.py hasn't found this city's restaurants yet (it reports its own errors)
            print(f"::warning title={city.name} skipped::No restaurant list yet ({city.restaurants.relative_to(ROOT)}). "
                  f"Run the workflow with task 'discover' or: python pipeline/discover.py {city.id}")
            continue
        try:
            run_city(city, args)
        except Exception as e:  # noqa: BLE001  one city failing must not stop the others
            print(f"::error title=Deal refresh failed for {city.name}::{type(e).__name__}: {e}")
            failed.append(city.name)
    if failed:
        sys.exit(f"Deal refresh failed for: {', '.join(failed)}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--render-page"]:  # the separate copy that runs the browser (see render_js)
        render_page(sys.argv[2], sandbox="--no-sandbox" not in sys.argv[3:])
    else:
        main()
