"""Step 1 (weekly): find every restaurant / bar / café in each city from OpenStreetMap.

Free, no API key. Writes each city's restaurants.json (Edmonton: data/restaurants.json,
others: data/<city>/restaurants.json). The area searched is the city's "bbox" in cities.json.

Usage:  python pipeline/discover.py              (every city)
        python pipeline/discover.py vancouver    (one city)
"""
import collections
import json
import os
import unicodedata
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

import cities

ROOT = Path(__file__).resolve().parent.parent
BRAND_SITES = ROOT / "data" / "brand_sites.json"       # brand -> website overrides (optional, shared by all cities)

AMENITIES = "restaurant|fast_food|pub|bar|cafe|biergarten|ice_cream"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
UA = "FoodishBot/1.0 (+https://www.foodish.ca/bot)"



def query(bbox) -> str:
    box = ",".join(str(x) for x in bbox)  # south, west, north, east
    return f"""
[out:json][timeout:180];
(
  node["amenity"~"^({AMENITIES})$"]["name"]({box});
  way["amenity"~"^({AMENITIES})$"]["name"]({box});
);
out center tags;
"""


# Links that aren't a restaurant's own website (social pages, directories, delivery and booking apps).
# Matched on whole domain labels, so "x.com" never blocks "x.company.ca" and "yelp" never blocks "yelpizza.ca".
NOT_A_RESTAURANT_SITE = {
    "facebook", "instagram", "linktr", "tiktok", "twitter", "x", "youtube", "linkedin", "foursquare",
    "doordash", "skipthedishes", "ubereats", "grubhub", "fantuan", "ritual", "yelp", "tripadvisor",
    "pagesjaunes", "yellowpages", "google", "goo", "opentable", "zomato", "allmenus", "menupages", "mapquest",
    # shared listing / guide / menu-hosting sites that many unrelated places link to
    "timeout", "blogto", "zomi",
}


def not_restaurant_site(host: str) -> bool:
    labels = host.lower().split(":")[0].removeprefix("www.").split(".")
    # the site's own name is the label before the ending: yelp.ca, m.facebook.com, tripadvisor.co.uk
    end = 2 if len(labels) > 2 and labels[-2] in {"co", "com", "org", "net"} else 1
    return len(labels) > end and labels[-end - 1] in NOT_A_RESTAURANT_SITE


def norm_site(url):
    if not url:
        return None
    url = url.strip().split(";")[0]
    if not re.match(r"^https?://", url):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc or not_restaurant_site(p.netloc):
        return None  # social pages, directories and delivery apps: not the restaurant's own site
    return f"{p.scheme}://{p.netloc.lower()}{p.path.rstrip('/') or ''}"


def address(t):
    num, street = t.get("addr:housenumber"), t.get("addr:street")
    if num and street:
        return f"{num} {street}"
    return t.get("addr:full") or street or ""


def fetch(bbox):
    last = None
    for url in OVERPASS:
        for attempt in range(3):
            try:
                r = requests.post(url, data={"data": query(bbox)}, headers={"User-Agent": UA}, timeout=240)
                if r.status_code == 200:
                    return r.json()["elements"]
                last = f"HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                last = str(e)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError(f"OpenStreetMap (Overpass) didn't answer: {last}")


# Names too generic to treat as one business: two "Café"s in a city are usually unrelated.
GENERIC_NAMES = {"cafe", "coffee", "coffeeshop", "restaurant", "pizza", "sushi", "pho", "bar", "pub", "grill",
                 "kitchen", "deli", "bakery", "cafeteria", "foodcourt", "bistro", "diner", "tea", "bubbletea",
                 "shawarma", "donair", "burger", "burgers", "noodles", "ramen", "takeout", "snackbar", "canteen"}


def name_key(name: str | None) -> str:
    """'Thaï Express', 'thai express!' and 'Thai-Express' all become 'thaiexpress'."""
    s = unicodedata.normalize("NFKD", (name or "").lower().replace("&", "and"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)))


def load_brand_sites() -> dict:
    """data/brand_sites.json: chain name -> website, for chains whose branches have none on OpenStreetMap.
    Keys starting with "_" are notes. Shared by every city, so list only real chains, not common names."""
    raw = json.loads(BRAND_SITES.read_text()) if BRAND_SITES.exists() else {}
    return {name_key(k): v for k, v in raw.items() if not k.startswith("_")}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def shared_site(urls: list[str]) -> str | None:
    """The one website a chain's branches agree on, or None if they don't.
    Counts by domain, so one odd branch (a Starbucks inside a Mac's that lists macs.ca) can't win:
    the domain must be listed by more than half of the branches that list one. Shares the page most
    of them list, or the domain's home page when each branch lists its own store page."""
    hosts = collections.Counter(_host(u) for u in urls)
    host, n = hosts.most_common(1)[0]
    if n * 2 <= len(urls):
        return None  # branches disagree: better no website than the wrong restaurant's
    on_host = collections.Counter(u for u in urls if _host(u) == host)
    url, count = min(on_host.items(), key=lambda kv: (-kv[1], len(kv[0])))
    if count == 1 and len(on_host) > 1:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    return url


