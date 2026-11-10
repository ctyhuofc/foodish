"""The cities list (cities.json) and everything that must agree with it."""
import datetime as dt
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import cities  # noqa: E402
import deal_state as ds  # noqa: E402
import pages  # noqa: E402

CONFIG = json.loads((ROOT / "cities.json").read_text())
UTC = dt.timezone.utc


def site_config() -> dict:
    """site/config.js as data (it's a plain script that sets window.FOODISH_CONFIG)."""
    js = "const window = {}; " + (ROOT / "site" / "config.js").read_text() + "; console.log(JSON.stringify(window.FOODISH_CONFIG));"
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)


class CitiesFile(unittest.TestCase):
    def test_ids_are_unique_and_default_exists(self):
        ids = [c["id"] for c in CONFIG["cities"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn(CONFIG["default"], ids)

    def test_each_centre_is_inside_its_search_area(self):
        for c in cities.all_cities():
            south, west, north, east = c.bbox
            self.assertTrue(south < c.center[0] < north and west < c.center[1] < east, c.id)

    def test_edmonton_keeps_its_original_files(self):
        """The live site reads these paths: they must never move."""
        e = cities.get("edmonton")
        rel = lambda p: str(p.relative_to(ROOT))  # noqa: E731
        self.assertEqual([rel(e.restaurants), rel(e.deals), rel(e.cache), rel(e.report), rel(e.manual_deals), rel(e.site_copy), rel(e.pages)],
                         ["data/restaurants.json", "data/deals.json", "data/cache/sites.json", "data/cache/report.json",
                          "data/manual_deals.json", "site/data/deals.json", "site/deals"])
        self.assertTrue(e.deals_url.endswith("/main/data/deals.json"))

    def test_cities_do_not_share_files(self):
        seen = set()
        for c in cities.all_cities():
            for p in (c.restaurants, c.deals, c.cache, c.report, c.pages):
                self.assertNotIn(p, seen)
                seen.add(p)
            self.assertTrue(c.deals_url.endswith(f"/main/{c.data_dir}/deals.json"), c.id)

    def test_unknown_city_is_an_error(self):
        with self.assertRaises(SystemExit):
            cities.pick(["calgary"])

    def test_no_names_means_every_city(self):
        self.assertEqual([c.id for c in cities.pick([])], [c["id"] for c in CONFIG["cities"]])


class AppAgrees(unittest.TestCase):
    def test_app_config_matches(self):
        cfg = site_config()
        self.assertEqual(cfg["DEFAULT_CITY"], CONFIG["default"])
        want = [(c["id"], c["name"], c["center_label"], c["center"], c["timezone"], c["deals_url"]) for c in CONFIG["cities"]]
        got = [(c["id"], c["name"], c["label"], c["center"], c["timezone"], c["dataUrl"]) for c in cfg["CITIES"]]
        self.assertEqual(got, want, "site/config.js CITIES must match cities.json")

    def test_alerts_worker_knows_every_city(self):
        src = (ROOT / "worker" / "worker.js").read_text()
        for c in cities.all_cities():
            m = re.search(r'\{ id: "%s", lat: ([-\d.]+), lng: ([-\d.]+), south: ([-\d.]+), north: ([-\d.]+), west: ([-\d.]+), east: ([-\d.]+) \}' % c.id, src)
            self.assertTrue(m, f"add {c.id} to CITY_AREAS in worker/worker.js")
            lat, lng, south, north, west, east = map(float, m.groups())
            self.assertTrue(south <= c.bbox[0] and north >= c.bbox[2] and west <= c.bbox[1] and east >= c.bbox[3], c.id)

    def test_links_into_the_app(self):
        self.assertEqual(pages.app_link(cities.get("edmonton"), "cat=wings"), "/?cat=wings")
        self.assertEqual(pages.app_link(cities.get("edmonton")), "/")
        self.assertEqual(pages.app_link(cities.get("vancouver"), "day=3"), "/?city=vancouver&day=3")
        self.assertEqual(pages.app_link(cities.get("vancouver")), "/?city=vancouver")


class Clocks(unittest.TestCase):
    def test_every_city_has_a_fixed_clock_rule_matching_cities_json(self):
        for c in cities.all_cities():
            self.assertIn(c.timezone, ds.FIXED_OFFSETS, f"add {c.timezone} to FIXED_OFFSETS in deal_state.py and FIXED in deal-state.js")
            winter = ds.local_time(c.timezone, dt.datetime(2027, 1, 15, 12, 0, tzinfo=UTC))
            summer = ds.local_time(c.timezone, dt.datetime(2027, 7, 15, 12, 0, tzinfo=UTC))
            for t in (winter, summer):
                self.assertEqual(t.utcoffset(), dt.timedelta(hours=c.utc_offset_hours), c.id)

    def test_vancouver_is_one_hour_behind_edmonton(self):
        at = dt.datetime(2026, 11, 11, 21, 30, tzinfo=UTC)
        self.assertEqual(ds.now_in("America/Edmonton", at).mins, 15 * 60 + 30)
        self.assertEqual(ds.now_in("America/Vancouver", at).mins, 14 * 60 + 30)

    def test_same_moment_same_deal_different_city(self):
        happy_hour = {"start": "15:00", "end": "18:00"}
        at = dt.datetime(2026, 11, 11, 21, 30, tzinfo=UTC)  # 3:30 pm Edmonton, 2:30 pm Vancouver
        self.assertEqual(ds.deal_state(happy_hour, ds.now_in("America/Edmonton", at)), ds.ON_NOW)
        self.assertEqual(ds.deal_state(happy_hour, ds.now_in("America/Vancouver", at)), ds.LATER)

    def test_vancouver_before_the_change(self):
        self.assertEqual(ds.now_in("America/Vancouver", dt.datetime(2026, 1, 15, 20, 0, tzinfo=UTC)).mins, 12 * 60)  # old winter: UTC-8
        self.assertEqual(ds.now_in("America/Vancouver", dt.datetime(2026, 7, 1, 19, 0, tzinfo=UTC)).mins, 12 * 60)   # summer: UTC-7

    def test_vancouver_late_evening_is_still_the_same_day(self):
        m = ds.now_in("America/Vancouver", dt.datetime(2026, 11, 12, 6, 30, tzinfo=UTC))
        self.assertEqual((m.date, m.day, m.mins), ("2026-11-11", 3, 23 * 60 + 30))


if __name__ == "__main__":
    unittest.main()
