"""Branches without a website borrow one from the same chain (pipeline/discover.py link_websites)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from discover import link_websites, name_key, load_brand_sites  # noqa: E402


def place(name, website=None, brand=None):
    return {"id": name + str(id(object())), "name": name, "brand": brand, "website": website}


class LinkWebsites(unittest.TestCase):
    def test_same_name_branch_in_the_city_shares_its_site(self):
        rows = [place("Kinton Ramen", "https://kintonramen.com"), place("Kinton Ramen")]
        self.assertEqual(link_websites(rows, {}), 1)
        self.assertEqual(rows[1]["website"], "https://kintonramen.com")

    def test_spelling_and_accents_do_not_matter(self):
        rows = [place("Thaï Express", "https://thaiexpress.ca"), place("Thai Express!")]
        link_websites(rows, {})
        self.assertEqual(rows[1]["website"], "https://thaiexpress.ca")

    def test_generic_names_are_never_linked(self):
        rows = [place("Cafe", "https://somecafe.ca"), place("Café")]
        self.assertEqual(link_websites(rows, {}), 0)
        self.assertIsNone(rows[1]["website"])

    def test_chain_list_fills_gaps_but_never_replaces_a_listed_site(self):
        rows = [place("Pizzaville"), place("Pizzaville", "https://www.pizzaville.ca/locations/42")]
        link_websites(rows, {name_key("Pizzaville"): "https://www.pizzaville.ca"})
        self.assertEqual(rows[1]["website"], "https://www.pizzaville.ca/locations/42")
        self.assertTrue(rows[0]["website"].startswith("https://www.pizzaville.ca"))

    def test_brand_tag_links_differently_named_branches(self):
        rows = [place("DQ Grill & Chill", "https://www.dairyqueen.com/en-ca", brand="Dairy Queen"),
                place("Dairy Queen Whyte Ave", brand="Dairy Queen")]
        link_websites(rows, {})
        self.assertEqual(rows[1]["website"], "https://www.dairyqueen.com/en-ca")

    def test_chain_list_file_is_valid_and_has_no_generic_names(self):
        sites = load_brand_sites()
        self.assertGreater(len(sites), 20)
        from discover import GENERIC_NAMES
        self.assertFalse(set(sites) & GENERIC_NAMES)
        for url in sites.values():
            self.assertRegex(url, r"^https?://[^/\s]+\.[a-z]{2,}")


class NotARestaurantSite(unittest.TestCase):
    def test_directories_social_and_delivery_links_are_dropped(self):
        from discover import norm_site
        for u in ["http://www.pagesjaunes.ca/goframe/1", "http://twitter.com", "https://x.com/a", "https://m.facebook.com/a",
                  "https://order.ubereats.com/a", "https://www.yelp.ca/biz/a", "https://maps.google.com/a",
                  "https://www.tripadvisor.co.uk/a", "https://linktr.ee/a", "https://www.opentable.ca/r/a"]:
            self.assertIsNone(norm_site(u), u)

    def test_real_sites_that_look_similar_are_kept(self):
        from discover import norm_site
        for u in ["https://x.company.ca", "https://yelpizza.ca", "https://boxcar.ca", "https://www.galsushi.com"]:
            self.assertIsNotNone(norm_site(u), u)


if __name__ == "__main__":
    unittest.main()


class KeepMissing(unittest.TestCase):
    def test_a_vanished_place_is_kept_three_weeks_then_dropped(self):
        import json, tempfile
        import cities as C
        from discover import keep_missing
        tmp = Path(tempfile.mkdtemp())
        city = C.City(**{**C.get("edmonton").__dict__, "data_dir": str(tmp)})
        prev = [{"id": "osm-n1", "name": "A&W"}, {"id": "osm-n2", "name": "Gone Cafe", "missing_since": "2026-10-01"},
                {"id": "manual-1", "name": "Hand added"}]
        (tmp / "restaurants.json").write_text(json.dumps(prev))
        out = keep_missing([{"id": "osm-n3", "name": "New Place"}], city, today="2026-11-11")
        self.assertEqual([(r["id"], r.get("missing_since")) for r in out], [("osm-n3", None), ("osm-n1", "2026-11-11")])
        back = keep_missing([{"id": "osm-n1", "name": "A&W"}], city, today="2026-11-18")
        self.assertEqual([r.get("missing_since") for r in back if r["id"] == "osm-n1"], [None])
