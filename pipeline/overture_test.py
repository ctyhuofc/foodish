"""Test (changes no data): how many missing websites would Overture Maps fill?

Downloads one city's places from Overture Maps (free; CDLA-Permissive / Apache 2.0), matches them to
that city's restaurants (data/<city>/restaurants.json) by name and distance, and reports:
  * restaurants with no website that Overture has a usable website for
  * food places Overture knows that OpenStreetMap doesn't (possible extra restaurants)
Writes data-free results to overture_report.json and the run's Summary page.

    pip install duckdb
    python pipeline/overture_test.py toronto
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cities  # noqa: E402
from discover import name_key, norm_site  # noqa: E402

STAC = "https://stac.overturemaps.org/catalog.json"
FALLBACK_RELEASE = "2026-10-21.0"
MATCH_METRES = 80        # same place if within this distance...
NAME_SIMILARITY = 0.82   # ...and the names are this alike (or one contains the other)
MIN_CONFIDENCE = 0.5     # Overture's 0-1 "this place exists" score, for extra-restaurant counts
FOOD = re.compile(r"restaurant|cafe|coffee|bar\b|pub|bakery|food|pizza|sushi|tea|bistro|diner|eat_and_drink|"
                  r"burger|chicken|dessert|ice_cream|juice|brewery|deli|noodle|ramen|taco|grill", re.I)


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
    x, y = name_key(a), name_key(b)
    if not x or not y:
        return False
    if len(min(x, y, key=len)) >= 5 and (x in y or y in x):
        return True
    return SequenceMatcher(None, x, y).ratio() >= NAME_SIMILARITY


def usable_site(urls: list[str]) -> str | None:
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


def report(city: cities.City, osm: list[dict], ov: list[dict]) -> dict:
    missing = [r for r in osm if not r.get("website")]
    pairs, used = match(missing, ov)
    fills = [(r, p, usable_site(p["websites"])) for r, p in pairs]
    good = [(r, p, s) for r, p, s in fills if s]
    social_only = sum(1 for r, p, s in fills if p["websites"] and not s)
    _, used_all = match(osm, ov)
    extra = [p for p in ov if p["id"] not in used_all and FOOD.search(p["category"] or "")
             and (p["confidence"] or 0) >= MIN_CONFIDENCE]
    extra_with_site = [p for p in extra if usable_site(p["websites"])]
    return {
        "city": city.name, "osm_places": len(osm), "osm_missing_website": len(missing),
        "matched_in_overture": len(pairs), "websites_filled": len(good),
        "only_social_or_delivery_links": social_only,
        "extra_food_places_in_overture": len(extra), "extra_with_website": len(extra_with_site),
        "sample_fills": [{"name": r["name"], "address": r.get("address", ""), "website": s} for r, p, s in good[:40]],
        "sample_extra": [{"name": p["name"], "category": p["category"], "website": usable_site(p["websites"])}
                         for p in extra_with_site[:25]],
    }


def summary_md(rep: dict, release: str) -> str:
    pct = lambda a, b: f"{100 * a / b:.0f}%" if b else "–"  # noqa: E731
    lines = [f"## Overture test · {rep['city']} · release {release}", "",
             "| | |", "|---|---|",
             f"| OpenStreetMap places | {rep['osm_places']} |",
             f"| …with no website | {rep['osm_missing_website']} |",
             f"| …found in Overture | {rep['matched_in_overture']} |",
             f"| **…Overture gives a usable website** | **{rep['websites_filled']} ({pct(rep['websites_filled'], rep['osm_missing_website'])})** |",
             f"| …Overture has only Facebook/Instagram/delivery links | {rep['only_social_or_delivery_links']} |",
             f"| Food places only in Overture (confidence ≥ {MIN_CONFIDENCE}) | {rep['extra_food_places_in_overture']} |",
             f"| …of those with a website | {rep['extra_with_website']} |", "",
             "### Sample filled websites", ""]
    lines += [f"- {f['name']} ({f['address'] or 'no address'}) → {f['website']}" for f in rep["sample_fills"][:20]]
    return "\n".join(lines) + "\n"


def main() -> None:
    city = cities.get((sys.argv[1:] or ["toronto"])[0])
    osm = json.loads(city.restaurants.read_text())
    release = latest_release()
    rep = report(city, osm, fetch(city, release))
    rep["release"] = release
    Path("overture_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    md = summary_md(rep, release)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(md)


if __name__ == "__main__":
    main()
