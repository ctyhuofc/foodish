"""Runs the scraper's whole per-city path, including publishing deals.json, with no network or AI.

A city whose only restaurant has no website and one hand-added deal goes through every step of
run_city() and must produce a valid deals file. (A NameError in the publish step once crashed every city.)
"""
import argparse
import json
import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import cities  # noqa: E402
import scrape  # noqa: E402


class PublishStep(unittest.TestCase):
    def setUp(self):
        self.dir = ROOT / "data" / "_test_city"
        self.site = ROOT / "site" / "data" / "_test_city"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        (self.dir / "restaurants.json").write_text(json.dumps(
            [{"id": "osm-1", "name": "Test Diner", "lat": 43.65, "lng": -79.38, "address": "1 Test St", "website": ""}]))
        (self.dir / "manual_deals.json").write_text(json.dumps([{
            "id": "t1", "brand": "Test Diner", "restaurant": "Test Diner", "title": "Half-price wings",
            "price": "50% off", "days": [3], "start": None, "end": None, "categories": ["wings"],
            "locations": [{"lat": 43.65, "lng": -79.38, "address": "1 Test St"}]}]))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.site, ignore_errors=True)

    def run_for(self, tz, dst):
        base = cities.get("toronto")
        city = cities.City(**{**base.__dict__, "id": "_test_city", "timezone": tz, "observes_dst": dst,
                              "data_dir": "data/_test_city", "pages_dir": "_test_city/deals"})
        scrape.run_city(city, argparse.Namespace(limit=0, workers=1, cities=[]))
        return json.loads(city.deals.read_text())

    def test_publishes_a_valid_file_for_a_city_that_changes_clocks(self):
        out = self.run_for("America/Toronto", True)
        self.assertEqual(out["observes_dst"], True)
        self.assertIn(out["utc_offset_hours"], (-5, -4))
        self.assertEqual([d["title"] for d in out["deals"]], ["Half-price wings"])
        self.assertTrue((self.site / "deals.json").exists())

    def test_publishes_a_valid_file_for_a_fixed_clock_city(self):
        out = self.run_for("America/Edmonton", False)
        self.assertEqual((out["observes_dst"], out["utc_offset_hours"]), (False, -6))


if __name__ == "__main__":
    unittest.main()
