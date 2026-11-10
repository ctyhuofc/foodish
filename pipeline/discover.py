"""Step 1 (weekly): find every restaurant / bar / café in each city from OpenStreetMap.

Free, no API key. Writes each city's restaurants.json (Edmonton: data/restaurants.json,
others: data/<city>/restaurants.json). The area searched is the city's "bbox" in cities.json.

Usage:  python pipeline/discover.py              (every city)
        python pipeline/discover.py vancouver    (one city)
"""
import json
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


def norm_site(url):
    if not url:
        return None
    url = url.strip().split(";")[0]
    if not re.match(r"^https?://", url):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc or any(s in p.netloc for s in ("facebook.com", "instagram.com", "linktr.ee", "tiktok.com", "doordash", "skipthedishes", "ubereats")):
        return None  # social pages can't be scraped reliably / allowed
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


def discover(city: cities.City) -> None:
    els = fetch(city.bbox)
    brand_sites = json.loads(BRAND_SITES.read_text()) if BRAND_SITES.exists() else {}
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

    # Chains: branches without a website inherit the brand's site (from overrides or another branch).
    by_brand = {}
    for r in rows:
        if r["brand"] and r["website"]:
            by_brand.setdefault(r["brand"], r["website"])
    for r in rows:
        key = r["brand"] or r["name"]
        if key in brand_sites:
            r["website"] = norm_site(brand_sites[key]) or r["website"]
        elif not r["website"] and r["brand"] in by_brand:
            r["website"] = by_brand[r["brand"]]

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
    failed = []
    for city in cities.pick(sys.argv[1:]):
        try:
            discover(city)
        except Exception as e:  # noqa: BLE001  one city failing must not stop the others
            print(f"::error title=Couldn't find restaurants in {city.name}::{e}")
            failed.append(city.name)
    if failed:
        sys.exit(f"Restaurant discovery failed for: {', '.join(failed)}. Other cities were updated; this one keeps its previous list.")


if __name__ == "__main__":
    main()
