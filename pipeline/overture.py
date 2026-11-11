"""Overture Maps places (free; CDLA-Permissive-2.0 / Apache-2.0): fills websites OpenStreetMap is missing.

discover.py calls fill_websites() for each city. If Overture can't be reached, discovery carries on
without it. Needs the duckdb package. Attribution is carried in every deals.json ("attribution").
"""
from __future__ import annotations

import json
import math
import re
import sys
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cities  # noqa: E402

STAC = "https://stac.overturemaps.org/catalog.json"
FALLBACK_RELEASE = "2026-10-21.0"
MATCH_METRES = 80        # same place if within this distance...
NAME_SIMILARITY = 0.82   # ...and the names are this alike (or one contains the other)
MIN_CONFIDENCE = 0.5     # Overture's 0-1 "this place exists" score, for extra-restaurant counts
# Overture food categories (snake_case, e.g. "sushi_restaurant", "gastropub"), matched on whole words
# so "pub" doesn't match "public_school" and "bar" doesn't match "barber_shop".
FOOD = re.compile(r"(^|_)(restaurant|cafe|coffee|coffee_shop|bar|pub|gastropub|bakery|pizza|sushi|tea|bistro|"
                  r"diner|eat_and_drink|burger|chicken|dessert|ice_cream|juice|brewery|deli|noodle|ramen|taco|"
                  r"grill|steakhouse|buffet|food_court|fast_food|bubble_tea|donut|bagel|wings)($|_)", re.I)


def latest_release() -> str:
    try:
        with urllib.request.urlopen(STAC, timeout=30) as r:
            cat = json.load(r)
        names = re.findall(r"(\d{4}-\d{2}-\d{2}\.\d+)", json.dumps(cat.get("links", [])))
        if names:
            return max(names)
    except Exception as e:  # noqa: BLE001
        print(f"STAC catalog unavailable ({e}); using {FALLBACK_RELEASE}")
    return FALLBACK_RELEASE


def fetch(city: cities.City, release: str) -> list[dict]:
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    s, w, n, e = city.bbox
    src = f"s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
    where = f"bbox.xmin > {w} AND bbox.xmax < {e} AND bbox.ymin > {s} AND bbox.ymax < {n}"
    for cat in ("taxonomy.primary", "basic_category", "categories.primary"):  # schema changed Sept 2026
        try:
            rows = con.execute(f"""
                SELECT id, names.primary, websites, {cat}, confidence,
                       (bbox.ymin + bbox.ymax) / 2, (bbox.xmin + bbox.xmax) / 2
                FROM read_parquet('{src}', hive_partitioning = 1) WHERE {where}""").fetchall()
            print(f"Overture {release}: {len(rows)} places in {city.name} (category column: {cat})")
            return [{"id": r[0], "name": r[1] or "", "websites": list(r[2] or []), "category": r[3] or "",
                     "confidence": r[4] or 0, "lat": r[5], "lng": r[6]} for r in rows]
        except Exception as ex:  # noqa: BLE001
            print(f"  query with {cat} failed: {str(ex)[:160]}")
    raise RuntimeError("Couldn't read Overture places")


def metres(a, b) -> float:
    dlat = (a["lat"] - b["lat"]) * 111_320
    dlng = (a["lng"] - b["lng"]) * 111_320 * math.cos(math.radians(a["lat"]))
    return math.hypot(dlat, dlng)


def same_name(a: str, b: str) -> bool:
    from discover import name_key
    x, y = name_key(a), name_key(b)
    if not x or not y:
        return False
    if len(min(x, y, key=len)) >= 5 and (x in y or y in x):
        return True
    return SequenceMatcher(None, x, y).ratio() >= NAME_SIMILARITY


def usable_site(urls: list[str]) -> str | None:
    from discover import norm_site
    for u in urls:
        s = norm_site(u)  # drops Facebook, Instagram, delivery apps...
        if s:
            return s
    return None


def match(osm: list[dict], ov: list[dict]) -> tuple[list[tuple[dict, dict]], set[str]]:
    """Pairs (osm place, overture place) that are the same place; and ids of Overture places matched."""
    cell = lambda p: (int(p["lat"] * 1000), int(p["lng"] * 1000))  # noqa: E731  ~110 m x 80 m cells
    grid: dict = {}
    for p in ov:
        grid.setdefault(cell(p), []).append(p)
    pairs, used = [], set()
    for r in osm:
        cy, cx = cell(r)
        near = [p for dy in (-1, 0, 1) for dx in (-1, 0, 1) for p in grid.get((cy + dy, cx + dx), [])]
        best = min((p for p in near if metres(r, p) <= MATCH_METRES and same_name(r["name"], p["name"])),
                   key=lambda p: metres(r, p), default=None)
        if best:
            pairs.append((r, best))
            used.add(best["id"])
    return pairs, used


def fill_websites(city: cities.City, rows: list[dict], places: list[dict] | None = None) -> int:
    """Give rows with no website the website of the same place in Overture (same name, within 80 m).
    Marks them website_source="overture". Returns how many were filled."""
    missing = [r for r in rows if not r.get("website")]
    if not missing:
        return 0
    if places is None:
        places = fetch(city, latest_release())
    filled = 0
    for r, p in match(missing, places)[0]:
        site = usable_site(p["websites"])
        if site:
            r["website"], r["website_source"] = site, "overture"
            filled += 1
    return filled
