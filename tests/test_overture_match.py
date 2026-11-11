"""Matching OpenStreetMap restaurants to Overture places (pipeline/overture_test.py), no network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import cities  # noqa: E402
import overture_test as ot  # noqa: E402

OSM = [
    {"id": "a", "name": "Gal's Sushi", "lat": 43.6600, "lng": -79.3900},
    {"id": "b", "name": "Kinton Ramen", "lat": 43.6500, "lng": -79.4000},
    {"id": "c", "name": "Joe's Diner", "lat": 43.7000, "lng": -79.4000, "website": "https://joes.ca"},
]
OV = [
    {"id": "1", "name": "Gals Sushi", "lat": 43.6602, "lng": -79.3901, "websites": ["https://www.galsushi.com/"], "category": "sushi_restaurant", "confidence": 0.9},
    {"id": "2", "name": "Kinton Ramen", "lat": 43.6600, "lng": -79.4000, "websites": ["https://kintonramen.com"], "category": "ramen_restaurant", "confidence": 0.9},
    {"id": "3", "name": "Joe's Diner", "lat": 43.70005, "lng": -79.40005, "websites": [], "category": "diner", "confidence": 0.9},
    {"id": "4", "name": "New Taco Spot", "lat": 43.6800, "lng": -79.3800, "websites": ["https://facebook.com/x", "https://newtaco.ca"], "category": "taco_restaurant", "confidence": 0.8},
    {"id": "5", "name": "Dentist", "lat": 43.6800, "lng": -79.3801, "websites": ["https://teeth.ca"], "category": "dentist", "confidence": 0.9},
]


class Match(unittest.TestCase):
    def test_close_and_similar_name_matches_far_does_not(self):
        pairs, _ = ot.match(OSM[:2], OV)
        self.assertEqual([(r["id"], p["id"]) for r, p in pairs], [("a", "1")])  # Kinton is 1.1 km away

    def test_social_links_are_skipped_for_a_real_site(self):
        self.assertEqual(ot.usable_site(OV[3]["websites"]), "https://newtaco.ca")
        self.assertIsNone(ot.usable_site(["https://instagram.com/x"]))

    def test_report_counts(self):
        rep = ot.report(cities.get("toronto"), OSM, OV)
        self.assertEqual((rep["osm_missing_website"], rep["websites_filled"]), (2, 1))
        self.assertEqual(rep["extra_food_places_in_overture"], 2)  # Kinton (unmatched, far) and the taco spot; not the dentist
        self.assertEqual(rep["sample_fills"][0]["website"], "https://www.galsushi.com")


if __name__ == "__main__":
    unittest.main()
