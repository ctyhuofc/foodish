"""Checks that keep wrong deals off Foodish (pipeline/scrape.py)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import cities  # noqa: E402
import scrape  # noqa: E402


def deal(title, description=""):
    return {"title": title, "description": description}


class LimitedElsewhere(unittest.TestCase):
    def setUp(self):
        self.toronto = scrape.place_words(cities.get("toronto"))
        self.vancouver = scrape.place_words(cities.get("vancouver"))

    def test_deal_only_at_branches_in_other_towns_is_left_out(self):
        self.assertTrue(scrape.limited_elsewhere(deal("Diwali bonus voucher", "Dine in at our Brampton or Milton locations"), self.toronto))
        self.assertTrue(scrape.limited_elsewhere(deal("Patio", "Only at our Richmond Hill location"), self.toronto))

    def test_neighbourhoods_streets_and_covered_suburbs_are_kept(self):
        for text in ["Happy hour at the English Bay location", "Buffet at the Canada Way location",
                     "Brunch at our Burnaby and Richmond locations", "At participating locations",
                     "Wings at our Downtown location", "$3 tacos every Tuesday"]:
            self.assertFalse(scrape.limited_elsewhere(deal("x", text), self.vancouver), text)
        for text in ["Wings at the Islington location", "Only at our Scarborough and Etobicoke locations",
                     "At our Brampton, Milton and Toronto locations"]:
            self.assertFalse(scrape.limited_elsewhere(deal("x", text), self.toronto), text)


class NamesOnPage(unittest.TestCase):
    def test_restaurant_name_must_appear(self):
        self.assertFalse(scrape.names_on_page("California Pizza Kitchen", "state & main edmonton happy hour"))
        self.assertTrue(scrape.names_on_page("Liberty Pizza", "liberty pizzeria, made with heart in toronto"))
        self.assertTrue(scrape.names_on_page("Café Crêpe", "cafe crepe on queen street"))
        self.assertTrue(scrape.names_on_page("Pizza Pizza", "order pizza pizza in toronto"))

    def test_overture_site_needs_place_and_name(self):
        b = [{"website_source": "overture", "name": "California Pizza Kitchen", "address": "10180 101 Street"}]
        places = scrape.place_words(cities.get("edmonton"))
        self.assertFalse(scrape.confirmed_site("State & Main, Edmonton. Happy hour 2-5", b, places))
        self.assertTrue(scrape.confirmed_site("California Pizza Kitchen Edmonton", b, places))


if __name__ == "__main__":
    unittest.main()
