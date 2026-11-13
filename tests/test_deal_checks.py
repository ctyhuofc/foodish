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


PAGE = """### PAGE: https://wingplace.ca/specials
Wing Wednesday: 59¢ wings all day, dine-in only
Happy Hour 3-6 pm daily. $5.75 pints and half-price apps
Festive menu starting November 30"""


def ai(**kw):
    base = {"title": "Wing night", "price": "59¢", "days": ["Wed"], "quote": "Wing Wednesday: 59¢ wings all day"}
    return {**base, **kw}


class CleanDeal(unittest.TestCase):
    def keep(self, d):
        return scrape.clean_deal(d, "https://wingplace.ca", PAGE)

    def test_a_deal_quoted_from_the_page_is_kept(self):
        self.assertIsNotNone(self.keep(ai()))
        self.assertIsNotNone(self.keep(ai(title="Happy hour pints", price="$5.75", days=["Mon"], quote="Happy Hour 3-6 pm daily. $5.75 pints")))

    def test_a_price_not_on_the_page_is_dropped(self):
        self.assertIsNone(self.keep(ai(price="49¢")))
        self.assertIsNone(self.keep(ai(price="$5", quote="Happy Hour 3-6 pm daily")))  # 5 is only inside 5.75

    def test_prices_without_numbers_are_not_checked(self):
        self.assertIsNotNone(self.keep(ai(price="Half-price", quote="half-price apps")))

    def test_a_quote_not_on_the_page_is_dropped(self):
        self.assertIsNone(self.keep(ai(quote="Wing Wednesday 59¢ wings with any drink purchase only")))
        self.assertIsNone(self.keep(ai(quote=None)))
        self.assertIsNone(self.keep(ai(quote="")))

    def test_quote_spacing_and_case_do_not_matter(self):
        self.assertIsNotNone(self.keep(ai(quote="wing  WEDNESDAY 59¢ wings")))

    def test_unreadable_days_drop_the_deal_instead_of_meaning_every_day(self):
        self.assertIsNone(self.keep(ai(days=["Game days"])))
        self.assertIsNone(self.keep(ai(days="whenever the Oilers play")))
        self.assertEqual(self.keep(ai(days="Mon-Fri"))["days"], [1, 5])  # a range we can't expand reads its ends
        self.assertEqual(self.keep(ai(days=[]))["days"], [])  # none given: every day, as before

    def test_start_date_is_kept_so_the_deal_waits_until_then(self):
        d = self.keep(ai(title="Festive menu", price=None, quote="Festive menu starting November 30", valid_from="2099-11-30"))
        self.assertEqual(d["valid_from"], "2099-11-30")
        self.assertIsNone(self.keep(ai(valid_from="next week"))["valid_from"])

    def test_without_page_text_only_the_basic_checks_run(self):
        self.assertIsNotNone(scrape.clean_deal(ai(quote=None, price="$99"), "https://wingplace.ca"))
