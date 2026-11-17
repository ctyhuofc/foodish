"""Branches without a website borrow one from the same chain (pipeline/discover.py link_websites)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from discover import link_websites, name_key, load_brand_sites  # noqa: E402


def place(name, website=None, brand=None):
    return {"id": name + str(id(object())), "name": name, "brand": brand, "website": website}


class PlaceName(unittest.TestCase):
    def test_two_spellings_joined_by_a_semicolon_keep_the_first(self):
        from discover import place_name
        self.assertEqual(place_name("The Canadian Brew House;The Canadian Brewhouse"), "The Canadian Brew House")
        self.assertEqual(place_name("Joey Bentall One"), "Joey Bentall One")


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


class BranchesMustAgree(unittest.TestCase):
    """One odd branch must not hand its website to the whole chain (Toronto Starbucks -> macs.ca)."""

    def test_odd_first_branch_does_not_win(self):
        rows = [place("Starbucks", "http://www.macs.ca"),
                place("Starbucks", "https://www.starbucks.ca/store-locator/store/1/a"),
                place("Starbucks", "https://www.starbucks.ca/store-locator/store/2/b"),
                place("Starbucks")]
        link_websites(rows, {})
        self.assertEqual(rows[3]["website"], "https://www.starbucks.ca")

    def test_odd_branch_gets_the_chain_site(self):
        rows = [place("Starbucks", "http://www.macs.ca")] + [
            place("Starbucks", f"https://www.starbucks.ca/store-locator/store/{i}/a") for i in range(3)]
        self.assertEqual(link_websites(rows, {}), 1)
        self.assertEqual(rows[0]["website"], "https://www.starbucks.ca")

    def test_franchise_own_site_with_the_name_is_kept(self):
        rows = [place("Boston Pizza", "https://bostonpizza-whyte.ca")] + [
            place("Boston Pizza", "https://bostonpizza.com") for _ in range(3)]
        link_websites(rows, {})
        self.assertEqual(rows[0]["website"], "https://bostonpizza-whyte.ca")

    def test_chain_own_other_domain_is_kept(self):
        rows = [place("Blenz Coffee", "http://www.blenz.ca")] + [place("Blenz Coffee", "https://blenz.com") for _ in range(3)]
        link_websites(rows, {})
        self.assertEqual(rows[0]["website"], "http://www.blenz.ca")

    def test_site_named_after_its_own_brand_is_kept(self):
        rows = [place("Rolltation", "https://rolltation.com", brand="South St. Burger")] + [
            place("South St. Burger", "https://southstburger.com") for _ in range(3)]
        link_websites(rows, {})
        self.assertEqual(rows[0]["website"], "https://rolltation.com")

    def test_odd_site_is_kept_when_few_branches_agree(self):
        rows = [place("Joe's Grill", "https://somewhere-else.ca"), place("Joe's Grill", "https://joesgrill.ca"),
                place("Joe's Grill", "https://joesgrill.ca")]
        link_websites(rows, {})
        self.assertEqual(rows[0]["website"], "https://somewhere-else.ca")

    def test_branches_that_disagree_get_no_website(self):
        rows = [place("Joe's Grill", "https://joesgrill.ca"), place("Joe's Grill", "https://joes-grill-calgary.com"),
                place("Joe's Grill")]
        self.assertEqual(link_websites(rows, {}), 0)
        self.assertIsNone(rows[2]["website"])

    def test_disagreement_falls_back_to_the_chain_list(self):
        rows = [place("Wendy's", "https://a.example.com"), place("Wendy's", "https://b.example.com"), place("Wendy's")]
        link_websites(rows, {name_key("Wendy's"): "https://www.wendys.com/en-ca"})
        self.assertEqual(rows[2]["website"], "https://www.wendys.com/en-ca")

    def test_the_page_most_branches_list_is_shared(self):
        rows = [place("Kinton Ramen", "https://kintonramen.com/menu"), place("Kinton Ramen", "https://kintonramen.com"),
                place("Kinton Ramen", "https://kintonramen.com"), place("Kinton Ramen")]
        link_websites(rows, {})
        self.assertEqual(rows[3]["website"], "https://kintonramen.com")

    def test_shared_guide_and_menu_sites_are_not_restaurant_sites(self):
        from discover import norm_site
        for u in ["https://www.timeout.com/toronto/a", "https://www.blogto.com/restaurants/a", "https://zomi.menu/a"]:
            self.assertIsNone(norm_site(u), u)

    def test_wendys_points_at_the_canadian_site(self):
        self.assertIn("/en-ca", load_brand_sites()[name_key("Wendy's")])


class OwnersListWins(unittest.TestCase):
    """data/brand_sites.json is the owner's: made-up OpenStreetMap entries can't repoint a chain listed there."""

    def test_branches_agreeing_on_another_site_do_not_beat_the_owners_list(self):
        rows = [place("Dairy Queen", "https://evil.example") for _ in range(3)] + [place("Dairy Queen")]
        link_websites(rows, {name_key("Dairy Queen"): "https://www.dairyqueen.com/en-ca"})
        self.assertEqual(rows[3]["website"], "https://www.dairyqueen.com/en-ca")

    def test_a_real_branch_is_not_swapped_for_the_fake_majority(self):
        rows = [place("Dairy Queen", "https://evil.example") for _ in range(3)] + [
            place("Dairy Queen", "https://www.dairyqueen.com/en-ca/locations/ab/edmonton/1")]
        self.assertEqual(link_websites(rows, {name_key("Dairy Queen"): "https://www.dairyqueen.com/en-ca"}), 0)
        self.assertEqual(rows[3]["website"], "https://www.dairyqueen.com/en-ca/locations/ab/edmonton/1")

    def test_an_odd_branch_is_still_corrected_when_the_majority_matches_the_owners_list(self):
        rows = [place("Starbucks", "http://www.macs.ca")] + [
            place("Starbucks", f"https://www.starbucks.ca/store-locator/store/{i}/a") for i in range(3)]
        self.assertEqual(link_websites(rows, {name_key("Starbucks"): "https://www.starbucks.ca"}), 1)
        self.assertEqual(rows[0]["website"], "https://www.starbucks.ca")


class OnlyPublicWebsites(unittest.TestCase):
    def test_addresses_a_restaurant_site_never_has_are_dropped(self):
        from discover import norm_site
        for u in ["127.0.0.1:8080", "http://169.254.169.254/latest", "www.timhortons.com@evil.ca", "https://user:pw@pizza.ca",
                  "http://[::1]/", "https://[2606:4700::1]/", "http://10.0.0.1", "https://0x7f.1", "https://intranet",
                  "https://printer.local", "https://db.internal/x", "https://app.localhost", "https://pizza.ca:8080/",
                  "https://pizza.ca:80", "https://pizza.ca:99999", "https://none", "https://+1-647-358-5000"]:
            self.assertIsNone(norm_site(u), u)

    def test_ordinary_websites_are_kept_and_tidied(self):
        from discover import norm_site
        self.assertEqual(norm_site("https://pizza.ca:443/menu/"), "https://pizza.ca/menu")
        self.assertEqual(norm_site("http://www.pizza.ca:80"), "http://www.pizza.ca")
        self.assertEqual(norm_site("HTTPS://Pizza.CA./menu"), "https://pizza.ca/menu")
        self.assertEqual(norm_site("www.local1794.ca"), "https://www.local1794.ca")
