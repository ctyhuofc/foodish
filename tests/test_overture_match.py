"""Matching OpenStreetMap restaurants to Overture places (pipeline/overture_test.py), no network."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import cities  # noqa: E402
import overture as ot  # noqa: E402
import overture_test  # noqa: E402,F401  (the report still imports)

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
        rep = overture_test.report(cities.get("toronto"), OSM, OV)
        self.assertEqual((rep["osm_missing_website"], rep["websites_filled"]), (2, 1))
        self.assertEqual(rep["extra_food_places_in_overture"], 2)  # Kinton (unmatched, far) and the taco spot; not the dentist
        self.assertEqual(rep["sample_fills"][0]["website"], "https://www.galsushi.com")


class Fill(unittest.TestCase):
    def test_fills_only_missing_websites_and_marks_the_source(self):
        rows = [dict(r) for r in OSM]
        n = ot.fill_websites(cities.get("toronto"), rows, OV)
        self.assertEqual(n, 1)
        self.assertEqual((rows[0]["website"], rows[0]["website_source"]), ("https://www.galsushi.com", "overture"))
        self.assertEqual(rows[2]["website"], "https://joes.ca")  # a listed site is never replaced
        self.assertNotIn("website_source", rows[2])

    def test_food_categories_match_whole_words(self):
        for c in ["sushi_restaurant", "gastropub", "coffee_shop", "pub"]:
            self.assertTrue(ot.FOOD.search(c), c)
        for c in ["public_school", "barber_shop", "dentist", "teacher"]:
            self.assertFalse(ot.FOOD.search(c), c)


class ConfirmedSite(unittest.TestCase):
    def setUp(self):
        import scrape
        self.scrape, self.places = scrape, scrape.place_words(cities.get("toronto"))

    def test_unrelated_site_from_overture_is_not_used(self):
        b = [{"website_source": "overture", "address": "2401 Bloor Street West"}]
        self.assertFalse(self.scrape.confirmed_site("Dark Horse Comics, Milwaukie, Oregon", b, self.places))

    def test_site_mentioning_city_area_or_street_is_used(self):
        b = [{"website_source": "overture", "name": "Pho Hoang", "address": "2401 Bloor Street West"}]
        for text in ["Pho Hoang: find us in Scarborough", "Pho Hoang, 2401 Bloor St W", "Pho Hoang, best pho in TORONTO"]:
            self.assertTrue(self.scrape.confirmed_site(text, b, self.places), text)

    def test_sites_from_openstreetmap_are_never_second_guessed(self):
        self.assertTrue(self.scrape.confirmed_site("Milwaukie, Oregon", [{"website": "https://x.ca"}], self.places))
        mixed = [{"website_source": "overture"}, {"website": "https://x.ca"}]
        self.assertTrue(self.scrape.confirmed_site("Milwaukie, Oregon", mixed, self.places))


if __name__ == "__main__":
    unittest.main()
