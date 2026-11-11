"""The cities Foodish covers, and where each city's files live.

cities.json (repo root) is the list. Every pipeline script takes city ids on the command line
(none = every city) and gets `City` objects from here, so no script hard-codes a city or a path.

    python pipeline/scrape.py                 # every city
    python pipeline/scrape.py vancouver       # one city
    python pipeline/discover.py edmonton vancouver

Edmonton keeps its original paths (data/deals.json, site/deals/…), so nothing that reads them changes.
Other cities live under data/<id>/ and site/<id>/.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "cities.json"


@dataclass(frozen=True)
class City:
    id: str
    name: str
    region: str
    center: tuple[float, float]
    center_label: str
    bbox: tuple[float, float, float, float]  # south, west, north, east
    timezone: str
    utc_offset_hours: int   # the standard (winter) offset; fixed all year unless observes_dst
    data_dir: str
    pages_dir: str      # under site/, e.g. "deals" or "vancouver/deals"
    deals_url: str
    observes_dst: bool = False  # True where clocks still change (Toronto); False where they're fixed (AB, BC)

    # ---- files ----
    @property
    def data(self) -> Path:
        return ROOT / self.data_dir

    @property
    def restaurants(self) -> Path:
        return self.data / "restaurants.json"

    @property
    def extra_restaurants(self) -> Path:      # your manual additions (optional)
        return self.data / "extra_restaurants.json"

    @property
    def deals(self) -> Path:                  # the published file apps read
        return self.data / "deals.json"

    @property
    def manual_deals(self) -> Path:           # hand-added deals (optional)
        return self.data / "manual_deals.json"

    @property
    def cache(self) -> Path:                  # per-site fingerprints, saved deals, monthly AI usage
        return self.data / "cache" / "sites.json"

    @property
    def report(self) -> Path:                 # what happened to each site on the latest run
        return self.data / "cache" / "report.json"

    @property
    def site_copy(self) -> Path:              # copy served next to the app, for local testing
        rel = Path(self.data_dir).relative_to("data")
        return ROOT / "site" / "data" / rel / "deals.json"

    @property
    def pages(self) -> Path:                  # search-friendly pages
        return ROOT / "site" / self.pages_dir

    @property
    def app_query(self) -> str:               # how links open the app in this city
        return "" if self.id == default_id() else f"city={self.id}"


def _config() -> dict:
    return json.loads(CONFIG.read_text())


def default_id() -> str:
    return _config()["default"]


def all_cities() -> list[City]:
    fields = City.__dataclass_fields__
    out = []
    for c in _config()["cities"]:
        kw = {k: c[k] for k in fields if k in c}  # optional fields keep their defaults
        kw["center"], kw["bbox"] = tuple(c["center"]), tuple(c["bbox"])
        out.append(City(**kw))
    return out


def get(city_id: str) -> City:
    for c in all_cities():
        if c.id == city_id:
            return c
    raise SystemExit(f"Unknown city {city_id!r}. Known cities: {', '.join(c.id for c in all_cities())} (see cities.json)")


def pick(ids: list[str] | None) -> list[City]:
    """Cities named on the command line, or every city when none are named."""
    return [get(i.strip().lower()) for i in ids if i.strip()] if ids else all_cities()
