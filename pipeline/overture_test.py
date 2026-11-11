"""Test (changes no data): how many missing websites would Overture Maps fill?

Downloads one city's places from Overture Maps, matches them to that city's restaurants by name and
distance, and reports how many websites it would fill and which food places only Overture has.
The real filling happens in discover.py (overture.fill_websites); this is the read-only check.

    pip install duckdb
    python pipeline/overture_test.py toronto
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cities  # noqa: E402
from overture import FOOD, MIN_CONFIDENCE, fetch, latest_release, match, usable_site  # noqa: E402,F401
from overture import metres, same_name  # noqa: E402,F401  (used by tests)


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