def link_websites(rows: list[dict], brand_sites: dict) -> int:
    """Give branches with no website one they can share. Only fills gaps: a listed website is never replaced.
      1. other branches in this city with the same brand tag or the same (non-generic) name agree on one
         (see shared_site; if they disagree, the gap is left)
      2. the chain is in brand_sites.json
    Returns how many places got a website."""
    listed = collections.defaultdict(list)
    for r in rows:
        if r.get("website"):
            for k in {name_key(r.get("brand")), name_key(r["name"])} - {""}:
                listed[k].append(r["website"])
    known = {k: shared_site(urls) for k, urls in listed.items()}
    filled = 0
    for r in rows:
        if r.get("website"):
            continue
        for k in [name_key(r.get("brand")), name_key(r["name"])]:
            if not k or k in GENERIC_NAMES:
                continue
            site = known.get(k) or norm_site(brand_sites.get(k))
            if site:
                r["website"] = site
                filled += 1
                break
    return filled


def add_overture(city: cities.City, rows: list[dict]) -> None:
    """Fill remaining gaps from Overture Maps, then share those sites with same-name branches.
    Optional: if Overture can't be reached (or OVERTURE=0), the city keeps what it has."""
    if os.environ.get("OVERTURE") == "0":
        return
    try:
        import overture
        n = overture.fill_websites(city, rows)
        n += link_websites(rows, {})
        print(f"{city.name}: Overture Maps filled {n} more websites")
    except Exception as e:  # noqa: BLE001
        print(f"::warning title=Overture skipped for {city.name}::{type(e).__name__}: {str(e)[:200]}")


KEEP_MISSING_DAYS = 21  # OpenStreetMap can answer with an incomplete list: keep a vanished place this long


def keep_missing(rows: list[dict], city: cities.City, today: str | None = None) -> list[dict]:
    """Places on the previous list that OpenStreetMap didn't return this time are kept, marked
    missing_since, for KEEP_MISSING_DAYS; then they're dropped (closed or deleted for real).
    Places that come back lose the mark."""
    today = today or time.strftime("%Y-%m-%d")
    try:
        previous = json.loads(city.restaurants.read_text())
    except (OSError, ValueError):
        return rows
    seen = {r["id"] for r in rows}
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.mktime(time.strptime(today, "%Y-%m-%d")) - KEEP_MISSING_DAYS * 86400))
    kept = []
    for r in previous:
        if r["id"] in seen or not str(r["id"]).startswith("osm-"):  # hand-added places are re-read from their file
            continue
        since = r.get("missing_since") or today
        if since > cutoff:
            kept.append({**r, "missing_since": since})
    if kept:
        print(f"{city.name}: {len(kept)} places missing from OpenStreetMap this time; kept until they've been gone {KEEP_MISSING_DAYS} days")
    return rows + kept


def relink(city: cities.City) -> None:
    """Re-apply link_websites() to a city's saved list without asking OpenStreetMap again."""
    rows = json.loads(city.restaurants.read_text())
    before = sum(1 for r in rows if r.get("website"))
    n = link_websites(rows, load_brand_sites())
    city.restaurants.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    print(f"{city.name}: {n} more places now have a website ({before} -> {before + n} of {len(rows)})")


def discover(city: cities.City) -> None:
    els = fetch(city.bbox)
    rows = []
    for e in els:
        t = e.get("tags", {})
        lat = e.get("lat") or e.get("center", {}).get("lat")
        lng = e.get("lon") or e.get("center", {}).get("lon")
        if lat is None or lng is None:
            continue
        brand = t.get("brand") or None
        site = norm_site(t.get("website") or t.get("contact:website") or t.get("brand:website"))
        rows.append({
            "id": f"osm-{e['type'][0]}{e['id']}",
            "name": t["name"],
            "brand": brand,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "address": address(t),
            "website": site,
            "cuisine": t.get("cuisine"),
            "kind": t.get("amenity"),
        })

    link_websites(rows, load_brand_sites())
    add_overture(city, rows)

    rows = keep_missing(rows, city)

    if city.extra_restaurants.exists():
        rows.extend(json.loads(city.extra_restaurants.read_text()))

    if not rows:
        raise RuntimeError("OpenStreetMap returned no places; keeping the existing list")
    rows.sort(key=lambda r: r["name"].lower())
    city.restaurants.parent.mkdir(parents=True, exist_ok=True)
    city.restaurants.write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    with_site = sum(1 for r in rows if r["website"])
    sites = len({urlparse(r["website"]).netloc for r in rows if r["website"]})
    print(f"{city.name}: {len(rows)} places found, {with_site} with a website ({sites} unique sites) -> {city.restaurants}")


def main():
    args = sys.argv[1:]
    if "--relink" in args:  # fill websites from brand_sites.json and same-name branches, no OpenStreetMap query
        for city in cities.pick([a for a in args if a != "--relink"]):
            relink(city)
        return
    failed = []
    for city in cities.pick(args):
        try:
            discover(city)
        except Exception as e:  # noqa: BLE001  one city failing must not stop the others
            print(f"::error title=Couldn't find restaurants in {city.name}::{e}")
            failed.append(city.name)
    if failed:
        sys.exit(f"Restaurant discovery failed for: {', '.join(failed)}. Other cities were updated; this one keeps its previous list.")


if __name__ == "__main__":
    main()
